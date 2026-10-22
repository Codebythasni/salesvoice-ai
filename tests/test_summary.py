"""Tests for the end-of-call summary."""

from __future__ import annotations

import json

import pytest

from salesvoice.summary import (
    SummaryError,
    generate_call_summary,
    parse_summary,
)

from conftest import FailingSummaryAssistant, DEFAULT_SUMMARY


def test_summary_is_generated_when_the_call_ends(client, assistant, started_call):
    client.post("/api/chat", json={"call_id": started_call, "message": "We need a demo"})
    response = client.post("/api/call/end", json={"call_id": started_call})
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["summary_error"] is None
    assert payload["summary"]["requirements"]
    assert payload["summary"]["objections"]
    assert payload["summary"]["next_steps"]
    assert payload["summary"]["outcome"]
    assert payload["call"]["summary_available"] is True
    assert len(assistant.summary_calls) == 1


def test_summary_is_saved_with_the_call(client, started_call):
    client.post("/api/call/end", json={"call_id": started_call})
    detail = client.get(f"/api/calls/{started_call}").get_json()
    assert detail["call"]["summary"]
    assert detail["call"]["summary_error"] is None


def test_suggested_status_is_applied(client, started_call):
    client.post("/api/call/end", json={"call_id": started_call})
    detail = client.get(f"/api/calls/{started_call}").get_json()
    assert detail["call"]["sales_status"] == "Demo Requested"
    assert detail["call"]["sales_status_source"] == "suggested"


def test_manual_status_is_not_overwritten_by_the_suggestion(client, started_call):
    client.post("/api/call/status", json={"call_id": started_call, "sales_status": "Converted"})
    client.post("/api/call/end", json={"call_id": started_call})
    detail = client.get(f"/api/calls/{started_call}").get_json()
    assert detail["call"]["sales_status"] == "Converted"
    assert detail["call"]["sales_status_source"] == "manual"


def test_call_is_preserved_when_the_summary_fails(app_factory):
    client = app_factory(FailingSummaryAssistant())
    call_id = client.post("/api/call/start", json={}).get_json()["call"]["id"]
    client.post("/api/chat", json={"call_id": call_id, "message": "Hello"})

    response = client.post("/api/call/end", json={"call_id": call_id})
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["summary"] is None
    assert "temporarily unavailable" in payload["summary_error"]
    assert payload["call"]["status"] == "ended"
    assert payload["call"]["turn_count"] == 3

    detail = client.get(f"/api/calls/{call_id}").get_json()
    assert detail["call"]["summary_error"]
    assert detail["call"]["summary_available"] is False
    assert len(detail["call"]["turns"]) == 3


def test_summary_can_be_retried_after_a_failure(app_factory, settings, assistant):
    from salesvoice import create_app

    failing_client = app_factory(FailingSummaryAssistant())
    call_id = failing_client.post("/api/call/start", json={}).get_json()["call"]["id"]
    failing_client.post("/api/call/end", json={"call_id": call_id})

    working = create_app(settings=settings, assistant=assistant)
    working.config.update(TESTING=True)
    client = working.test_client()

    response = client.post("/api/call/summary", json={"call_id": call_id})
    assert response.status_code == 200
    assert response.get_json()["summary"]
    assert response.get_json()["summary_error"] is None


def test_summary_of_an_active_call_is_refused(client, started_call):
    response = client.post("/api/call/summary", json={"call_id": started_call})
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "call_active"


def test_summary_requires_a_call_id(client):
    assert client.post("/api/call/summary", json={}).status_code == 400


def test_summary_of_an_unknown_call_returns_404(client):
    assert client.post("/api/call/summary", json={"call_id": "nope"}).status_code == 404


def test_summary_without_an_assistant_keeps_the_call(app_factory):
    client = app_factory(None)
    call_id = client.post("/api/call/start", json={}).get_json()["call"]["id"]
    response = client.post("/api/call/end", json={"call_id": call_id})
    assert response.status_code == 200
    assert response.get_json()["summary"] is None
    assert response.get_json()["summary_error"]
    assert client.get(f"/api/calls/{call_id}").get_json()["call"]["turn_count"] == 1


def test_parse_summary_reads_json():
    result = parse_summary(json.dumps(DEFAULT_SUMMARY))
    assert result.requirements.startswith("The customer wants")
    assert result.suggested_status == "Demo Requested"
    assert "Customer requirements" in result.to_text()


def test_parse_summary_handles_markdown_fences():
    raw = "```json\n" + json.dumps(DEFAULT_SUMMARY) + "\n```"
    result = parse_summary(raw)
    assert result.suggested_status == "Demo Requested"


def test_parse_summary_falls_back_to_plain_text():
    result = parse_summary("The customer asked about pricing and then hung up.")
    assert result.outcome == "The customer asked about pricing and then hung up."
    assert result.suggested_status == "No Response"


def test_parse_summary_rejects_an_unknown_status():
    raw = json.dumps({"outcome": "ok", "suggested_status": "Teleported"})
    result = parse_summary(raw)
    assert result.suggested_status == "No Response"


def test_generate_call_summary_raises_without_an_assistant():
    from salesvoice.sessions import CallSession

    with pytest.raises(SummaryError):
        generate_call_summary(None, CallSession(id="x"))


def test_generate_call_summary_rejects_an_empty_transcript():
    from salesvoice.sessions import CallSession

    with pytest.raises(SummaryError):
        generate_call_summary(object(), CallSession(id="x"))
