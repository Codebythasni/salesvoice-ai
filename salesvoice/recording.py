"""Local, consent-gated storage for call audio.

Recordings are written to a local folder (default ``recordings/``) that is
excluded from Git. Nothing is uploaded anywhere.

Scope note: browsers cannot route the output of ``speechSynthesis`` into a
``MediaStream``, so the capture that is available here is the customer
microphone only. The assistant side is therefore *not* recorded, and the
dashboard says so plainly instead of claiming a full two-sided recording.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".webm", ".wav", ".ogg", ".oga", ".mp3", ".m4a", ".mp4"}
MAX_FILENAME_LENGTH = 200
MIN_AUDIO_BYTES = 32

# A recording is only reported as playable when the bytes on disk start with a
# signature that matches a real audio container. This keeps a missing, empty or
# corrupt file from being presented as a working recording.
_EBML_HEADER = b"\x1a\x45\xdf\xa3"
_OGG_HEADER = b"OggS"
_RIFF_HEADER = b"RIFF"
_MP3_HEADERS = (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa", b"\xff\xe3")
_FTYP_HEADER = b"ftyp"

TWO_SIDED_RECORDING_SUPPORTED = False
RECORDING_SCOPE_NOTICE = (
    "Only the customer microphone is captured. The assistant's spoken replies are "
    "produced by the browser and cannot be routed into the recording, so this is a "
    "customer-side capture, not a full two-sided call recording."
)


class RecordingError(RuntimeError):
    """A recording failure with a message that is safe to show to the user."""

    def __init__(self, public_message: str, detail: str = "") -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.detail = detail


@dataclass
class SavedRecording:
    filename: str
    path: Path
    size_bytes: int
    mime_type: str | None
    duration_seconds: float | None = None

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "mime_type": self.mime_type,
            "duration_seconds": self.duration_seconds,
        }


def _safe_extension(filename: str) -> str:
    name = os.path.basename(filename or "").strip()
    extension = Path(name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise RecordingError(
            "That file type is not supported. Use a webm, wav, ogg, mp3, m4a or mp4 recording.",
            detail=f"rejected extension: {extension!r}",
        )
    if not name or len(name) > MAX_FILENAME_LENGTH:
        raise RecordingError(
            "The recording file name is not valid.", detail="bad filename"
        )
    return extension


def _audio_looks_valid(head: bytes, extension: str) -> bool:
    """Check the leading bytes against the signature for this container."""
    if extension == ".webm":
        return head.startswith(_EBML_HEADER)
    if extension in (".ogg", ".oga"):
        return head.startswith(_OGG_HEADER)
    if extension == ".wav":
        return head.startswith(_RIFF_HEADER) and b"WAVE" in head[:16]
    if extension == ".mp3":
        return any(head.startswith(sig) for sig in _MP3_HEADERS)
    if extension in (".m4a", ".mp4"):
        # ISO base media files place the ftyp box after a 4-byte size field.
        return head[4:8] == _FTYP_HEADER
    return False


def probe_audio(path: Path) -> dict:
    """Describe whether ``path`` is a real, playable audio file.

    Returns a dict with ``available`` (the file exists), ``valid_audio`` (it
    contains recognisable audio), ``size_bytes`` and a user-facing ``problem``.
    """
    if not path.is_file():
        return {
            "available": False,
            "valid_audio": False,
            "size_bytes": 0,
            "problem": "The audio file is missing from this computer.",
        }

    try:
        size = path.stat().st_size
    except OSError:
        return {
            "available": False,
            "valid_audio": False,
            "size_bytes": 0,
            "problem": "The audio file could not be read.",
        }

    if size == 0:
        return {
            "available": True,
            "valid_audio": False,
            "size_bytes": 0,
            "problem": "The audio file is empty, so there is nothing to play.",
        }

    if size < MIN_AUDIO_BYTES:
        return {
            "available": True,
            "valid_audio": False,
            "size_bytes": size,
            "problem": "The audio file is too small to contain a real recording.",
        }

    try:
        with open(path, "rb") as handle:
            head = handle.read(16)
    except OSError:
        return {
            "available": True,
            "valid_audio": False,
            "size_bytes": size,
            "problem": "The audio file could not be read.",
        }

    if not _audio_looks_valid(head, path.suffix.lower()):
        return {
            "available": True,
            "valid_audio": False,
            "size_bytes": size,
            "problem": "The audio file does not contain a recognised audio format.",
        }

    return {
        "available": True,
        "valid_audio": True,
        "size_bytes": size,
        "problem": None,
    }


class RecordingStore:
    """Stores audio files on the local disk, one folder per call."""

    def __init__(self, root: str | Path, max_bytes: int = 25 * 1024 * 1024) -> None:
        self.root = Path(root).resolve()
        self.max_bytes = max(1024, int(max_bytes))
        self.root.mkdir(parents=True, exist_ok=True)

    def call_folder(self, call_id: str) -> Path:
        safe = "".join(ch for ch in str(call_id) if ch.isalnum())[:64] or "unknown"
        return self.root / safe

    def save(self, call_id: str, data: bytes, original_name: str, mime_type: str | None) -> SavedRecording:
        if not data:
            raise RecordingError("The recording was empty, so nothing was saved.", detail="empty body")
        if len(data) > self.max_bytes:
            raise RecordingError(
                "That recording is too large to store. Keep calls under "
                f"{self.max_bytes // (1024 * 1024)} MB.",
                detail=f"body of {len(data)} bytes",
            )

        extension = _safe_extension(original_name)
        if len(data) < MIN_AUDIO_BYTES or not _audio_looks_valid(data[:16], extension):
            raise RecordingError(
                "The uploaded file is not a valid audio recording, so nothing was saved.",
                detail=f"invalid {extension} payload of {len(data)} bytes",
            )
        filename = f"{secrets.token_hex(8)}{extension}"
        folder = self.call_folder(call_id)
        folder.mkdir(parents=True, exist_ok=True)
        target = (folder / filename).resolve()

        if not str(target).startswith(str(self.root)):
            raise RecordingError(
                "The recording could not be stored.", detail="path outside recordings root"
            )

        try:
            with open(target, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            raise RecordingError(
                "The recording could not be written to disk.",
                detail=type(exc).__name__,
            ) from exc

        return SavedRecording(
            filename=filename,
            path=target,
            size_bytes=len(data),
            mime_type=mime_type,
        )

    def path_for(self, call_id: str, filename: str) -> Path | None:
        """Resolve a stored file, refusing anything outside the recordings root."""
        if not filename or os.path.basename(filename) != filename:
            return None
        candidate = (self.call_folder(call_id) / filename).resolve()
        if not str(candidate).startswith(str(self.root)):
            return None
        if not candidate.is_file():
            return None
        return candidate

    def inspect(self, call_id: str, filename: str) -> dict:
        """Check whether the stored file is present and actually playable."""
        path = self.path_for(call_id, filename)
        if path is None:
            return {
                "available": False,
                "valid_audio": False,
                "size_bytes": 0,
                "problem": "The audio file is missing from this computer.",
            }
        return probe_audio(path)

    def delete(self, call_id: str, filename: str) -> bool:
        target = self.path_for(call_id, filename)
        if target is None:
            return False
        try:
            target.unlink()
        except OSError:
            return False
        folder = self.call_folder(call_id)
        try:
            if folder.is_dir() and not any(folder.iterdir()):
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass
        return True

    def total_bytes(self) -> int:
        total = 0
        for path in self.root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total
