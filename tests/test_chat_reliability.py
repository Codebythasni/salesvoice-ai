"""Tests for the reliability of the chat request.

These cover the failures that made the dashboard look stuck: a request that
never finishes, a Gemini model that is unavailable, a rate limit, a rejected
key and a network problem. Every one of them has to end in a clear success
or error state, and the customer message must never be duplicated or
answered with a made-up reply.
"""

from __future__ import annotations

import httpx
import pytest

from salesvoice.gemini import (
    MAX_ATTEMPTS,
    GeminiError,
    SalesAssistant,
    build_assistant,
)
from salesvoice.config import Settings


class FakeResponse:
    def __init__(self, text: str = "TaskFlow is $39 per user.") -> None:
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


def _api_error(code, status=""):
    from google.genai import errors as genai_errors

    class _Real(genai_errors.APIError):
        def __init__(self):
            Exception.__init__(self, "api error")
            self.code = code
            self.status = status

    return _Real()


def _assistant(models: FakeModels, **kwargs) -> SalesAssistant:
    return SalesAssistant(
        api_key="fake-key",
        model="gemini-test-model",
        client=FakeClient(models),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# the client must never be able to hang
# ---------------------------------------------------------------------------


def test_the_client_is_built_with_an_http_timeout(monkeypatch):
    """Without a timeout the SDK sends the request with no timeout at all."""
    import google.genai as genai

    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return FakeClient(FakeModels(FakeResponse()))

    monkeypatch.setattr(genai, "Client", fake_client)

    assistant = SalesAssistant(api_key="fake-key", model="gemini-test-model", timeout_seconds=12)
    assistant._get_client()

    options = captured["http_options"]
    assert options.timeout == 12000  # milliseconds


def test_the_sdk_retry_loop_is_limited(monkeypatch):
    """The app owns the retry policy, so the SDK must not retry on its own."""
    import google.genai as genai

    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return FakeClient(FakeModels(FakeResponse()))

    monkeypatch.setattr(genai, "Client", fake_client)

    assistant = SalesAssistant(api_key="fake-key", model="gemini-test-model")
    assistant._get_client()

    retry_options = captured["http_options"].retry_options
    assert retry_options is not None
    assert retry_options.attempts == 1


def test_a_client_that_cannot_start_is_reported_as_not_retryable(monkeypatch):
    import google.genai as genai

    def fake_client(**kwargs):
        raise ValueError("bad configuration")

    monkeypatch.setattr(genai, "Client", fake_client)

    assistant = SalesAssistant(api_key="fake-key", model="gemini-test-model")

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert excinfo.value.code == "gemini_client_error"
    assert excinfo.value.retryable is False


def test_the_total_budget_is_bounded():
    assistant = SalesAssistant(api_key="fake-key", model="gemini-test-model", timeout_seconds=30)
    assert assistant.total_budget_seconds() == 65
    assert assistant.timeout_seconds == 30


# ---------------------------------------------------------------------------
# every failure class gets its own message
# ---------------------------------------------------------------------------


def test_a_gateway_timeout_is_not_retried_and_is_reported_as_a_timeout():
    models = FakeModels(error=_api_error(504, "DEADLINE_EXCEEDED"))
    assistant = _assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert excinfo.value.code == "gemini_timeout"
    assert excinfo.value.status_code == 504
    assert len(models.calls) == 1, "a timeout must not be retried"


def test_a_rejected_request_is_not_a_temporary_outage():
    models = FakeModels(error=_api_error(400, "INVALID_ARGUMENT"))
    assistant = _assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert excinfo.value.code == "gemini_bad_request"
    assert excinfo.value.retryable is False
    assert len(models.calls) == 1


def test_a_network_failure_has_its_own_code():
    models = FakeModels(error=httpx.ConnectError("no route to host"))
    assistant = _assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert excinfo.value.code == "gemini_network"
    assert excinfo.value.retryable is True


def test_an_unavailable_model_is_retryable_and_names_the_model():
    models = FakeModels(error=_api_error(503, "UNAVAILABLE"))
    assistant = _assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert excinfo.value.code == "gemini_unavailable"
    assert excinfo.value.retryable is True
    assert "gemini-test-model" in excinfo.value.public_message
    assert len(models.calls) == MAX_ATTEMPTS


def test_the_api_key_is_never_part_of_an_error():
    models = FakeModels(error=_api_error(401, "API_KEY_INVALID"))
    assistant = _assistant(models)

    with pytest.raises(GeminiError) as excinfo:
        assistant.generate_reply([{"role": "customer", "text": "Hi"}])

    assert "fake-key" not in excinfo.value.public_message
    assert "fake-key" not in excinfo.value.detail


def test_build_assistant_uses_the_configured_timeout():
    settings = Settings(
        gemini_api_key="fake-key",
        gemini_model="gemini-test-model",
        gemini_timeout_seconds=17.5,
    )
    assistant = build_assistant(settings)
    assert assistant is not None
    assert assistant.timeout_seconds == 17.5
    assert assistant.total_budget_seconds() == 40.0


# ---------------------------------------------------------------------------
# the endpoint always ends in a terminal state
# ---------------------------------------------------------------------------


class AssistantThatFails:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def generate_reply(self, history):
        raise self.error

    def generate_summary(self, transcript):
        raise self.error


def _start(client):
    return client.post("/api/call/start", json={}).get_json()["call"]["id"]


def _turns(client, call_id):
    return client.get(f"/api/calls/{call_id}").get_json()["call"]["turns"]


@pytest.mark.parametrize(
    "code,status,error_code,http_status,retryable",
    [
        (503, "UNAVAILABLE", "gemini_unavailable", 503, True),
        (504, "DEADLINE_EXCEEDED", "gemini_timeout", 504, True),
        (429, "RESOURCE_EXHAUSTED", "gemini_rate_limited", 429, True),
        (401, "API_KEY_INVALID", "gemini_auth", 503, False),
        (404, "NOT_FOUND", "gemini_model_not_found", 503, False),
        (400, "INVALID_ARGUMENT", "gemini_bad_request", 502, False),
    ],
)
def test_every_failure_class_reaches_the_browser(
    app_factory, code, status, error_code, http_status, retryable
):
    """The real assistant, a fake Gemini client and the real route."""
    assistant = _assistant(FakeModels(error=_api_error(code, status)))
    client = app_factory(assistant)
    call_id = _start(client)

    response = client.post(
        "/api/chat", json={"call_id": call_id, "message": "Hi", "message_id": "m1"}
    )

    assert response.status_code == http_status
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == error_code
    assert payload["retryable"] is retryable
    assert payload["error"]["message"]
    assert "detail" not in payload["error"]


def test_an_unexpected_internal_error_stays_an_internal_error(app_factory):
    """A bug in the app is not reported as a Gemini outage."""
    client = app_factory(AssistantThatFails(RuntimeError("boom")))
    call_id = _start(client)

    response = client.post("/api/chat", json={"call_id": call_id, "message": "Hi"})

    assert response.status_code == 500
    assert response.get_json()["error"]["code"] == "internal_error"
    assert "boom" not in response.get_data(as_text=True)


def test_a_failed_message_is_saved_once_and_gets_no_made_up_reply(app_factory):
    client = app_factory(AssistantThatFails(_api_error(503, "UNAVAILABLE")))
    call_id = _start(client)

    client.post("/api/chat", json={"call_id": call_id, "message": "Hi", "message_id": "m1"})

    turns = _turns(client, call_id)
    customer = [turn for turn in turns if turn["role"] == "customer"]
    assistant = [turn for turn in turns if turn["role"] == "assistant"]
    assert len(customer) == 1
    assert customer[0]["text"] == "Hi"
    # Only the greeting, no invented answer.
    assert len(assistant) == 1
    assert "Hi, thanks for taking my call" in assistant[0]["text"]


def test_a_retry_after_a_failure_adds_the_missing_reply_only(app_factory):
    class Flaky:
        def __init__(self):
            self.attempts = 0

        def generate_reply(self, history):
            self.attempts += 1
            if self.attempts == 1:
                raise GeminiError(
                    "The model is busy.", detail="503", status_code=503,
                    code="gemini_unavailable", retryable=True,
                )
            return "TaskFlow CRM costs $39 per user per month."

        def generate_summary(self, transcript):
            return "{}"

    client = app_factory(Flaky())
    call_id = _start(client)
    payload = {"call_id": call_id, "message": "What does it cost?", "message_id": "m1"}

    first = client.post("/api/chat", json=payload)
    assert first.status_code == 503

    retry = client.post("/api/chat", json=payload)
    assert retry.status_code == 200
    assert retry.get_json()["reply"] == "TaskFlow CRM costs $39 per user per month."

    turns = _turns(client, call_id)
    customer = [turn for turn in turns if turn["role"] == "customer"]
    assert len(customer) == 1, "the customer message must not be duplicated"


def test_the_config_endpoint_tells_the_dashboard_how_long_to_wait(client):
    body = client.get("/api/config").get_json()
    assert body["limits"]["gemini_timeout_seconds"] > 0
    assert body["limits"]["chat_timeout_seconds"] > body["limits"]["gemini_timeout_seconds"]
