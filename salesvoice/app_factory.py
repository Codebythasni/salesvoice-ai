"""Flask application factory."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask

from .config import Settings, load_settings
from .database import CallDatabase
from .gemini import build_assistant
from .product import get_product
from .recording import RecordingStore
from .routes import bp, register_error_handlers

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_UNSET = object()


def create_app(settings: Settings | None = None, assistant=_UNSET, database=None) -> Flask:
    """Create and configure the Flask application.

    ``assistant`` can be injected (used by the tests to avoid calling Gemini).
    Passing ``None`` explicitly disables the assistant on purpose.
    ``database`` can be injected to point the app at a test database.
    """
    settings = settings or load_settings()

    app = Flask(
        __name__,
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
        instance_path=str(PROJECT_ROOT / "instance"),
    )
    secret_key = os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
    if secret_key and secret_key.strip():
        app.secret_key = secret_key.strip()
    else:
        app.secret_key = os.urandom(24)
    app.config.update(
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=max(1_000_000, settings.recording_max_bytes + 1_000_000),
        TEMPLATES_AUTO_RELOAD=settings.debug,
    )

    if assistant is _UNSET and settings.gemini_configured:
        assistant = build_assistant(settings, get_product())
    elif assistant is _UNSET:
        assistant = None

    if database is None:
        database = CallDatabase(settings.resolved_database_path)
    stale = database.end_stale_active_calls()
    if stale:
        app.logger.info("Closed %s call(s) left open by a previous run.", stale)

    recordings = RecordingStore(settings.resolved_recordings_dir, settings.recording_max_bytes)

    app.extensions["salesvoice"] = {
        "settings": settings,
        "assistant": assistant,
        "database": database,
        "recordings": recordings,
        "product": get_product(),
    }

    app.register_blueprint(bp)
    register_error_handlers(app)

    if not app.debug:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    if not settings.gemini_configured:
        app.logger.warning(
            "GEMINI_API_KEY / GEMINI_MODEL are not configured. The dashboard will run "
            "but the assistant cannot answer. Check the .env file on the server."
        )

    return app
