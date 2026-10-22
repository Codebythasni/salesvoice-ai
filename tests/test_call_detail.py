"""Tests for opening a saved call and for honest recording playback."""

from __future__ import annotations

import io
from pathlib import Path

from conftest import VALID_WEBM


def _grant(client, call_id):
    response = client.post(
        "/api/call/consent", json={"call_id": call_id, "decision": "granted"}
    )
    assert response.status_code == 200


def _upload(client, call_id, data: bytes = VALID_WEBM):
    return client.post(
        "/api/call/recording",
        data={"call_id": call_id, "recording": (io.BytesIO(data), "recording.webm")},
        content_type="multipart/form-data",
    )


def _detail(client, call_id):
    response = client.get(f"/api/calls/{call_id}")
    assert response.status_code == 200
    return response.get_json()["call"]


def test_open_returns_the_saved_transcript_and_details(client, started_call):
    client.post("/api/chat", json={"call_id": started_call, "message": "Tell me about pricing"})
    client.post("/api/call/status", json={"call_id": started_call, "sales_status": "Interested"})
    client.post("/api/call/end", json={"call_id": started_call})

    call = _detail(client, started_call)
    assert call["status"] == "ended"
    assert call["sales_status"] == "Interested"
    assert call["duration_seconds"] >= 0
    assert call["turn_count"] == 3
    roles = [turn["role"] for turn in call["turns"]]
    assert roles.count("assistant") == 2
    assert roles.count("customer") == 1
    assert "Tell me about pricing" in call["turns"][1]["text"]
    assert call["summary"]
    assert call["persisted"] is True


def test_opening_a_call_does_not_change_it(client, started_call):
    client.post("/api/chat", json={"call_id": started_call, "message": "Hello"})
    client.post("/api/call/end", json={"call_id": started_call})
    before = _detail(client, started_call)

    for _ in range(3):
        _detail(client, started_call)

    after = _detail(client, started_call)
    assert after["turns"] == before["turns"]
    assert after["summary"] == before["summary"]
    assert after["sales_status"] == before["sales_status"]
    assert after["ended_at"] == before["ended_at"]


def test_a_call_with_no_recording_is_reported_honestly(client, started_call):
    client.post("/api/call/end", json={"call_id": started_call})
    call = _detail(client, started_call)
    assert call["recording_consent"] == "pending"
    assert call["recording_count"] == 0
    assert call["playable_recording_count"] == 0
    assert call["recording_available"] is False
    assert call["recordings"] == []
    assert call["recording_two_sided"] is False


def test_a_valid_recording_is_playable(client, started_call, app):
    _grant(client, started_call)
    upload = _upload(client, started_call)
    assert upload.status_code == 200
    recording = upload.get_json()["recording"]
    assert recording["playable"] is True
    assert recording["valid_audio"] is True
    assert recording["size_bytes"] == len(VALID_WEBM)

    call = _detail(client, started_call)
    assert call["recording_available"] is True
    assert call["playable_recording_count"] == 1
    assert call["recordings"][0]["playback_url"] == f"/api/recording/{recording['id']}"

    playback = client.get(recording["playback_url"])
    assert playback.status_code == 200
    assert playback.get_data() == VALID_WEBM
    assert playback.headers["X-Recording-Scope"] == "customer-microphone-only"


def test_a_missing_recording_file_is_not_playable(client, started_call, settings):
    _grant(client, started_call)
    recording = _upload(client, started_call).get_json()["recording"]

    root = Path(settings.resolved_recordings_dir)
    for path in root.rglob("*.webm"):
        path.unlink()

    call = _detail(client, started_call)
    assert call["recording_count"] == 1
    assert call["playable_recording_count"] == 0
    assert call["recording_available"] is False
    entry = call["recordings"][0]
    assert entry["playable"] is False
    assert entry["available"] is False
    assert entry["playback_url"] is None
    assert entry["problem"]

    failure = client.get(f"/api/recording/{recording['id']}")
    assert failure.status_code == 404
    assert failure.get_json()["error"]["code"] == "recording_missing"


def test_an_empty_recording_file_is_not_playable(client, started_call, settings):
    _grant(client, started_call)
    recording = _upload(client, started_call).get_json()["recording"]

    for path in Path(settings.resolved_recordings_dir).rglob("*.webm"):
        path.write_bytes(b"")

    call = _detail(client, started_call)
    assert call["recording_available"] is False
    assert call["recordings"][0]["playable"] is False

    failure = client.get(f"/api/recording/{recording['id']}")
    assert failure.status_code == 422
    assert failure.get_json()["error"]["code"] == "recording_invalid"


def test_a_corrupt_recording_file_is_not_playable(client, started_call, settings):
    _grant(client, started_call)
    recording = _upload(client, started_call).get_json()["recording"]

    for path in Path(settings.resolved_recordings_dir).rglob("*.webm"):
        path.write_bytes(b"not audio data" * 20)

    call = _detail(client, started_call)
    assert call["recording_available"] is False
    assert call["recordings"][0]["valid_audio"] is False
    assert client.get(f"/api/recording/{recording['id']}").status_code == 422


def test_a_recording_row_without_a_file_is_still_listed_but_not_playable(app, client, started_call):
    database = app.extensions["salesvoice"]["database"]
    database.add_recording(
        call_id=started_call,
        filename="never-written.webm",
        mime_type="audio/webm",
        size_bytes=999,
        duration_seconds=3.0,
        consent="granted",
    )
    call = _detail(client, started_call)
    assert call["recording_count"] == 1
    assert call["playable_recording_count"] == 0
    assert call["recording_available"] is False
    assert call["recordings"][0]["playable"] is False


def test_recording_playback_uses_the_recording_id_not_a_guess(client, started_call):
    _grant(client, started_call)
    first = _upload(client, started_call).get_json()["recording"]
    second = _upload(client, started_call).get_json()["recording"]
    assert first["id"] != second["id"]

    call = _detail(client, started_call)
    urls = [item["playback_url"] for item in call["recordings"]]
    assert urls == [f"/api/recording/{first['id']}", f"/api/recording/{second['id']}"]
    assert all(item["call_id"] == started_call for item in call["recordings"])


def test_retry_summary_after_a_failure_preserves_the_call(app_factory, settings, assistant):
    from salesvoice import create_app

    failing_client = app_factory(FailingSummaryAssistantForRetry())
    call_id = failing_client.post("/api/call/start", json={}).get_json()["call"]["id"]
    failing_client.post("/api/chat", json={"call_id": call_id, "message": "We want a demo"})
    failing_client.post("/api/call/status", json={"call_id": call_id, "sales_status": "Interested"})
    ended = failing_client.post("/api/call/end", json={"call_id": call_id}).get_json()
    assert ended["summary"] is None
    assert ended["summary_error"]
    turns_before = ended["call"]["turns"]

    working = create_app(settings=settings, assistant=assistant)
    working.config.update(TESTING=True)
    client = working.test_client()

    retry = client.post("/api/call/summary", json={"call_id": call_id})
    assert retry.status_code == 200
    payload = retry.get_json()
    assert payload["summary_error"] is None
    assert payload["summary"]

    call = _detail(client, call_id)
    assert call["turns"] == turns_before
    assert call["sales_status"] == "Interested"
    assert call["sales_status_source"] == "manual"
    assert call["summary"]


def test_retry_summary_while_gemini_is_unavailable_keeps_everything(app_factory):
    client = app_factory(FailingSummaryAssistantForRetry())
    call_id = client.post("/api/call/start", json={}).get_json()["call"]["id"]
    client.post("/api/chat", json={"call_id": call_id, "message": "Hello"})
    client.post("/api/call/status", json={"call_id": call_id, "sales_status": "Follow-up Required"})
    client.post("/api/call/end", json={"call_id": call_id})
    before = _detail(client, call_id)

    retry = client.post("/api/call/summary", json={"call_id": call_id})
    assert retry.status_code == 200
    payload = retry.get_json()
    assert payload["summary"] is None
    assert "temporarily unavailable" in payload["summary_error"]

    after = _detail(client, call_id)
    assert after["turns"] == before["turns"]
    assert after["sales_status"] == "Follow-up Required"
    assert after["status"] == "ended"


def test_retry_on_an_active_call_is_refused(client, started_call):
    response = client.post("/api/call/summary", json={"call_id": started_call})
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "call_active"
    assert _detail(client, started_call)["status"] == "active"


class FailingSummaryAssistantForRetry:
    """Replies work but the summary raises a 503, like the real service did."""

    def __init__(self):
        from salesvoice.gemini import GeminiError

        self.error = GeminiError(
            "The Gemini service is temporarily unavailable. Please try again.",
            detail="503 UNAVAILABLE",
            status_code=503,
        )

    def generate_reply(self, history):
        return "TaskFlow CRM starts at $39 per user per month."

    def generate_summary(self, transcript):
        raise self.error
