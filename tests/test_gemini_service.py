"""Tests for the Gemini service wrapper. The SDK client is always a fake."""

from __future__ import annotations

import pytest

from salesvoice.gemini import (
    GeminiError,
    GeminiNotConfiguredError,
    SalesAssistant,
    build_assistant,
)
from salesvoice.config import Settings


class FakeResponse:
    def __init__(self, text: str = "Hello from TaskFlow CRM.") -> None:
        self.text = text


class FakeModels:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def generate_content(self, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, models: FakeModels) -> None:
        self.models = models


class FakeAPIError(Exception):
    def __init__(self, code=None, status=""):
        super().__init__("api error")
        self.code = code
        self.status = status


def make_assistant(models: FakeModels) -> SalesAssistant:
    return SalesAssistant(
        api_key="fake-key",
        model="gemini-test-model",
        client=FakeClient(models),
    )


def test_generate_reply_returns_text():
    models = FakeModels(FakeResponse("TaskFlow is $39 per user."))
    assistant = make_assistant(models)

    reply = assistant.generate_reply([{"role": "customer", "text": "What does it cost?"}])

    assert reply == "TaskFlow is $39 per user."
    assert models.calls[0]["model"] == "gemini-test-model"
    contents = models.calls[0]["contents"]
    assert contents[-1] == {"role": "user", "parts": [{"text": "What does it cost?"}]}


def test_generate_reply_sends_the_system_instruction():
    models = FakeModels(FakeResponse("Sure."))
    assistant = make_assistant(models)

    assistant.generate_reply([{"role": "customer", "text": "Hello"}])

    config = models.calls[0]["config"]
    assert "TaskFlow CRM" in config.system_instruction


def test_missing_key_raises_not_configured():
    with pytest.raises(GeminiNotConfiguredError):
        SalesAssistant(api_key="", model="gemini-test-model")


def test_empty_history_is_rejected():
    models = FakeModels(FakeResponse("unused"))
    assistant = make_assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([])

    assert excinfo.value.status_code == 400
    assert not models.calls


def test_empty_model_response_is_reported():
    models = FakeModels(FakeResponse("   "))
    assistant = make_assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert excinfo.value.status_code == 502


def test_rate_limit_is_translated_to_a_friendly_error():
    error = _api_error(429, "RESOURCE_EXHAUSTED")
    models = FakeModels(error=error)
    assistant = make_assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert excinfo.value.status_code == 429
    assert "usage limit" in excinfo.value.public_message
    assert "RESOURCE_EXHAUSTED" not in excinfo.value.public_message
    assert excinfo.value.code == "gemini_rate_limited"
    assert excinfo.value.retryable is True


def test_invalid_key_is_translated_without_leaking_the_key():
    models = FakeModels(error=_api_error(401, "API_KEY_INVALID"))
    assistant = make_assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "gemini_auth"
    assert excinfo.value.retryable is False
    assert "fake-key" not in excinfo.value.public_message
    assert "fake-key" not in excinfo.value.detail


def test_unknown_model_is_translated():
    models = FakeModels(error=_api_error(404, "NOT_FOUND"))
    assistant = make_assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert "GEMINI_MODEL" in excinfo.value.public_message
    assert excinfo.value.code == "gemini_model_not_found"
    assert excinfo.value.retryable is False


def test_timeout_is_reported_separately_and_not_retried():
    models = FakeModels(error=TimeoutError("slow"))
    assistant = make_assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert excinfo.value.status_code == 504
    assert excinfo.value.code == "gemini_timeout"
    assert "too long" in excinfo.value.public_message
    assert len(models.calls) == 1


def test_unavailable_mentions_the_model_and_is_retryable():
    models = FakeModels(error=_api_error(503, "UNAVAILABLE"))
    assistant = make_assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "gemini_unavailable"
    assert excinfo.value.retryable is True
    assert "gemini-test-model" in excinfo.value.public_message
    assert "unavailable" in excinfo.value.public_message.lower()


def test_unexpected_exception_becomes_a_generic_error():
    models = FakeModels(error=RuntimeError("network down"))
    assistant = make_assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert excinfo.value.status_code == 502
    assert "network down" not in excinfo.value.public_message


def test_transient_unavailable_is_retried():
    class FlakyModels(FakeModels):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def generate_content(self, model, contents, config=None):
            self.attempts += 1
            if self.attempts < 3:
                raise _api_error(503, "UNAVAILABLE")
            return FakeResponse("Recovered.")

    models = FlakyModels()
    assistant = make_assistant(models)

    reply = assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert reply == "Recovered."
    assert models.attempts == 3


def test_transient_failure_gives_up_after_the_attempt_budget():
    models = FakeModels(error=_api_error(503, "UNAVAILABLE"))
    assistant = make_assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert excinfo.value.status_code == 503
    assert len(models.calls) == 3


def test_non_transient_failure_is_not_retried():
    models = FakeModels(error=_api_error(400, "INVALID_ARGUMENT"))
    assistant = make_assistant(models)

    with pytest.raises(GeminiError):
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert len(models.calls) == 1


def test_generate_summary_returns_the_raw_output():
    models = FakeModels(FakeResponse('{"outcome": "good"}'))
    assistant = make_assistant(models)

    result = assistant.generate_summary("customer: hello\nassistant: hi")

    assert result == '{"outcome": "good"}'
    contents = models.calls[0]["contents"]
    assert contents[0]["role"] == "user"
    assert "customer: hello" in contents[0]["parts"][0]["text"]


def test_generate_summary_uses_the_summary_instruction():
    models = FakeModels(FakeResponse("{}"))
    assistant = make_assistant(models)

    assistant.generate_summary("customer: hello")

    config = models.calls[0]["config"]
    assert "suggested_status" in config.system_instruction
    assert "Converted" in config.system_instruction


def test_generate_summary_rejects_an_empty_transcript():
    models = FakeModels(FakeResponse("{}"))
    assistant = make_assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_summary("   ")

    assert excinfo.value.status_code == 400
    assert not models.calls


def test_generate_summary_surfaces_api_errors():
    models = FakeModels(error=_api_error(503, "UNAVAILABLE"))
    assistant = make_assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_summary("customer: hello")

    assert excinfo.value.status_code == 503


def test_build_assistant_returns_none_when_not_configured():
    settings = Settings(gemini_api_key="", gemini_model="")
    assert build_assistant(settings) is None


def test_build_assistant_returns_an_assistant_when_configured():
    settings = Settings(gemini_api_key="fake-key", gemini_model="gemini-test-model")
    assistant = build_assistant(settings)
    assert assistant is not None
    assert assistant.model == "gemini-test-model"


def _api_error(code, status):
    try:
        from google.genai import errors as genai_errors

        class _Real(genai_errors.APIError):
            def __init__(self):
                Exception.__init__(self, "api error")
                self.code = code
                self.status = status

        return _Real()
    except Exception:
        return FakeAPIError(code=code, status=status)
