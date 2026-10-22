"""Tests for the chat error paths and retrying a failed message."""

from __future__ import annotations

from salesvoice.gemini import GeminiError


class FlakyAssistant:
    """Fails the summary/reply a fixed number of times, then succeeds."""

    def __init__(self, failures: int = 1, reply: str = "TaskFlow CRM starts at $39.") -> None:
        self.failures = failures
        self.reply_text = reply
        self.attempts = 0

    def generate_reply(self, history):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise GeminiError(
                "The Gemini service reported that the configured model is unavailable.",
                detail="503 UNAVAILABLE",
                status_code=503,
                code="gemini_unavailable",
                retryable=True,
            )
        return self.reply_text

    def generate_summary(self, transcript):
        return "{}"


def _start(client):
    return client.post("/api/call/start", json={}).get_json()["call"]["id"]


def _turns(client, call_id):
    return client.get(f"/api/calls/{call_id}").get_json()["call"]["turns"]


def test_a_failed_message_returns_a_safe_error_with_a_retry_hint(app_factory):
    client = app_factory(FlakyAssistant(failures=99))
    call_id = _start(client)

    response = client.post(
        "/api/chat", json={"call_id": call_id, "message": "What does it cost?", "message_id": "m1"}
    )

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["retryable"] is True
    assert payload["error"]["code"] == "gemini_unavailable"
    assert "unavailable" in payload["error"]["message"].lower()
    assert "detail" not in payload["error"]


def test_the_customer_message_is_saved_once_when_the_reply_fails(app_factory):
    client = app_factory(FlakyAssistant(failures=99))
    call_id = _start(client)

    client.post(
        "/api/chat", json={"call_id": call_id, "message": "Hello", "message_id": "m1"}
    )

    turns = _turns(client, call_id)
    customer = [turn for turn in turns if turn["role"] == "customer"]
    assert len(customer) == 1
    assert customer[0]["text"] == "Hello"
    assert customer[0]["client_message_id"] == "m1"


def test_retrying_the_same_message_id_generates_the_missing_reply(app_factory):
    assistant = FlakyAssistant(failures=1)
    client = app_factory(assistant)
    call_id = _start(client)
    payload = {"call_id": call_id, "message": "What does it cost?", "message_id": "m1"}

    first = client.post("/api/chat", json=payload)
    assert first.status_code == 503

    retry = client.post("/api/chat", json=payload)
    assert retry.status_code == 200
    body = retry.get_json()
    assert body["reply"] == assistant.reply_text
    assert body["duplicate"] is False

    turns = _turns(client, call_id)
    customer = [turn for turn in turns if turn["role"] == "customer"]
    assert len(customer) == 1

    assistant_turns = [turn for turn in turns if turn["role"] == "assistant"]
    assert assistant_turns[-1]["text"] == assistant.reply_text
    assert len(assistant_turns) == 2


def test_a_duplicate_with_an_existing_reply_is_not_answered_again(app_factory):
    assistant = FlakyAssistant(failures=0)
    client = app_factory(assistant)
    call_id = _start(client)
    payload = {"call_id": call_id, "message": "Hello", "message_id": "m1"}

    first = client.post("/api/chat", json=payload)
    assert first.status_code == 200
    assert first.get_json()["duplicate"] is False

    second = client.post("/api/chat", json=payload)
    assert second.status_code == 200
    assert second.get_json()["duplicate"] is True
    assert second.get_json()["reply"] == assistant.reply_text
    assert assistant.attempts == 1
    assert len([t for t in _turns(client, call_id) if t["role"] == "customer"]) == 1


def test_retry_never_borrows_a_later_reply(app_factory):
    """A failed message must not receive the answer to a later message."""

    class SequenceAssistant:
        def __init__(self):
            self.calls = 0

        def generate_reply(self, history):
            self.calls += 1
            if self.calls == 1:
                raise GeminiError(
                    "The model is unavailable.", detail="503", status_code=503,
                    code="gemini_unavailable", retryable=True,
                )
            return "Reply number " + str(self.calls)

        def generate_summary(self, transcript):
            return "{}"

    assistant = SequenceAssistant()
    client = app_factory(assistant)
    call_id = _start(client)

    client.post("/api/chat", json={"call_id": call_id, "message": "First", "message_id": "m1"})
    later = client.post(
        "/api/chat", json={"call_id": call_id, "message": "Second", "message_id": "m2"}
    )
    assert later.get_json()["reply"] == "Reply number 2"

    retry = client.post(
        "/api/chat", json={"call_id": call_id, "message": "First", "message_id": "m1"}
    )
    assert retry.status_code == 200
    assert retry.get_json()["reply"] == "Reply number 3"
    assert retry.get_json()["reply"] != "Reply number 2"

    turns = _turns(client, call_id)
    first_customer = next(t for t in turns if t["client_message_id"] == "m1")
    index = turns.index(first_customer)
    assert turns[index + 1]["role"] == "customer"
    assert turns[-1]["role"] == "assistant"
    assert turns[-1]["text"] == "Reply number 3"


def test_auth_errors_are_reported_as_not_retryable(app_factory):
    class AuthFailAssistant:
        def generate_reply(self, history):
            raise GeminiError(
                "The assistant's server credentials were rejected.",
                detail="401 API_KEY_INVALID",
                status_code=503,
                code="gemini_auth",
                retryable=False,
            )

        def generate_summary(self, transcript):
            return "{}"

    client = app_factory(AuthFailAssistant())
    call_id = _start(client)
    response = client.post("/api/chat", json={"call_id": call_id, "message": "Hi"})
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "gemini_auth"
    assert response.get_json()["retryable"] is False


def test_timeout_is_reported_with_its_own_status(app_factory):
    class TimeoutAssistant:
        def generate_reply(self, history):
            raise GeminiError(
                "The assistant took too long to respond. Please try again.",
                detail="TimeoutError",
                status_code=504,
                code="gemini_timeout",
                retryable=True,
            )

        def generate_summary(self, transcript):
            return "{}"

    client = app_factory(TimeoutAssistant())
    call_id = _start(client)
    response = client.post("/api/chat", json={"call_id": call_id, "message": "Hi"})
    assert response.status_code == 504
    assert response.get_json()["error"]["code"] == "gemini_timeout"
    assert response.get_json()["retryable"] is True


def test_rate_limit_is_reported_with_its_own_status(app_factory):
    class RateLimitedAssistant:
        def generate_reply(self, history):
            raise GeminiError(
                "The assistant hit a usage limit.",
                detail="429 RESOURCE_EXHAUSTED",
                status_code=429,
                code="gemini_rate_limited",
                retryable=True,
            )

        def generate_summary(self, transcript):
            return "{}"

    client = app_factory(RateLimitedAssistant())
    call_id = _start(client)
    response = client.post("/api/chat", json={"call_id": call_id, "message": "Hi"})
    assert response.status_code == 429
    assert response.get_json()["error"]["code"] == "gemini_rate_limited"


def test_an_empty_reply_is_treated_as_an_error(app_factory):
    class EmptyAssistant:
        def generate_reply(self, history):
            raise GeminiError(
                "The assistant did not return an answer this time. Please try again.",
                detail="empty model response",
                status_code=502,
                code="gemini_error",
                retryable=True,
            )

        def generate_summary(self, transcript):
            return "{}"

    client = app_factory(EmptyAssistant())
    call_id = _start(client)
    response = client.post("/api/chat", json={"call_id": call_id, "message": "Hi"})
    assert response.status_code == 502
    assert response.get_json()["ok"] is False
