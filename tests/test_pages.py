"""Tests for the page routes and the read-only JSON endpoints."""

from __future__ import annotations

from salesvoice.sessions import SALES_STATUSES


def test_homepage_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "SalesVoice AI" in body
    assert "TaskFlow CRM" in body


def test_dashboard_renders_with_controls(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="start-call"' in body
    assert 'id="end-call"' in body
    assert 'id="call-timer"' in body
    assert 'id="transcript"' in body
    assert 'id="message-input"' in body


def test_dashboard_offers_every_sales_status(client):
    body = client.get("/dashboard").get_data(as_text=True)
    for status in SALES_STATUSES:
        assert status in body


def test_dashboard_shows_the_recording_scope_notice(client):
    body = client.get("/dashboard").get_data(as_text=True)
    assert "customer microphone" in body.lower()


def test_dashboard_has_a_saved_call_detail_dialog(client):
    body = client.get("/dashboard").get_data(as_text=True)
    assert 'id="call-detail-modal"' in body
    assert 'id="call-detail-transcript"' in body
    assert 'id="call-detail-recordings"' in body
    assert 'id="call-detail-retry"' in body
    assert 'id="call-detail-player"' in body


def test_dashboard_starts_with_an_honest_recording_note(client):
    body = client.get("/dashboard").get_data(as_text=True)
    assert "No recording has been made for this call yet." in body


def test_pages_never_expose_the_api_key(client, settings):
    for path in ("/", "/dashboard"):
        body = client.get(path).get_data(as_text=True)
        assert settings.gemini_api_key not in body
        assert "GEMINI_API_KEY=" not in body


def test_health_endpoint(client):
    payload = client.get("/api/health").get_json()
    assert payload["ok"] is True
    assert payload["assistant_ready"] is True
    assert payload["model"] == "gemini-test-model"


def test_config_endpoint_exposes_product_and_feature_flags(client):
    payload = client.get("/api/config").get_json()
    assert payload["ok"] is True
    assert payload["product"]["name"] == "TaskFlow CRM"
    assert payload["features"]["recording"] is True
    assert payload["features"]["recording_consent_flow"] is True
    assert payload["features"]["call_summary"] is True
    assert payload["features"]["sales_status_classification"] is True
    assert payload["features"]["call_history_persistence"] is True
    assert payload["features"]["voice_input"] is True


def test_config_is_honest_about_the_recording_scope(client):
    payload = client.get("/api/config").get_json()
    assert payload["features"]["recording_two_sided"] is False
    assert "customer microphone" in payload["features"]["recording_scope_notice"].lower()


def test_config_lists_the_sales_statuses(client):
    payload = client.get("/api/config").get_json()
    assert payload["sales_statuses"] == list(SALES_STATUSES)
    assert payload["default_sales_status"] == "No Response"


def test_unknown_page_returns_404(client):
    assert client.get("/definitely-not-a-page").status_code == 404


def test_api_unknown_route_returns_json_error(client):
    response = client.get("/api/not-a-real-endpoint")
    assert response.status_code == 404
    assert response.get_json()["ok"] is False
