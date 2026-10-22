"""Tests for the sales status of a call."""

from __future__ import annotations

import pytest

from salesvoice.sessions import (
    DEFAULT_SALES_STATUS,
    SALES_STATUSES,
    is_valid_sales_status,
)

EXPECTED_STATUSES = [
    "Converted",
    "Interested",
    "Follow-up Required",
    "Not Interested",
    "Demo Requested",
    "No Response",
]


def test_the_exact_set_of_statuses_is_used():
    assert list(SALES_STATUSES) == EXPECTED_STATUSES
    assert DEFAULT_SALES_STATUS == "No Response"


@pytest.mark.parametrize("status", EXPECTED_STATUSES)
def test_every_status_is_accepted(status, client, started_call):
    response = client.post(
        "/api/call/status", json={"call_id": started_call, "sales_status": status}
    )
    assert response.status_code == 200
    assert response.get_json()["call"]["sales_status"] == status
    assert response.get_json()["call"]["sales_status_source"] == "manual"


def test_an_unknown_status_is_rejected(client, started_call):
    response = client.post(
        "/api/call/status", json={"call_id": started_call, "sales_status": "Teleported"}
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_sales_status"
    assert response.get_json()["sales_statuses"] == EXPECTED_STATUSES


def test_a_status_with_a_different_case_is_rejected(client, started_call):
    response = client.post(
        "/api/call/status", json={"call_id": started_call, "sales_status": "converted"}
    )
    assert response.status_code == 400


def test_status_requires_a_call_id(client):
    response = client.post("/api/call/status", json={"sales_status": "Interested"})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_call_id"


def test_status_of_an_unknown_call_returns_404(client):
    response = client.post(
        "/api/call/status", json={"call_id": "nope", "sales_status": "Interested"}
    )
    assert response.status_code == 404


def test_the_status_can_be_changed_after_the_call_ended(client, started_call):
    client.post("/api/call/end", json={"call_id": started_call})
    response = client.post(
        "/api/call/status", json={"call_id": started_call, "sales_status": "Not Interested"}
    )
    assert response.status_code == 200
    assert response.get_json()["call"]["sales_status"] == "Not Interested"

    detail = client.get(f"/api/calls/{started_call}").get_json()
    assert detail["call"]["sales_status"] == "Not Interested"


def test_a_new_call_starts_with_the_default_status(client, started_call):
    client.post("/api/call/status", json={"call_id": started_call, "sales_status": "Converted"})
    client.post("/api/call/end", json={"call_id": started_call})
    second = client.post("/api/call/start", json={}).get_json()
    assert second["call"]["sales_status"] == DEFAULT_SALES_STATUS


def test_patch_is_accepted_as_well(client, started_call):
    response = client.patch(
        "/api/call/status", json={"call_id": started_call, "sales_status": "Interested"}
    )
    assert response.status_code == 200


def test_is_valid_sales_status_helper():
    assert is_valid_sales_status("Converted") is True
    assert is_valid_sales_status("Converted ") is False
    assert is_valid_sales_status(None) is False
    assert is_valid_sales_status(1) is False
