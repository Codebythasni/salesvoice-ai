"""Call session models and the shared vocabulary of the application.

The values below are the contract between the server, the database and the
dashboard, so they are defined in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

ACTIVE = "active"
ENDED = "ended"

# The complete list of sales statuses a call can carry. The dashboard and the
# API both use these exact strings.
SALES_STATUSES = (
    "Converted",
    "Interested",
    "Follow-up Required",
    "Not Interested",
    "Demo Requested",
    "No Response",
)
DEFAULT_SALES_STATUS = "No Response"

# How the current sales status was chosen.
STATUS_SOURCE_DEFAULT = "default"
STATUS_SOURCE_MANUAL = "manual"
STATUS_SOURCE_SUGGESTED = "suggested"

# Recording consent states.
CONSENT_PENDING = "pending"
CONSENT_GRANTED = "granted"
CONSENT_DENIED = "denied"
CONSENT_STATES = (CONSENT_PENDING, CONSENT_GRANTED, CONSENT_DENIED)


def is_valid_sales_status(value: object) -> bool:
    return isinstance(value, str) and value in SALES_STATUSES


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class Turn:
    role: str
    text: str
    at: datetime = field(default_factory=_now)
    source: str = "text"
    client_message_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "text": self.text,
            "at": self.at.isoformat(),
            "source": self.source,
            "client_message_id": self.client_message_id,
        }


@dataclass
class CallSession:
    """A single sales call, as read back from the database."""

    id: str
    product_key: str = "taskflow-crm"
    started_at: datetime = field(default_factory=_now)
    ended_at: datetime | None = None
    status: str = ACTIVE
    sales_status: str = DEFAULT_SALES_STATUS
    sales_status_source: str = STATUS_SOURCE_DEFAULT
    summary: str | None = None
    summary_error: str | None = None
    recording_consent: str = CONSENT_PENDING
    turns: list[Turn] = field(default_factory=list)
    recording_count: int = 0

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or _now()
        return max(0.0, (end - self.started_at).total_seconds())

    @property
    def is_active(self) -> bool:
        return self.status == ACTIVE

    def history(self) -> list[dict]:
        return [{"role": t.role, "text": t.text} for t in self.turns]

    def transcript_text(self) -> str:
        return "\n".join(f"{t.role}: {t.text}" for t in self.turns)

    def to_dict(self, include_turns: bool = True) -> dict:
        payload = {
            "id": self.id,
            "product": self.product_key,
            "status": self.status,
            "sales_status": self.sales_status,
            "sales_status_source": self.sales_status_source,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_seconds": round(self.duration_seconds, 1),
            "turn_count": len(self.turns),
            "summary": self.summary,
            "summary_error": self.summary_error,
            "summary_available": bool(self.summary),
            "recording_consent": self.recording_consent,
            "recording_count": self.recording_count,
            "recording_available": self.recording_count > 0,
            "persisted": True,
        }
        if include_turns:
            payload["turns"] = [t.to_dict() for t in self.turns]
        return payload

    @classmethod
    def from_row(cls, row, turns: list[Turn] | None = None, recording_count: int = 0) -> "CallSession":
        return cls(
            id=row["id"],
            product_key=row["product_key"],
            started_at=_parse(row["started_at"]) or _now(),
            ended_at=_parse(row["ended_at"]),
            status=row["status"],
            sales_status=row["sales_status"] or DEFAULT_SALES_STATUS,
            sales_status_source=row["sales_status_source"] or STATUS_SOURCE_DEFAULT,
            summary=row["summary"],
            summary_error=row["summary_error"],
            recording_consent=row["recording_consent"] or CONSENT_PENDING,
            turns=turns or [],
            recording_count=recording_count,
        )
