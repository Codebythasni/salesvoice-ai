"""Tests for the call lifecycle and the chat endpoint (Gemini mocked)."""

from __future__ import annotations

from salesvoice.gemini import GeminiError

from conftest import FailingAssistant, StubAssistant


def test_start_call_creates_a_session_and_greeting(client):
    response = client.post("/api/call/start", json={})
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["call"]["status"] == "active"
    assert payload["call"]["persisted"] is True
    assert payload["call"]["recording_available"] is False
    assert payload["call"]["sales_status"] == "No Response"
    assert "TaskFlow CRM" in payload["greeting"]
    assert payload["call"]["id"]


def test_second_active_call_is_refused(client, started_call):
    response = client.post("/api/call/start", json={})
    assert response.status_code == 409

    payload = response.get_json()
    assert payload["error"]["code"] == "call_already_active"
    assert payload["call"]["id"] == started_call


def test_a_new_call_can_start_once_the_previous_one_ended(client, started_call):
    client.post("/api/call/end", json={"call_id": started_call})
    response = client.post("/api/call/start", json={})
    assert response.status_code == 200
    assert response.get_json()["call"]["id"] != started_call


def test_chat_returns_the_assistant_reply(client, assistant, started_call):
    response = client.post(
        "/api/chat",
        json={"call_id": started_call, "message": "How much does it cost?", "source": "voice"},
    )
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["reply"] == assistant.reply
    assert payload["duplicate"] is False

    history = assistant.last_history
    assert history[-1] == {"role": "customer", "text": "How much does it cost?"}
    assert history[0]["role"] == "assistant"


def test_chat_records_both_turns_in_the_session(client, started_call):
    client.post("/api/chat", json={"call_id": started_call, "message": "Do you support SSO?"})
    payload = client.get("/api/calls").get_json()
    call = next(c for c in payload["calls"] if c["id"] == started_call)
    assert call["turn_count"] == 3
    assert call["persisted"] is True
    assert call["recording_available"] is False


def test_chat_without_a_call_id_opens_a_call(client, assistant):
    response = client.post("/api/chat", json={"message": "Hello there"})
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["call_created"] is True
    assert payload["call"]["status"] == "active"
    assert "TaskFlow CRM" in payload["greeting"]
    assert payload["reply"] == assistant.reply

    stored = client.get("/api/calls").get_json()
    assert stored["count"] == 1


def test_chat_without_a_call_id_joins_the_active_call(client, started_call):
    response = client.post("/api/chat", json={"message": "Hello there"})
    assert response.status_code == 200
    assert response.get_json()["call_created"] is False
    assert response.get_json()["call"]["id"] == started_call


def test_repeated_message_id_is_not_answered_twice(client, assistant, started_call):
    payload = {"call_id": started_call, "message": "Tell me about pricing", "message_id": "abc-123"}

    first = client.post("/api/chat", json=payload)
    second = client.post("/api/chat", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()["duplicate"] is True
    assert second.get_json()["reply"] == first.get_json()["reply"]
    assert len(assistant.calls) == 1


def test_different_message_ids_are_answered_separately(client, assistant, started_call):
    client.post("/api/chat", json={"call_id": started_call, "message": "Hi", "message_id": "a"})
    client.post("/api/chat", json={"call_id": started_call, "message": "Hi", "message_id": "b"})
    assert len(assistant.calls) == 2


def test_chat_rejects_an_empty_message(client, started_call):
    response = client.post("/api/chat", json={"call_id": started_call, "message": "   "})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "empty_message"


def test_chat_rejects_a_non_string_message(client, started_call):
    response = client.post("/api/chat", json={"call_id": started_call, "message": {"a": 1}})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "empty_message"


def test_chat_with_an_unknown_call_id_returns_404(client):
    response = client.post("/api/chat", json={"call_id": "does-not-exist", "message": "Hi"})
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "call_not_found"


def test_end_call_closes_the_session(client, started_call):
    response = client.post("/api/call/end", json={"call_id": started_call})
    assert response.status_code == 200

    call = response.get_json()["call"]
    assert call["status"] == "ended"
    assert call["ended_at"] is not None
    assert call["duration_seconds"] >= 0


def test_end_call_requires_a_call_id(client):
    response = client.post("/api/call/end", json={})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_call_id"


def test_end_call_with_unknown_id_returns_404(client):
    response = client.post("/api/call/end", json={"call_id": "nope"})
    assert response.status_code == 404


def test_chat_after_ending_a_call_is_rejected(client, started_call):
    client.post("/api/call/end", json={"call_id": started_call})
    response = client.post("/api/chat", json={"call_id": started_call, "message": "One more thing"})
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "call_ended"


def test_oversized_message_is_truncated_not_rejected(client, assistant, started_call):
    response = client.post(
        "/api/chat",
        json={"call_id": started_call, "message": "x" * 9000},
    )
    assert response.status_code == 200
    assert len(assistant.last_history[-1]["text"]) == 4000


def test_product_name_mis_hearing_is_normalised(client, assistant, started_call):
    response = client.post(
        "/api/chat",
        json={"call_id": started_call, "message": "how much is task flow crm"},
    )
    assert response.status_code == 200
    assert response.get_json()["normalized"] is True
    assert assistant.last_history[-1]["text"] == "how much is TaskFlow CRM"


def test_assistant_failure_returns_a_safe_error(app_factory):
    failing = FailingAssistant(
        GeminiError("The assistant is busy right now.", detail="429", status_code=503)
    )
    client = app_factory(failing)
    call_id = client.post("/api/call/start", json={}).get_json()["call"]["id"]

    response = client.post("/api/chat", json={"call_id": call_id, "message": "Hello"})
    assert response.status_code == 503

    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["error"]["message"] == "The assistant is busy right now."
    assert "429" not in payload["error"]["message"]


def test_the_customer_turn_is_kept_when_the_reply_fails(app_factory):
    """A failed reply must not lose what the customer actually said."""
    failing = FailingAssistant(GeminiError("Service unavailable.", detail="x", status_code=502))
    client = app_factory(failing)
    call_id = client.post("/api/call/start", json={}).get_json()["call"]["id"]

    client.post("/api/chat", json={"call_id": call_id, "message": "Hello"})

    call = next(
        c for c in client.get("/api/calls").get_json()["calls"] if c["id"] == call_id
    )
    assert call["turn_count"] == 2

    detail = client.get(f"/api/calls/{call_id}").get_json()
    assert detail["call"]["turns"][-1]["text"] == "Hello"


def test_unexpected_assistant_exception_is_handled(app_factory):
    failing = FailingAssistant(RuntimeError("boom"))
    client = app_factory(failing)
    call_id = client.post("/api/call/start", json={}).get_json()["call"]["id"]

    response = client.post("/api/chat", json={"call_id": call_id, "message": "Hello"})
    assert response.status_code == 500
    assert response.get_json()["error"]["code"] == "internal_error"
    assert "boom" not in response.get_data(as_text=True)


def test_assistant_not_configured_returns_503(app_factory):
    client = app_factory(None)

    response = client.post("/api/chat", json={"message": "Hello"})
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "assistant_not_configured"


def test_calls_endpoint_lists_sessions(client, started_call):
    payload = client.get("/api/calls").get_json()
    assert payload["persisted"] is True
    assert payload["count"] == 1
    assert payload["calls"][0]["id"] == started_call


def test_call_detail_returns_the_transcript(client, started_call):
    client.post("/api/chat", json={"call_id": started_call, "message": "Hello there"})
    payload = client.get(f"/api/calls/{started_call}").get_json()
    assert payload["ok"] is True
    roles = [turn["role"] for turn in payload["call"]["turns"]]
    assert roles == ["assistant", "customer", "assistant"]


def test_call_detail_for_unknown_call_returns_404(client):
    assert client.get("/api/calls/nope").status_code == 404


def test_stub_assistant_receives_history_without_secrets(settings):
    stub = StubAssistant(reply="Sure.")
    stub.generate_reply([{"role": "customer", "text": "Hi"}])
    assert stub.last_history == [{"role": "customer", "text": "Hi"}]
