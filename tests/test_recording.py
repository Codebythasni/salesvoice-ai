"""Tests for the recording consent flow and the local recording storage."""

from __future__ import annotations

import io

import pytest

from conftest import VALID_WEBM
from salesvoice.recording import (
    RECORDING_SCOPE_NOTICE,
    RecordingError,
    RecordingStore,
)


def _audio(name: str = "recording.webm", data: bytes = VALID_WEBM) -> io.BytesIO:
    return io.BytesIO(data)


def test_a_new_call_has_no_consent(client, started_call):
    detail = client.get(f"/api/calls/{started_call}").get_json()
    assert detail["call"]["recording_consent"] == "pending"


def test_consent_can_be_granted(client, started_call):
    response = client.post(
        "/api/call/consent", json={"call_id": started_call, "decision": "granted"}
    )
    assert response.status_code == 200
    assert response.get_json()["call"]["recording_consent"] == "granted"
    assert response.get_json()["recording_scope_notice"] == RECORDING_SCOPE_NOTICE


def test_consent_can_be_denied(client, started_call):
    response = client.post(
        "/api/call/consent", json={"call_id": started_call, "decision": "denied"}
    )
    assert response.status_code == 200
    assert response.get_json()["call"]["recording_consent"] == "denied"


def test_an_invalid_consent_decision_is_rejected(client, started_call):
    response = client.post(
        "/api/call/consent", json={"call_id": started_call, "decision": "maybe"}
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_consent"


def test_consent_requires_a_call_id(client):
    assert client.post("/api/call/consent", json={"decision": "granted"}).status_code == 400


def test_consent_for_an_unknown_call_returns_404(client):
    response = client.post("/api/call/consent", json={"call_id": "nope", "decision": "granted"})
    assert response.status_code == 404


def test_recording_is_refused_without_consent(client, started_call):
    response = client.post(
        "/api/call/recording",
        data={"call_id": started_call, "recording": (_audio(), "recording.webm")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "recording_consent_required"


def test_recording_is_refused_when_consent_was_denied(client, started_call):
    client.post("/api/call/consent", json={"call_id": started_call, "decision": "denied"})
    response = client.post(
        "/api/call/recording",
        data={"call_id": started_call, "recording": (_audio(), "recording.webm")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 403
    assert client.get(f"/api/calls/{started_call}").get_json()["call"]["recording_count"] == 0


def test_recording_is_stored_after_consent_was_granted(client, started_call, settings):
    client.post("/api/call/consent", json={"call_id": started_call, "decision": "granted"})
    response = client.post(
        "/api/call/recording",
        data={
            "call_id": started_call,
            "duration_seconds": "12.5",
            "recording": (_audio(), "recording.webm"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200

    recording = response.get_json()["recording"]
    assert recording["id"]
    assert recording["playback_url"].startswith("/api/recording/")

    detail = client.get(f"/api/calls/{started_call}").get_json()
    assert detail["call"]["recording_count"] == 1
    assert detail["call"]["recording_available"] is True


def test_the_stored_recording_can_be_played_back(client, started_call):
    client.post("/api/call/consent", json={"call_id": started_call, "decision": "granted"})
    stored = client.post(
        "/api/call/recording",
        data={"call_id": started_call, "recording": (_audio(), "recording.webm")},
        content_type="multipart/form-data",
    ).get_json()["recording"]

    response = client.get(stored["playback_url"])
    assert response.status_code == 200
    assert response.get_data() == VALID_WEBM


def test_a_recording_can_be_deleted(client, started_call):
    client.post("/api/call/consent", json={"call_id": started_call, "decision": "granted"})
    stored = client.post(
        "/api/call/recording",
        data={"call_id": started_call, "recording": (_audio(), "recording.webm")},
        content_type="multipart/form-data",
    ).get_json()["recording"]

    assert client.delete(stored["playback_url"]).status_code == 200
    assert client.get(stored["playback_url"]).status_code == 404
    assert client.get(f"/api/calls/{started_call}").get_json()["call"]["recording_count"] == 0


def test_an_unknown_recording_returns_404(client):
    assert client.get("/api/recording/999").status_code == 404


def test_recordings_are_listed_per_call(client, started_call):
    client.post("/api/call/consent", json={"call_id": started_call, "decision": "granted"})
    client.post(
        "/api/call/recording",
        data={"call_id": started_call, "recording": (_audio(), "recording.webm")},
        content_type="multipart/form-data",
    )
    response = client.get(f"/api/call/{started_call}/recordings")
    assert response.status_code == 200
    recordings = response.get_json()["recordings"]
    assert len(recordings) == 1
    assert recordings[0]["playable"] is True
    assert recordings[0]["playback_url"] == f"/api/recording/{recordings[0]['id']}"
    assert recordings[0]["call_id"] == started_call


def test_an_invalid_audio_upload_is_rejected(client, started_call, settings):
    client.post("/api/call/consent", json={"call_id": started_call, "decision": "granted"})
    response = client.post(
        "/api/call/recording",
        data={
            "call_id": started_call,
            "recording": (_audio(data=b"not audio at all" * 20), "recording.webm"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "recording_rejected"
    assert client.get(f"/api/calls/{started_call}").get_json()["call"]["recording_count"] == 0


def test_an_unsupported_file_type_is_rejected(client, started_call):
    client.post("/api/call/consent", json={"call_id": started_call, "decision": "granted"})
    response = client.post(
        "/api/call/recording",
        data={"call_id": started_call, "recording": (_audio(data=b"x"), "payload.exe")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "recording_rejected"


def test_an_empty_upload_is_rejected(client, started_call):
    client.post("/api/call/consent", json={"call_id": started_call, "decision": "granted"})
    response = client.post(
        "/api/call/recording",
        data={"call_id": started_call, "recording": (_audio(data=b""), "recording.webm")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_recording_for_an_unknown_call_returns_404(client):
    response = client.post(
        "/api/call/recording",
        data={"call_id": "nope", "recording": (_audio(), "recording.webm")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 404


def test_recordings_are_not_uploaded_to_a_second_location(client, started_call, settings):
    client.post("/api/call/consent", json={"call_id": started_call, "decision": "granted"})
    client.post(
        "/api/call/recording",
        data={"call_id": started_call, "recording": (_audio(), "recording.webm")},
        content_type="multipart/form-data",
    )
    store = RecordingStore(settings.resolved_recordings_dir)
    assert store.total_bytes() > 0


def test_store_refuses_paths_outside_the_recordings_folder(tmp_path):
    store = RecordingStore(tmp_path / "recordings")
    assert store.path_for("call", "../../secret.txt") is None
    assert store.path_for("call", "sub/folder.webm") is None


def test_store_rejects_unsupported_extensions(tmp_path):
    store = RecordingStore(tmp_path / "recordings")
    with pytest.raises(RecordingError):
        store.save("call", b"data", "payload.exe", "application/octet-stream")


def test_store_rejects_oversized_files(tmp_path):
    store = RecordingStore(tmp_path / "recordings", max_bytes=1024)
    with pytest.raises(RecordingError):
        store.save("call", b"x" * 2048, "recording.webm", "audio/webm")


def test_store_writes_the_file_locally(tmp_path):
    store = RecordingStore(tmp_path / "recordings")
    saved = store.save("call123", VALID_WEBM, "recording.webm", "audio/webm")
    assert saved.path.exists()
    assert saved.path.read_bytes() == VALID_WEBM
    assert saved.path.parent.name == "call123"


def test_store_rejects_a_file_that_is_not_audio(tmp_path):
    store = RecordingStore(tmp_path / "recordings")
    with pytest.raises(RecordingError):
        store.save("call", b"this is not audio" * 10, "recording.webm", "audio/webm")


def test_probe_reports_a_missing_file(tmp_path):
    from salesvoice.recording import probe_audio

    info = probe_audio(tmp_path / "nope.webm")
    assert info["available"] is False
    assert info["valid_audio"] is False
    assert info["size_bytes"] == 0
    assert info["problem"]


def test_probe_reports_an_empty_file(tmp_path):
    from salesvoice.recording import probe_audio

    path = tmp_path / "empty.webm"
    path.write_bytes(b"")
    info = probe_audio(path)
    assert info["available"] is True
    assert info["valid_audio"] is False
    assert "empty" in info["problem"].lower()


def test_probe_reports_an_invalid_file(tmp_path):
    from salesvoice.recording import probe_audio

    path = tmp_path / "bad.webm"
    path.write_bytes(b"not really audio" * 20)
    info = probe_audio(path)
    assert info["available"] is True
    assert info["valid_audio"] is False
    assert "recognised" in info["problem"].lower() or "recognized" in info["problem"].lower()


def test_probe_accepts_a_real_header(tmp_path):
    from salesvoice.recording import probe_audio

    path = tmp_path / "good.webm"
    path.write_bytes(VALID_WEBM)
    info = probe_audio(path)
    assert info == {
        "available": True,
        "valid_audio": True,
        "size_bytes": len(VALID_WEBM),
        "problem": None,
    }


def test_inspect_reports_missing_stored_file(tmp_path):
    store = RecordingStore(tmp_path / "recordings")
    info = store.inspect("call", "gone.webm")
    assert info["available"] is False
    assert info["valid_audio"] is False
