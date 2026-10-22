"""SQLite persistence for call history.

Every statement in this module uses bound parameters - no SQL string is ever
built from user input. The database file lives outside the source tree (in
``instance/`` by default) and is excluded from Git.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .sessions import (
    ACTIVE,
    CONSENT_PENDING,
    DEFAULT_SALES_STATUS,
    ENDED,
    STATUS_SOURCE_DEFAULT,
    CallSession,
    Turn,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id                  TEXT PRIMARY KEY,
    product_key         TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    duration_seconds    REAL NOT NULL DEFAULT 0,
    status              TEXT NOT NULL,
    sales_status        TEXT NOT NULL,
    sales_status_source TEXT NOT NULL DEFAULT 'default',
    summary             TEXT,
    summary_error       TEXT,
    recording_consent   TEXT NOT NULL DEFAULT 'pending',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS call_turns (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id            TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    seq                INTEGER NOT NULL,
    role               TEXT NOT NULL,
    text               TEXT NOT NULL,
    source             TEXT NOT NULL DEFAULT 'text',
    at                 TEXT NOT NULL,
    client_message_id  TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_call_turns_client_message
    ON call_turns (call_id, client_message_id)
    WHERE client_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_call_turns_call ON call_turns (call_id, seq);

CREATE TABLE IF NOT EXISTS recordings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id           TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    filename          TEXT NOT NULL,
    mime_type         TEXT,
    size_bytes        INTEGER NOT NULL DEFAULT 0,
    duration_seconds  REAL,
    consent           TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recordings_call ON recordings (call_id);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CallDatabase:
    """Thread-safe SQLite store for calls, turns and recording metadata."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        if self.path != ":memory:":
            parent = Path(self.path).parent
            if str(parent):
                parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self.init_schema()

    def init_schema(self) -> None:
        """Create the tables and indexes if they do not exist yet."""
        with self._lock:
            self._connection.executescript(SCHEMA)
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # ------------------------------------------------------------------
    # calls
    # ------------------------------------------------------------------

    def create_call(self, product_key: str) -> CallSession:
        now = _now_iso()
        call_id = uuid.uuid4().hex
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO calls (
                    id, product_key, started_at, ended_at, duration_seconds, status,
                    sales_status, sales_status_source, summary, summary_error,
                    recording_consent, created_at, updated_at
                ) VALUES (?, ?, ?, NULL, 0, ?, ?, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    call_id,
                    product_key,
                    now,
                    ACTIVE,
                    DEFAULT_SALES_STATUS,
                    STATUS_SOURCE_DEFAULT,
                    CONSENT_PENDING,
                    now,
                    now,
                ),
            )
            self._connection.commit()
        return self.get_call(call_id)  # type: ignore[return-value]

    def get_call(self, call_id: str) -> CallSession | None:
        if not isinstance(call_id, str) or not call_id:
            return None
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM calls WHERE id = ?", (call_id,)
            ).fetchone()
            if row is None:
                return None
            return self._hydrate(row)

    def get_active_call(self) -> CallSession | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM calls WHERE status = ? ORDER BY started_at DESC LIMIT 1",
                (ACTIVE,),
            ).fetchone()
            if row is None:
                return None
            return self._hydrate(row)

    def end_call(self, call_id: str) -> CallSession | None:
        now = datetime.now(timezone.utc)
        with self._lock:
            row = self._connection.execute(
                "SELECT started_at, status FROM calls WHERE id = ?", (call_id,)
            ).fetchone()
            if row is None:
                return None
            if row["status"] == ENDED:
                return self.get_call(call_id)
            started = row["started_at"]
            try:
                started_dt = datetime.fromisoformat(started)
            except ValueError:
                started_dt = now
            duration = max(0.0, (now - started_dt).total_seconds())
            self._connection.execute(
                """
                UPDATE calls
                   SET status = ?, ended_at = ?, duration_seconds = ?, updated_at = ?
                 WHERE id = ?
                """,
                (ENDED, now.isoformat(), duration, now.isoformat(), call_id),
            )
            self._connection.commit()
        return self.get_call(call_id)

    def set_sales_status(
        self, call_id: str, sales_status: str, source: str
    ) -> CallSession | None:
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE calls
                   SET sales_status = ?, sales_status_source = ?, updated_at = ?
                 WHERE id = ?
                """,
                (sales_status, source, _now_iso(), call_id),
            )
            self._connection.commit()
            if cursor.rowcount == 0:
                return None
        return self.get_call(call_id)

    def set_summary(self, call_id: str, summary: str) -> CallSession | None:
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE calls
                   SET summary = ?, summary_error = NULL, updated_at = ?
                 WHERE id = ?
                """,
                (summary, _now_iso(), call_id),
            )
            self._connection.commit()
            if cursor.rowcount == 0:
                return None
        return self.get_call(call_id)

    def set_summary_error(self, call_id: str, message: str) -> CallSession | None:
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE calls
                   SET summary_error = ?, updated_at = ?
                 WHERE id = ?
                """,
                (message, _now_iso(), call_id),
            )
            self._connection.commit()
            if cursor.rowcount == 0:
                return None
        return self.get_call(call_id)

    def set_recording_consent(self, call_id: str, consent: str) -> CallSession | None:
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE calls
                   SET recording_consent = ?, updated_at = ?
                 WHERE id = ?
                """,
                (consent, _now_iso(), call_id),
            )
            self._connection.commit()
            if cursor.rowcount == 0:
                return None
        return self.get_call(call_id)

    def list_calls(self, limit: int = 50) -> list[CallSession]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM calls ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._hydrate(row) for row in rows]

    def count_calls(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS total FROM calls").fetchone()
            return int(row["total"]) if row else 0

    # ------------------------------------------------------------------
    # turns
    # ------------------------------------------------------------------

    def append_turn(
        self,
        call_id: str,
        role: str,
        text: str,
        source: str = "text",
        client_message_id: str | None = None,
    ) -> Turn | None:
        """Append a transcript entry. Returns ``None`` if the call is gone."""
        at = _now_iso()
        with self._lock:
            exists = self._connection.execute(
                "SELECT 1 FROM calls WHERE id = ?", (call_id,)
            ).fetchone()
            if exists is None:
                return None
            seq_row = self._connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM call_turns WHERE call_id = ?",
                (call_id,),
            ).fetchone()
            seq = int(seq_row["next_seq"])
            self._connection.execute(
                """
                INSERT INTO call_turns (call_id, seq, role, text, source, at, client_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (call_id, seq, role, text, source, at, client_message_id),
            )
            self._connection.execute(
                "UPDATE calls SET updated_at = ? WHERE id = ?", (at, call_id)
            )
            self._connection.commit()
        return Turn(
            role=role,
            text=text,
            at=datetime.fromisoformat(at),
            source=source,
            client_message_id=client_message_id,
        )

    def find_turn_by_client_message_id(self, call_id: str, client_message_id: str):
        if not client_message_id:
            return None
        with self._lock:
            return self._connection.execute(
                """
                SELECT * FROM call_turns
                 WHERE call_id = ? AND client_message_id = ?
                 ORDER BY seq LIMIT 1
                """,
                (call_id, client_message_id),
            ).fetchone()

    def assistant_reply_for(self, call_id: str, client_message_id: str) -> str | None:
        """Return the assistant reply that directly followed a client message.

        Only the turn immediately after the customer message counts. A later
        successful exchange must never be mistaken for the reply to an earlier
        message that failed, otherwise a retry could attach the wrong answer.
        """
        if not client_message_id:
            return None
        with self._lock:
            row = self._connection.execute(
                """
                SELECT a.text AS text
                  FROM call_turns a
                  JOIN call_turns c
                    ON c.call_id = a.call_id
                   AND a.seq = c.seq + 1
                 WHERE a.call_id = ?
                   AND a.role = 'assistant'
                   AND c.client_message_id = ?
                 ORDER BY c.seq DESC
                 LIMIT 1
                """,
                (call_id, client_message_id),
            ).fetchone()
            return row["text"] if row else None

    def turns_for(self, call_id: str) -> list[Turn]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM call_turns WHERE call_id = ? ORDER BY seq", (call_id,)
            ).fetchall()
            return [
                Turn(
                    role=row["role"],
                    text=row["text"],
                    at=_safe_iso(row["at"]),
                    source=row["source"],
                    client_message_id=row["client_message_id"],
                )
                for row in rows
            ]

    # ------------------------------------------------------------------
    # recordings
    # ------------------------------------------------------------------

    def add_recording(
        self,
        call_id: str,
        filename: str,
        mime_type: str | None,
        size_bytes: int,
        duration_seconds: float | None,
        consent: str,
    ) -> int | None:
        with self._lock:
            exists = self._connection.execute(
                "SELECT 1 FROM calls WHERE id = ?", (call_id,)
            ).fetchone()
            if exists is None:
                return None
            cursor = self._connection.execute(
                """
                INSERT INTO recordings
                    (call_id, filename, mime_type, size_bytes, duration_seconds, consent, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    filename,
                    mime_type,
                    int(size_bytes),
                    duration_seconds,
                    consent,
                    _now_iso(),
                ),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def list_recordings(self, call_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM recordings WHERE call_id = ? ORDER BY id", (call_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_recording(self, recording_id: int) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM recordings WHERE id = ?", (recording_id,)
            ).fetchone()
            return dict(row) if row else None

    def count_recordings(self, call_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM recordings WHERE call_id = ?", (call_id,)
            ).fetchone()
            return int(row["total"]) if row else 0

    def delete_recording(self, recording_id: int) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM recordings WHERE id = ?", (recording_id,)
            )
            self._connection.commit()
            return cursor.rowcount > 0

    def end_stale_active_calls(self) -> int:
        """Close calls that were left open by a previous server process.

        A call cannot continue across a restart, so any call still marked
        active when the app starts is closed with the current timestamp.
        """
        now = datetime.now(timezone.utc)
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, started_at FROM calls WHERE status = ?", (ACTIVE,)
            ).fetchall()
            for row in rows:
                try:
                    started_dt = datetime.fromisoformat(row["started_at"])
                except ValueError:
                    started_dt = now
                duration = max(0.0, (now - started_dt).total_seconds())
                self._connection.execute(
                    """
                    UPDATE calls
                       SET status = ?, ended_at = ?, duration_seconds = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (ENDED, now.isoformat(), duration, now.isoformat(), row["id"]),
                )
            self._connection.commit()
            return len(rows)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _hydrate(self, row) -> CallSession:
        return CallSession.from_row(
            row,
            turns=self.turns_for(row["id"]),
            recording_count=self.count_recordings(row["id"]),
        )


def _safe_iso(value: str | None) -> datetime:
    try:
        return datetime.fromisoformat(value or "")
    except ValueError:
        return datetime.now(timezone.utc)
