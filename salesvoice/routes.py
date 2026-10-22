"""HTTP routes for SalesVoice AI.

Pages
    ``/``            landing page
    ``/dashboard``   the live sales dashboard

JSON API
    ``GET    /api/health``                  server status
    ``GET    /api/config``                  product + capability flags
    ``POST   /api/call/start``              open a call session, get the greeting
    ``POST   /api/call/end``                close a call session and summarise it
    ``POST   /api/call/summary``            (re)generate the summary of a call
    ``POST   /api/call/status``             set the sales status of a call
    ``POST   /api/call/consent``            record the recording consent decision
    ``POST   /api/call/recording``          store a consented recording
    ``GET    /api/call/<id>/recordings``    list the recordings of a call
    ``GET    /api/recording/<id>``          stream a recording for playback
    ``DELETE /api/recording/<id>``          delete a stored recording
    ``POST   /api/chat``                    send a customer utterance, get the reply
    ``GET    /api/calls``                   persisted call history
    ``GET    /api/calls/<id>``              one persisted call
    ``GET    /api/call/active``             the call that is still open, if any
"""

from __future__ import annotations

import logging
import mimetypes
import traceback
from typing import Any

from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

from .database import CallDatabase
from .gemini import GeminiError, redact_secret
from .prompts import build_greeting
from .product import get_product
from .recording import (
    RECORDING_SCOPE_NOTICE,
    TWO_SIDED_RECORDING_SUPPORTED,
    RecordingError,
    RecordingStore,
)
from .sessions import (
    ACTIVE,
    CONSENT_DENIED,
    CONSENT_GRANTED,
    CONSENT_PENDING,
    CONSENT_STATES,
    DEFAULT_SALES_STATUS,
    SALES_STATUSES,
    CallSession,
    is_valid_sales_status,
)
from .summary import (
    SummaryError,
    apply_suggested_status,
    generate_call_summary,
    summary_text_to_dict,
)
from .text import normalize_utterance, was_normalized

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 4000
MAX_CLIENT_MESSAGE_ID_CHARS = 80

bp = Blueprint("main", __name__)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _database() -> CallDatabase:
    return current_app.extensions["salesvoice"]["database"]


def _assistant():
    return current_app.extensions["salesvoice"]["assistant"]


def _recordings() -> RecordingStore:
    return current_app.extensions["salesvoice"]["recordings"]


def _wants_json() -> bool:
    return request.path.startswith("/api/")


def _error(message: str, status: int, code: str = "error", **extra):
    payload = {"ok": False, "error": {"code": code, "message": message}}
    payload.update(extra)
    return jsonify(payload), status


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return {}
    return data


def _clean_message(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[:MAX_MESSAGE_CHARS]
    return text


def _clean_client_message_id(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    return value[:MAX_CLIENT_MESSAGE_ID_CHARS]


def _source(data: dict) -> str:
    source = data.get("source")
    return source if isinstance(source, str) and source in {"voice", "text"} else "text"


def _chat_timeout_seconds(settings: Any) -> float:
    """How long the dashboard should wait for a reply before it gives up.

    The server always answers first (see
    :meth:`salesvoice.gemini.SalesAssistant.total_budget_seconds`), so the
    browser only aborts a request that is genuinely stuck.
    """
    timeout = float(getattr(settings, "gemini_timeout_seconds", 30.0) or 30.0)
    return round(timeout * 2 + 20, 1)


def _is_gemini_failure(exc: Exception) -> bool:
    """True for failures that came from Gemini or from the network.

    Anything else is a bug in the application itself and keeps the generic
    internal-error answer.
    """
    if isinstance(exc, (GeminiError, TimeoutError, ConnectionError)):
        return True
    try:
        from google.genai import errors as genai_errors

        if isinstance(exc, genai_errors.APIError):
            return True
    except Exception:  # pragma: no cover - the SDK is a hard dependency
        pass
    try:
        import httpx

        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
            return True
    except Exception:  # pragma: no cover - httpx always installed in practice
        pass
    return False


def _classify_chat_error(exc: Exception) -> GeminiError:
    """Give a Gemini failure a specific, user-safe message and code.

    The assistant normally raises a :class:`GeminiError` already, but an
    error raised outside the retry loop (or by a different assistant) still
    has to reach the browser as a classified failure rather than a generic
    500.
    """
    assistant = _assistant()
    translator = getattr(assistant, "translate_error", None)
    if callable(translator):
        try:
            classified = translator(exc)
        except Exception:  # pragma: no cover - defensive
            classified = None
        if isinstance(classified, GeminiError):
            return classified
    return GeminiError(
        "The assistant could not complete the request. Please try again.",
        detail=type(exc).__name__,
        status_code=502,
        code="gemini_error",
        retryable=True,
    )


def _safe_traceback(exc: Exception) -> str:
    """Format a traceback for the log without ever printing the API key."""
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    settings = current_app.extensions["salesvoice"].get("settings")
    return redact_secret(text, getattr(settings, "gemini_api_key", None))


def _recording_view(row: dict) -> dict:
    """Describe a stored recording together with the state of the real file.

    ``playable`` is only true when the file exists on disk and its bytes look
    like the audio format it claims to be. A row in the database is never
    enough on its own.
    """
    info = _recordings().inspect(row["call_id"], row["filename"])
    playable = bool(info["available"] and info["valid_audio"])
    return {
        "id": row["id"],
        "call_id": row["call_id"],
        "filename": row["filename"],
        "mime_type": row["mime_type"],
        "size_bytes": info["size_bytes"],
        "duration_seconds": row["duration_seconds"],
        "created_at": row["created_at"],
        "available": bool(info["available"]),
        "valid_audio": bool(info["valid_audio"]),
        "playable": playable,
        "problem": info["problem"],
        "playback_url": f"/api/recording/{row['id']}" if playable else None,
    }


def _recording_views(call_id: str) -> list[dict]:
    return [_recording_view(row) for row in _database().list_recordings(call_id)]


def _call_payload(session: CallSession) -> dict:
    """Everything the dashboard needs to show one saved call.

    The transcript, the summary, the sales status and the recordings all come
    from the same call id, so a reloaded page can rebuild the panel from the
    database without guessing which call it belongs to.
    """
    payload = session.to_dict()
    recordings = _recording_views(session.id)
    playable = [item for item in recordings if item["playable"]]
    payload["recordings"] = recordings
    payload["playable_recording_count"] = len(playable)
    payload["recording_available"] = bool(playable)
    payload["recording_scope_notice"] = RECORDING_SCOPE_NOTICE
    payload["recording_two_sided"] = False
    payload["summary_fields"] = summary_text_to_dict(session.summary)
    return payload


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------


@bp.get("/")
def index():
    product = get_product()
    settings = current_app.extensions["salesvoice"]["settings"]
    return render_template(
        "index.html",
        product=product,
        stage=settings.stage,
        assistant_ready=current_app.extensions["salesvoice"]["assistant"] is not None,
        model_name=settings.model_name,
    )


@bp.get("/dashboard")
def dashboard():
    product = get_product()
    settings = current_app.extensions["salesvoice"]["settings"]
    return render_template(
        "dashboard.html",
        product=product,
        stage=settings.stage,
        assistant_ready=current_app.extensions["salesvoice"]["assistant"] is not None,
        model_name=settings.model_name,
        sales_statuses=SALES_STATUSES,
        default_sales_status=DEFAULT_SALES_STATUS,
        recording_scope_notice=RECORDING_SCOPE_NOTICE,
    )


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@bp.get("/api/health")
def health():
    settings = current_app.extensions["salesvoice"]["settings"]
    database = _database()
    return jsonify(
        {
            "ok": True,
            "app": "SalesVoice AI",
            "stage": settings.stage,
            "assistant_ready": current_app.extensions["salesvoice"]["assistant"] is not None,
            "model": settings.model_name,
            "storage": "sqlite",
            "database_path": settings.resolved_database_path,
            "stored_calls": database.count_calls(),
        }
    )


@bp.get("/api/config")
def config():
    product = get_product()
    settings = current_app.extensions["salesvoice"]["settings"]
    return jsonify(
        {
            "ok": True,
            "stage": settings.stage,
            "product": product.to_dict(),
            "assistant_ready": current_app.extensions["salesvoice"]["assistant"] is not None,
            "model": settings.model_name,
            "features": {
                "voice_input": True,
                "voice_output": True,
                "recording": True,
                "recording_consent_flow": True,
                "recording_two_sided": TWO_SIDED_RECORDING_SUPPORTED,
                "recording_scope_notice": RECORDING_SCOPE_NOTICE,
                "call_summary": True,
                "sales_status_classification": True,
                "call_history_persistence": True,
            },
            "sales_statuses": list(SALES_STATUSES),
            "default_sales_status": DEFAULT_SALES_STATUS,
            "limits": {
                "max_message_chars": MAX_MESSAGE_CHARS,
                "recording_max_bytes": settings.recording_max_bytes,
                "gemini_timeout_seconds": settings.gemini_timeout_seconds,
                "chat_timeout_seconds": _chat_timeout_seconds(settings),
            },
        }
    )


@bp.post("/api/call/start")
def call_start():
    """Open a call. Only one call can be active at a time."""
    product = get_product()
    database = _database()

    existing = database.get_active_call()
    if existing is not None:
        return _error(
            "A call is already in progress. End it before starting a new one.",
            409,
            "call_already_active",
            call=existing.to_dict(include_turns=False),
        )

    session = database.create_call(product.key)
    greeting = build_greeting(product)
    database.append_turn(session.id, "assistant", greeting, source="server")

    return jsonify(
        {
            "ok": True,
            "call": session.to_dict(),
            "greeting": greeting,
            "assistant_ready": current_app.extensions["salesvoice"]["assistant"] is not None,
        }
    )


@bp.post("/api/call/end")
def call_end():
    data = _json_body()
    call_id = data.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return _error("A call_id is required to end a call.", 400, "invalid_call_id")

    database = _database()
    session = database.get_call(call_id)
    if session is None:
        return _error("That call was not found.", 404, "call_not_found")

    session = database.end_call(call_id) or session

    summary_payload, summary_error = _generate_and_store_summary(session)
    session = database.get_call(call_id) or session

    return jsonify(
        {
            "ok": True,
            "call": session.to_dict(),
            "summary": summary_payload,
            "summary_error": summary_error,
        }
    )


@bp.post("/api/call/summary")
def call_summary():
    """Generate (or regenerate) the summary of a finished call."""
    data = _json_body()
    call_id = data.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return _error("A call_id is required to generate a summary.", 400, "invalid_call_id")

    database = _database()
    session = database.get_call(call_id)
    if session is None:
        return _error("That call was not found.", 404, "call_not_found")

    if session.status == ACTIVE:
        return _error(
            "End the call before generating its summary.", 409, "call_active"
        )

    summary_payload, summary_error = _generate_and_store_summary(session)
    session = database.get_call(call_id) or session

    return jsonify(
        {
            "ok": True,
            "call": session.to_dict(),
            "summary": summary_payload,
            "summary_error": summary_error,
        }
    )


@bp.route("/api/call/status", methods=["POST", "PATCH"])
def call_status():
    """Set the sales status of a call."""
    data = _json_body()
    call_id = data.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return _error("A call_id is required to set the sales status.", 400, "invalid_call_id")

    status = data.get("sales_status")
    if not is_valid_sales_status(status):
        return _error(
            "That is not a valid sales status. Choose one of: " + ", ".join(SALES_STATUSES) + ".",
            400,
            "invalid_sales_status",
            sales_statuses=list(SALES_STATUSES),
        )

    database = _database()
    session = database.get_call(call_id)
    if session is None:
        return _error("That call was not found.", 404, "call_not_found")

    session = database.set_sales_status(call_id, status, "manual")
    return jsonify({"ok": True, "call": session.to_dict(include_turns=False)})


@bp.post("/api/call/consent")
def call_consent():
    """Record the customer's decision about call recording."""
    data = _json_body()
    call_id = data.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return _error("A call_id is required to record consent.", 400, "invalid_call_id")

    decision = data.get("decision")
    if decision not in (CONSENT_GRANTED, CONSENT_DENIED):
        return _error(
            "The consent decision must be 'granted' or 'denied'.", 400, "invalid_consent"
        )

    database = _database()
    session = database.get_call(call_id)
    if session is None:
        return _error("That call was not found.", 404, "call_not_found")

    session = database.set_recording_consent(call_id, decision)
    return jsonify(
        {
            "ok": True,
            "call": session.to_dict(include_turns=False),
            "recording_scope_notice": RECORDING_SCOPE_NOTICE,
        }
    )


@bp.post("/api/call/recording")
def upload_recording():
    """Store a recording. Refused unless consent was explicitly granted."""
    call_id = request.form.get("call_id") or _json_body().get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return _error("A call_id is required to store a recording.", 400, "invalid_call_id")

    database = _database()
    session = database.get_call(call_id)
    if session is None:
        return _error("That call was not found.", 404, "call_not_found")

    if session.recording_consent != CONSENT_GRANTED:
        return _error(
            "Recording consent was not granted, so nothing was recorded.",
            403,
            "recording_consent_required",
            recording_consent=session.recording_consent,
        )

    upload = request.files.get("recording")
    if upload is None or not upload.filename:
        return _error("No recording file was uploaded.", 400, "missing_recording")

    duration_raw = request.form.get("duration_seconds")
    try:
        duration = float(duration_raw) if duration_raw else None
    except (TypeError, ValueError):
        duration = None

    try:
        saved = _recordings().save(
            call_id,
            upload.read(),
            upload.filename or "recording.webm",
            upload.mimetype or None,
        )
    except RecordingError as exc:
        return _error(exc.public_message, 400, "recording_rejected", detail=exc.detail)

    recording_id = database.add_recording(
        call_id=call_id,
        filename=saved.filename,
        mime_type=saved.mime_type,
        size_bytes=saved.size_bytes,
        duration_seconds=duration,
        consent=session.recording_consent,
    )

    row = database.get_recording(recording_id)
    recording = _recording_view(row) if row is not None else None

    return jsonify(
        {
            "ok": True,
            "recording": recording,
            "call": database.get_call(call_id).to_dict(include_turns=False),  # type: ignore[union-attr]
            "recording_scope_notice": RECORDING_SCOPE_NOTICE,
        }
    )


@bp.get("/api/call/<call_id>/recordings")
def list_recordings(call_id: str):
    database = _database()
    session = database.get_call(call_id)
    if session is None:
        return _error("That call was not found.", 404, "call_not_found")
    recordings = _recording_views(call_id)
    return jsonify(
        {
            "ok": True,
            "recording_scope_notice": RECORDING_SCOPE_NOTICE,
            "recordings": recordings,
        }
    )


@bp.get("/api/recording/<int:recording_id>")
def stream_recording(recording_id: int):
    database = _database()
    row = database.get_recording(recording_id)
    if row is None:
        return _error("That recording was not found.", 404, "recording_not_found")

    info = _recordings().inspect(row["call_id"], row["filename"])
    if not info["available"]:
        return _error(
            "The recording file is no longer on this computer.", 404, "recording_missing"
        )
    if not info["valid_audio"]:
        return _error(
            info["problem"] or "The recording file is not valid audio.",
            422,
            "recording_invalid",
        )

    path = _recordings().path_for(row["call_id"], row["filename"])
    if path is None:  # pragma: no cover - inspect() already proved it exists
        return _error("The recording file is no longer on this computer.", 404, "recording_missing")

    mime = row["mime_type"] or mimetypes.guess_type(row["filename"])[0] or "application/octet-stream"
    response = send_file(
        path,
        mimetype=mime,
        as_attachment=False,
        download_name=row["filename"],
        conditional=True,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Recording-Scope"] = "customer-microphone-only"
    return response


@bp.delete("/api/recording/<int:recording_id>")
def delete_recording(recording_id: int):
    database = _database()
    row = database.get_recording(recording_id)
    if row is None:
        return _error("That recording was not found.", 404, "recording_not_found")

    _recordings().delete(row["call_id"], row["filename"])
    database.delete_recording(recording_id)
    return jsonify({"ok": True, "recording_id": recording_id})


@bp.post("/api/chat")
def chat():
    """Send a customer utterance and get the assistant's reply."""
    data = _json_body()
    message = _clean_message(data.get("message"))
    if not message:
        return _error(
            "Please enter a message before sending.", 400, "empty_message", retryable=False
        )

    original_message = message
    message = normalize_utterance(message)

    assistant = _assistant()
    if assistant is None:
        return _error(
            "The assistant is not configured on the server. "
            "Set GEMINI_API_KEY and GEMINI_MODEL in the .env file and restart the app.",
            503,
            "assistant_not_configured",
            retryable=False,
        )

    database = _database()
    product = get_product()
    call_id = data.get("call_id")
    client_message_id = _clean_client_message_id(data.get("message_id"))
    greeting = None
    created = False
    already_saved = False

    if isinstance(call_id, str) and call_id:
        session = database.get_call(call_id)
        if session is None:
            return _error(
                "That call was not found.", 404, "call_not_found", retryable=False
            )
        if session.status != ACTIVE:
            return _error(
                "This call has already ended. Start a new call to continue.",
                409,
                "call_ended",
                retryable=False,
            )
    else:
        session = database.get_active_call()
        if session is None:
            session = database.create_call(product.key)
            greeting = build_greeting(product)
            database.append_turn(session.id, "assistant", greeting, source="server")
            created = True

    if client_message_id:
        existing = database.find_turn_by_client_message_id(session.id, client_message_id)
        if existing is not None:
            cached = database.assistant_reply_for(session.id, client_message_id)
            if cached:
                return jsonify(
                    {
                        "ok": True,
                        "reply": cached,
                        "duplicate": True,
                        "retryable": False,
                        "call": database.get_call(session.id).to_dict(include_turns=False),  # type: ignore[union-attr]
                    }
                )
            # The message was saved by an earlier attempt but that attempt
            # failed before an answer existed. Generate the answer now without
            # appending the customer message a second time.
            logger.info("Retrying a saved message that has no assistant reply yet")
            already_saved = True

    source = _source(data)
    if not already_saved:
        database.append_turn(
            session.id, "customer", message, source=source, client_message_id=client_message_id
        )
    session = database.get_call(session.id) or session
    history = session.history()

    try:
        reply = assistant.generate_reply(history)
    except GeminiError as exc:
        logger.warning("Gemini error while handling chat: %s", exc.detail or exc.public_message)
        return _error(
            exc.public_message,
            exc.status_code,
            exc.code,
            retryable=exc.retryable,
        )
    except Exception as exc:
        if _is_gemini_failure(exc):
            failure = _classify_chat_error(exc)
            logger.warning("Chat failed: %s", failure.detail or type(exc).__name__)
            return _error(
                failure.public_message,
                failure.status_code,
                failure.code,
                retryable=failure.retryable,
            )
        logger.error("Unexpected error while generating a reply: %s", _safe_traceback(exc))
        return _error(
            "Something went wrong while generating a reply. Please try again.",
            500,
            "internal_error",
            retryable=True,
        )

    database.append_turn(session.id, "assistant", reply, source="server")

    return jsonify(
        {
            "ok": True,
            "reply": reply,
            "duplicate": False,
            "normalized": was_normalized(original_message, message),
            "message": message,
            "greeting": greeting,
            "call_created": created,
            "call": database.get_call(session.id).to_dict(include_turns=False),  # type: ignore[union-attr]
        }
    )


@bp.get("/api/calls")
def calls():
    """The persisted call history."""
    database = _database()
    limit = request.args.get("limit", 50)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    sessions = database.list_calls(limit)
    active = database.get_active_call()
    return jsonify(
        {
            "ok": True,
            "persisted": True,
            "count": len(sessions),
            "active_call_id": active.id if active is not None else None,
            "calls": [s.to_dict(include_turns=False) for s in sessions],
        }
    )


@bp.get("/api/call/active")
def active_call():
    """The call that is still open on the server, if there is one.

    A page reload loses the browser side of a call but not the server side.
    The dashboard asks this endpoint to find out whether a call is still open
    so it can show that call instead of opening a second one.
    """
    database = _database()
    session = database.get_active_call()
    if session is None:
        return jsonify({"ok": True, "active": False, "call": None})
    return jsonify({"ok": True, "active": True, "call": _call_payload(session)})


@bp.get("/api/calls/<call_id>")
def call_detail(call_id: str):
    database = _database()
    session = database.get_call(call_id)
    if session is None:
        return _error("That call was not found.", 404, "call_not_found")
    return jsonify({"ok": True, "call": _call_payload(session)})


# ---------------------------------------------------------------------------
# summary helper
# ---------------------------------------------------------------------------


def _generate_and_store_summary(session: CallSession):
    """Generate the summary and persist it. Never loses the call."""
    database = _database()
    try:
        result = generate_call_summary(_assistant(), session)
    except SummaryError as exc:
        logger.warning("Summary generation failed: %s", exc.detail or exc.public_message)
        database.set_summary_error(session.id, exc.public_message)
        return None, exc.public_message

    database.set_summary(session.id, result.to_text())
    apply_suggested_status(database, session, result)
    return result.to_dict(), None


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


def register_error_handlers(app) -> None:
    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        if _wants_json():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": {
                            "code": exc.name.lower().replace(" ", "_"),
                            "message": exc.description,
                        },
                    }
                ),
                exc.code or 500,
            )
        return exc

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        logger.exception("Unhandled error: %s", type(exc).__name__)
        if _wants_json():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": {
                            "code": "internal_error",
                            "message": "Something went wrong on the server. Please try again.",
                        },
                    }
                ),
                500,
            )
        return (
            "<h1>Something went wrong</h1><p>Please try again.</p>",
            500,
        )
