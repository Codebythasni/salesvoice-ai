"""Tests for the SQLite persistence layer."""

from __future__ import annotations

import sqlite3

from salesvoice import create_app
from salesvoice.database import CallDatabase
from salesvoice.sessions import ACTIVE, CONSENT_PENDING, ENDED


def test_schema_is_created_on_start(tmp_path):
    database = CallDatabase(tmp_path / "calls.db")
    tables = {
        row["name"]
        for row in database._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"calls", "call_turns", "recordings"}.issubset(tables)
    database.close()


def test_init_schema_is_safe_to_run_twice(tmp_path):
    database = CallDatabase(tmp_path / "calls.db")
    database.init_schema()
    database.init_schema()
    assert database.count_calls() == 0
    database.close()


def test_call_survives_a_new_app_instance(settings, assistant, second_client):
    first = create_app(settings=settings, assistant=assistant)
    first.config.update(TESTING=True)
    client = first.test_client()

    call_id = client.post("/api/call/start", json={}).get_json()["call"]["id"]
    client.post("/api/chat", json={"call_id": call_id, "message": "I need a demo please"})

    # A brand new app instance, same database file.
    history = second_client.get("/api/calls").get_json()
    assert history["persisted"] is True
    assert history["count"] == 1
    assert history["calls"][0]["id"] == call_id
    assert history["calls"][0]["turn_count"] == 3

    detail = second_client.get(f"/api/calls/{call_id}").get_json()
    texts = [turn["text"] for turn in detail["call"]["turns"]]
    assert "I need a demo please" in texts


def test_ended_call_survives_a_new_app_instance(settings, assistant, second_client):
    first = create_app(settings=settings, assistant=assistant)
    first.config.update(TESTING=True)
    client = first.test_client()

    call_id = client.post("/api/call/start", json={}).get_json()["call"]["id"]
    client.post("/api/call/end", json={"call_id": call_id})

    detail = second_client.get(f"/api/calls/{call_id}").get_json()
    assert detail["call"]["status"] == ENDED
    assert detail["call"]["ended_at"] is not None
    assert detail["call"]["duration_seconds"] >= 0


def test_sales_status_survives_a_new_app_instance(settings, assistant, second_client):
    first = create_app(settings=settings, assistant=assistant)
    first.config.update(TESTING=True)
    client = first.test_client()

    call_id = client.post("/api/call/start", json={}).get_json()["call"]["id"]
    client.post("/api/call/status", json={"call_id": call_id, "sales_status": "Interested"})
    client.post("/api/call/end", json={"call_id": call_id})

    detail = second_client.get(f"/api/calls/{call_id}").get_json()
    assert detail["call"]["sales_status"] == "Interested"


def test_active_call_left_open_by_a_previous_run_is_closed(settings, assistant):
    database = CallDatabase(settings.resolved_database_path)
    call = database.create_call("taskflow-crm")
    database.append_turn(call.id, "customer", "hello", source="text")
    database.close()

    application = create_app(settings=settings, assistant=assistant)
    application.config.update(TESTING=True)
    client = application.test_client()

    detail = client.get(f"/api/calls/{call.id}").get_json()
    assert detail["call"]["status"] == ENDED
    assert detail["call"]["ended_at"] is not None

    # A new call can be started because nothing is left active.
    assert client.post("/api/call/start", json={}).status_code == 200


def test_duration_is_stored_in_the_database(tmp_path):
    database = CallDatabase(tmp_path / "calls.db")
    call = database.create_call("taskflow-crm")
    database.end_call(call.id)
    row = database._connection.execute(
        "SELECT duration_seconds, status, started_at, ended_at FROM calls WHERE id = ?",
        (call.id,),
    ).fetchone()
    assert row["status"] == ENDED
    assert row["duration_seconds"] >= 0
    assert row["started_at"] and row["ended_at"]
    database.close()


def test_transcript_turns_are_stored_in_order(tmp_path):
    database = CallDatabase(tmp_path / "calls.db")
    call = database.create_call("taskflow-crm")
    database.append_turn(call.id, "assistant", "greeting", source="server")
    database.append_turn(call.id, "customer", "hello", source="voice")
    database.append_turn(call.id, "assistant", "reply", source="server")

    turns = database.turns_for(call.id)
    assert [t.role for t in turns] == ["assistant", "customer", "assistant"]
    assert [t.text for t in turns] == ["greeting", "hello", "reply"]
    database.close()


def test_unknown_call_id_is_handled_safely(tmp_path):
    database = CallDatabase(tmp_path / "calls.db")
    assert database.get_call("does-not-exist") is None
    assert database.end_call("does-not-exist") is None
    assert database.append_turn("does-not-exist", "customer", "hi") is None
    assert database.set_sales_status("does-not-exist", "Interested", "manual") is None
    database.close()


def test_sql_in_a_call_id_is_treated_as_data(tmp_path):
    database = CallDatabase(tmp_path / "calls.db")
    injection = "'; DROP TABLE calls; --"
    assert database.get_call(injection) is None
    database.create_call("taskflow-crm")
    assert database.count_calls() == 1
    database.close()


def test_sql_in_a_turn_text_is_stored_verbatim(tmp_path):
    database = CallDatabase(tmp_path / "calls.db")
    call = database.create_call("taskflow-crm")
    nasty = "Robert'); DROP TABLE calls;--"
    database.append_turn(call.id, "customer", nasty, source="text")

    turns = database.turns_for(call.id)
    assert turns[0].text == nasty
    assert database.count_calls() == 1
    database.close()


def test_health_reports_the_storage(settings, assistant, client):
    payload = client.get("/api/health").get_json()
    assert payload["storage"] == "sqlite"
    assert payload["database_path"].endswith("calls.db")
    assert payload["stored_calls"] == 0


def test_call_is_removed_when_its_row_is_deleted(tmp_path):
    database = CallDatabase(tmp_path / "calls.db")
    call = database.create_call("taskflow-crm")
    database.append_turn(call.id, "customer", "hello", source="text")
    with database._lock:
        database._connection.execute("DELETE FROM calls WHERE id = ?", (call.id,))
        database._connection.commit()
    assert database.turns_for(call.id) == []
    database.close()


def test_connection_uses_row_factory(tmp_path):
    database = CallDatabase(tmp_path / "calls.db")
    assert isinstance(
        database._connection.execute("SELECT 1 AS one").fetchone(), sqlite3.Row
    )
    database.close()
