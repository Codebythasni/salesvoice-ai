"""Shared pytest fixtures.

The Gemini client is always replaced by a stub so the test suite never calls
the real API and never needs a real key. Every test gets its own temporary
SQLite database and recordings folder.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from salesvoice import create_app  # noqa: E402
from salesvoice.config import Settings  # noqa: E402

DEFAULT_SUMMARY = {
    "requirements": "The customer wants a shared pipeline for a 20 person sales team.",
    "objections": "They worried about the price and about migrating from spreadsheets.",
    "next_steps": "Send a comparison sheet and book a 15 minute demo.",
    "outcome": "The customer agreed to a demo next week.",
    "suggested_status": "Demo Requested",
}

# A minimal payload with a real WebM/EBML header, used wherever a test needs
# bytes that the recorder can recognise as playable audio.
VALID_WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 200


class StubAssistant:
    """Minimal stand-in for :class:`salesvoice.gemini.SalesAssistant`."""

    def __init__(self, reply: str = "TaskFlow costs $39 per user per month.", summary=None) -> None:
        self.reply = reply
        self.summary = summary if summary is not None else json.dumps(DEFAULT_SUMMARY)
        self.calls: list[list[dict]] = []
        self.summary_calls: list[str] = []

    def generate_reply(self, history):
        self.calls.append(list(history))
        return self.reply

    def generate_summary(self, transcript):
        self.summary_calls.append(transcript)
        return self.summary

    @property
    def last_history(self) -> list[dict]:
        return self.calls[-1] if self.calls else []


class FailingAssistant:
    """Assistant that always raises, used to test the error path."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def generate_reply(self, history):
        raise self.error

    def generate_summary(self, transcript):
        raise self.error


class FailingSummaryAssistant(StubAssistant):
    """Replies work, but the summary always fails."""

    def __init__(self, message: str = "The Gemini service is temporarily unavailable.") -> None:
        super().__init__()
        self.message = message

    def generate_summary(self, transcript):
        from salesvoice.gemini import GeminiError

        self.summary_calls.append(transcript)
        raise GeminiError(self.message, detail="503", status_code=503)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        gemini_api_key="test-key-not-real",
        gemini_model="gemini-test-model",
        host="127.0.0.1",
        port=5000,
        debug=False,
        stage="2",
        database_path=str(tmp_path / "calls.db"),
        recordings_dir=str(tmp_path / "recordings"),
    )


@pytest.fixture
def assistant() -> StubAssistant:
    return StubAssistant()


@pytest.fixture
def app(settings, assistant):
    application = create_app(settings=settings, assistant=assistant)
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def started_call(client) -> str:
    response = client.post("/api/call/start", json={})
    assert response.status_code == 200
    return response.get_json()["call"]["id"]


@pytest.fixture
def app_factory(settings):
    """Build an app with a custom assistant and return its test client."""

    def _factory(assistant):
        application = create_app(settings=settings, assistant=assistant)
        application.config.update(TESTING=True)
        return application.test_client()

    return _factory


@pytest.fixture
def second_client(settings, assistant):
    """A second app instance pointing at the same database file."""
    application = create_app(settings=settings, assistant=assistant)
    application.config.update(TESTING=True)
    return application.test_client()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for name in ("GEMINI_API_KEY", "GEMINI_MODEL"):
        if name in os.environ:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    monkeypatch.setattr("salesvoice.gemini.RETRY_BACKOFF_SECONDS", (0, 0))
    monkeypatch.setattr("salesvoice.gemini.MAX_ATTEMPTS", 3)
