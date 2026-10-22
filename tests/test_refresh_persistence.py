"""Tests for what a page reload has to bring back.

A reload loses the browser side of a call but not the server side, so the
dashboard has to be able to ask the server what happened and rebuild the
panel from the database - without opening a second call or storing a message
twice.
"""

from __future__ import annotations

import io

from conftest import VALID_WEBM

from salesvoice.sessions import ACTIVE, ENDED
from salesvoice.summary import summary_text_to_dict


def _active(client) -> dict:
    response = client.get("/api/call/active")
    assert response.status_code == 200
    return response.get_json()


def test_the_active_call_endpoint_reports_the_open_call(client, started_call):
    client.post("/api/chat", json={"call_id": started_call, "message": "What does it cost?"})

    payload = _active(client)
    assert payload["ok"] is True
    assert payload["active"] is True
    assert payload["call"]["id"] == started_call
    assert payload["call"]["status"] == ACTIVE
    assert [turn["role"] for turn in payload["call"]["turns"]] == [
        "assistant",
        "customer",
        "assistant",
    ]
    assert payload["call"]["turns"][1]["text"] == "What does it cost?"


def test_the_active_call_endpoint_is_empty_when_no_call_is_open(client):
    payload = _active(client)
    assert payload["ok"] is True
    assert payload["active"] is False
    assert payload["call"] is None


def test_the_active_call_endpoint_is_empty_after_the_call_ended(client, started_call):
    client.post("/api/call/end", json={"call_id": started_call})
    assert _active(client)["call"] is None


def test_the_call_history_reports_which_call_is_open(client, started_call):
    payload = client.get("/api/calls").get_json()
    assert payload["active_call_id"] == started_call


def test_a_reload_does_not_open_a_second_call(client, started_call):
    """The dashboard asks the server what is open instead of starting a call."""
    for _ in range(3):
        payload = _active(client)
        assert payload["call"]["id"] == started_call

    # The server still refuses a second call, so no duplicate can be stored.
    second = client.post("/api/call/start", json={})
    assert second.status_code == 409
    assert second.get_json()["error"]["code"] == "call_already_active"

    calls = client.get("/api/calls").get_json()
    assert calls["count"] == 1
    assert calls["calls"][0]["id"] == started_call


def test_a_reload_does_not_store_the_message_twice(client, started_call):
    """The same message id is answered once, whatever the browser does."""
    payload = {
        "call_id": started_call,
        "message": "Tell me about pricing",
        "message_id": "reload-1",
    }
    first = client.post("/api/chat", json=payload)
    second = client.post("/api/chat", json=payload)

    assert first.get_json()["duplicate"] is False
    assert second.get_json()["duplicate"] is True
    assert second.get_json()["reply"] == first.get_json()["reply"]

    detail = client.get(f"/api/calls/{started_call}").get_json()["call"]
    customer_turns = [turn for turn in detail["turns"] if turn["role"] == "customer"]
    assert len(customer_turns) == 1
    assert customer_turns[0]["client_message_id"] == "reload-1"


def test_a_saved_message_without_a_reply_is_not_stored_again(app_factory):
    """A message that was saved before the reload keeps its place."""
    from salesvoice.gemini import GeminiError

    class FlakyAssistant:
        """Fails the first reply and works after that."""

        def __init__(self) -> None:
            self.failed = False
            self.calls = 0

        def generate_reply(self, history):
            self.calls += 1
            if not self.failed:
                self.failed = True
                raise GeminiError("Service unavailable.", detail="503", status_code=503)
            return "TaskFlow CRM starts at $39 per user."

        def generate_summary(self, transcript):
            return "{}"

    client = app_factory(FlakyAssistant())
    call_id = client.post("/api/call/start", json={}).get_json()["call"]["id"]
    payload = {
        "call_id": call_id,
        "message": "We need a demo",
        "message_id": "reload-2",
    }

    # The reply fails, but what the customer said is saved.
    first = client.post("/api/chat", json=payload)
    assert first.status_code == 503

    detail = client.get(f"/api/calls/{call_id}").get_json()["call"]
    assert [turn["role"] for turn in detail["turns"]] == ["assistant", "customer"]

    # After the reload the same message id is used again.
    retry = client.post("/api/chat", json=payload)
    assert retry.status_code == 200
    assert retry.get_json()["duplicate"] is False
    assert retry.get_json()["reply"] == "TaskFlow CRM starts at $39 per user."

    detail = client.get(f"/api/calls/{call_id}").get_json()["call"]
    customer_turns = [turn for turn in detail["turns"] if turn["role"] == "customer"]
    assert len(customer_turns) == 1
    assert detail["turns"][-1]["role"] == "assistant"


def test_the_restored_call_keeps_its_own_transcript_summary_status_and_recording(client):
    """Every part of a call stays attached to the call it belongs to."""
    first = client.post("/api/call/start", json={}).get_json()["call"]["id"]
    client.post("/api/chat", json={"call_id": first, "message": "First call question"})
    client.post("/api/call/status", json={"call_id": first, "sales_status": "Interested"})
    client.post("/api/call/consent", json={"call_id": first, "decision": "granted"})
    client.post(
        "/api/call/recording",
        data={"call_id": first, "recording": (io.BytesIO(VALID_WEBM), "recording.webm")},
        content_type="multipart/form-data",
    )
    client.post("/api/call/end", json={"call_id": first})

    second = client.post("/api/call/start", json={}).get_json()["call"]["id"]
    client.post("/api/chat", json={"call_id": second, "message": "Second call question"})
    client.post("/api/call/end", json={"call_id": second})

    first_detail = client.get(f"/api/calls/{first}").get_json()["call"]
    second_detail = client.get(f"/api/calls/{second}").get_json()["call"]

    assert first_detail["sales_status"] == "Interested"
    assert first_detail["recording_consent"] == "granted"
    assert first_detail["recording_count"] == 1
    assert first_detail["recordings"][0]["call_id"] == first
    assert first_detail["recordings"][0]["playback_url"].startswith("/api/recording/")
    assert "First call question" in [turn["text"] for turn in first_detail["turns"]]
    assert "Second call question" not in [turn["text"] for turn in first_detail["turns"]]

    assert second_detail["sales_status_source"] == "suggested"
    assert second_detail["sales_status"] != "Interested"
    assert second_detail["recording_consent"] == "pending"
    assert second_detail["recording_count"] == 0
    assert second_detail["recordings"] == []
    assert "Second call question" in [turn["text"] for turn in second_detail["turns"]]

    # The recording still plays back from its own call.
    playback = client.get(first_detail["recordings"][0]["playback_url"])
    assert playback.status_code == 200
    assert playback.get_data() == VALID_WEBM


def test_the_saved_summary_is_readable_as_fields(client, started_call):
    client.post("/api/chat", json={"call_id": started_call, "message": "We want a demo"})
    client.post("/api/call/end", json={"call_id": started_call})

    detail = client.get(f"/api/calls/{started_call}").get_json()["call"]
    assert detail["summary"]
    assert detail["summary_fields"]["requirements"]
    assert detail["summary_fields"]["outcome"]


def test_the_active_call_carries_its_recordings(client, started_call):
    client.post("/api/call/consent", json={"call_id": started_call, "decision": "granted"})
    client.post(
        "/api/call/recording",
        data={"call_id": started_call, "recording": (io.BytesIO(VALID_WEBM), "recording.webm")},
        content_type="multipart/form-data",
    )

    payload = _active(client)
    assert payload["call"]["recording_count"] == 1
    assert payload["call"]["recordings"][0]["playable"] is True
    assert payload["call"]["recordings"][0]["playback_url"]


def test_an_ended_call_is_closed_when_the_server_restarts(settings, assistant):
    """A call cannot survive a restart, and the dashboard is told so."""
    from salesvoice import create_app

    application = create_app(settings=settings, assistant=assistant)
    application.config.update(TESTING=True)
    client = application.test_client()
    call_id = client.post("/api/call/start", json={}).get_json()["call"]["id"]

    restarted = create_app(settings=settings, assistant=assistant)
    restarted.config.update(TESTING=True)
    new_client = restarted.test_client()

    payload = _active(new_client)
    assert payload["call"] is None

    detail = new_client.get(f"/api/calls/{call_id}").get_json()["call"]
    assert detail["status"] == ENDED
    assert detail["turns"], "the transcript of the interrupted call is kept"


def test_the_existing_database_is_not_touched_when_the_app_starts(settings, assistant):
    """Starting the app again keeps every stored call exactly as it was."""
    from salesvoice import create_app

    application = create_app(settings=settings, assistant=assistant)
    application.config.update(TESTING=True)
    client = application.test_client()
    call_id = client.post("/api/call/start", json={}).get_json()["call"]["id"]
    client.post("/api/chat", json={"call_id": call_id, "message": "Keep me"})
    client.post("/api/call/status", json={"call_id": call_id, "sales_status": "Converted"})
    client.post("/api/call/end", json={"call_id": call_id})

    before = client.get(f"/api/calls/{call_id}").get_json()["call"]

    restarted = create_app(settings=settings, assistant=assistant)
    restarted.config.update(TESTING=True)
    new_client = restarted.test_client()

    after = new_client.get(f"/api/calls/{call_id}").get_json()["call"]
    assert after["turns"] == before["turns"]
    assert after["summary"] == before["summary"]
    assert after["sales_status"] == "Converted"
    assert after["started_at"] == before["started_at"]
    assert new_client.get("/api/calls").get_json()["count"] == 1


def test_summary_text_is_read_back_into_its_fields():
    text = (
        "Customer requirements: A shared pipeline for 20 people\n"
        "Objections raised: The price\n"
        "Next steps: Send a comparison sheet\n"
        "Outcome: A demo was booked"
    )
    fields = summary_text_to_dict(text)
    assert fields["requirements"] == "A shared pipeline for 20 people"
    assert fields["objections"] == "The price"
    assert fields["next_steps"] == "Send a comparison sheet"
    assert fields["outcome"] == "A demo was booked"


def test_summary_text_that_is_not_in_the_known_shape_is_not_invented():
    assert summary_text_to_dict("") == {}
    assert summary_text_to_dict(None) == {}
    assert summary_text_to_dict("   ") == {}
    assert summary_text_to_dict("something else entirely")["outcome"] == (
        "something else entirely"
    )
