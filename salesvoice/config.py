"""Application configuration loaded from environment variables.

Secrets are read from the local ``.env`` file and never leave the server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PLACEHOLDER_VALUES = {"", "your_api_key_here", "your_model_name_here"}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "instance" / "salesvoice.db"
DEFAULT_RECORDINGS_DIR = PROJECT_ROOT / "recordings"


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().strip('"').strip("'")


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the application."""

    gemini_api_key: str = ""
    gemini_model: str = ""
    host: str = "127.0.0.1"
    port: int = 5000
    debug: bool = False
    stage: str = "2"
    gemini_timeout_seconds: float = 30.0
    max_history_messages: int = 24
    database_path: str = ""
    recordings_dir: str = ""
    recording_max_bytes: int = 25 * 1024 * 1024
    extra: dict = field(default_factory=dict)

    @property
    def gemini_configured(self) -> bool:
        """True when a usable API key and model name are present."""
        return bool(
            self.gemini_api_key
            and self.gemini_model
            and self.gemini_api_key not in PLACEHOLDER_VALUES
            and self.gemini_model not in PLACEHOLDER_VALUES
        )

    @property
    def model_name(self) -> str:
        """Safe-to-display model identifier (no key material)."""
        return self.gemini_model or "not configured"

    @property
    def resolved_database_path(self) -> str:
        return self.database_path or str(DEFAULT_DATABASE_PATH)

    @property
    def resolved_recordings_dir(self) -> str:
        return self.recordings_dir or str(DEFAULT_RECORDINGS_DIR)


def load_settings(env_file: str | os.PathLike[str] | None = None) -> Settings:
    """Load settings from ``.env`` plus the real process environment.

    Real environment variables take precedence over ``.env`` values so that a
    deployment can override the local file.
    """
    if env_file is None:
        load_dotenv()
    else:
        load_dotenv(dotenv_path=env_file, override=False)

    try:
        port = int(os.getenv("PORT", "5000"))
    except ValueError:
        port = 5000

    host = _clean(os.getenv("HOST")) or ""
    if not host:
        # Render and most PaaS platforms require binding to 0.0.0.0
        if os.getenv("PORT") or os.getenv("RENDER") or os.getenv("RENDER_SERVICE_NAME"):
            host = "0.0.0.0"
        else:
            host = "127.0.0.1"

    try:
        timeout = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "30"))
    except ValueError:
        timeout = 30.0

    try:
        max_history = int(os.getenv("MAX_HISTORY_MESSAGES", "24"))
    except ValueError:
        max_history = 24

    try:
        recording_max_bytes = int(os.getenv("RECORDING_MAX_BYTES", str(25 * 1024 * 1024)))
    except ValueError:
        recording_max_bytes = 25 * 1024 * 1024

    return Settings(
        gemini_api_key=_clean(os.getenv("GEMINI_API_KEY")),
        gemini_model=_clean(os.getenv("GEMINI_MODEL")),
        host=host,
        port=port,
        debug=_clean(os.getenv("FLASK_DEBUG")).lower() in {"1", "true", "yes", "on"},
        stage=_clean(os.getenv("APP_STAGE")) or "2",
        gemini_timeout_seconds=timeout,
        max_history_messages=max(2, max_history),
        database_path=_clean(os.getenv("DATABASE_PATH")),
        recordings_dir=_clean(os.getenv("RECORDINGS_DIR")),
        recording_max_bytes=max(1024, recording_max_bytes),
    )
