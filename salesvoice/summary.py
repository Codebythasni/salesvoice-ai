"""End-of-call summary generation.

The summary is produced by Gemini from the stored transcript. If Gemini is
unavailable the call is still kept - the failure is reported as a safe
message and the summary can be retried later.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from .sessions import CallSession, DEFAULT_SALES_STATUS, SALES_STATUSES, is_valid_sales_status

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = """You summarise sales calls for the sales team.

You will be given a transcript of a call where an assistant (Ava) from Sankar Group
sold TaskFlow CRM to a potential customer.

Return ONLY a JSON object, with no markdown fences and no commentary, using exactly
these keys:

{
  "requirements": "what the customer said they need, in 1-3 sentences",
  "objections": "the concerns or objections the customer raised and how they were addressed, in 1-3 sentences",
  "next_steps": "the agreed next steps, in 1-3 sentences",
  "outcome": "how the call ended, in 1-2 sentences",
  "suggested_status": "one of: Converted, Interested, Follow-up Required, Not Interested, Demo Requested, No Response"
}

Rules:
- Base everything on the transcript only. If the customer did not say something, write "Not mentioned".
- suggested_status must be exactly one of the listed values. Use "No Response" when the customer
  did not really engage, and "Converted" only when a purchase or trial was agreed.
- Do not invent names, prices, dates or commitments that are not in the transcript.
- Keep each field under 60 words."""

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# The labels used by :meth:`SummaryResult.to_text`. The same order is used
# whenever a stored summary is read back and shown to the user.
SUMMARY_FIELDS = (
    ("requirements", "Customer requirements"),
    ("objections", "Objections raised"),
    ("next_steps", "Next steps"),
    ("outcome", "Outcome"),
)


class SummaryError(RuntimeError):
    """A summary failure with a message that is safe to show to the user."""

    def __init__(self, public_message: str, detail: str = "") -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.detail = detail


@dataclass
class SummaryResult:
    requirements: str = ""
    objections: str = ""
    next_steps: str = ""
    outcome: str = ""
    suggested_status: str = DEFAULT_SALES_STATUS
    raw: str = ""

    def to_text(self) -> str:
        return (
            f"Customer requirements: {self.requirements}\n"
            f"Objections raised: {self.objections}\n"
            f"Next steps: {self.next_steps}\n"
            f"Outcome: {self.outcome}"
        )

    def to_dict(self) -> dict:
        return {
            "requirements": self.requirements,
            "objections": self.objections,
            "next_steps": self.next_steps,
            "outcome": self.outcome,
            "suggested_status": self.suggested_status,
            "text": self.to_text(),
        }


def _extract_json(raw: str) -> dict | None:
    candidates = []
    fenced = _FENCE_RE.search(raw)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate.strip())
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _text_field(data: dict, key: str) -> str:
    value = data.get(key)
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value)
    if value is None:
        return ""
    text = str(value).strip()
    return text[:2000]


def parse_summary(raw: str) -> SummaryResult:
    """Parse the model output into a :class:`SummaryResult`.

    Falls back to plain text when the model does not return usable JSON, so a
    summary is never lost because of a formatting quirk.
    """
    raw = (raw or "").strip()
    data = _extract_json(raw)

    if data is None:
        return SummaryResult(
            requirements="",
            objections="",
            next_steps="",
            outcome=raw[:2000] if raw else "Not mentioned",
            suggested_status=DEFAULT_SALES_STATUS,
            raw=raw,
        )

    suggested = data.get("suggested_status")
    if not is_valid_sales_status(suggested):
        suggested = DEFAULT_SALES_STATUS

    result = SummaryResult(
        requirements=_text_field(data, "requirements") or "Not mentioned",
        objections=_text_field(data, "objections") or "Not mentioned",
        next_steps=_text_field(data, "next_steps") or "Not mentioned",
        outcome=_text_field(data, "outcome") or "Not mentioned",
        suggested_status=suggested,
        raw=raw,
    )
    return result


def summary_text_to_dict(text: str | None) -> dict:
    """Read a stored summary back into its four fields.

    The summary is written to the database as plain text so that it can be
    read without any dependency. This turns that text back into the fields the
    dashboard shows, which keeps a reloaded call looking exactly like the one
    that was saved. An empty dict means the text is not in the known shape.
    """
    if not text or not text.strip():
        return {}

    fields = {key: "" for key, _ in SUMMARY_FIELDS}
    current: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        matched = False
        for key, label in SUMMARY_FIELDS:
            prefix = label + ":"
            if stripped.lower().startswith(prefix.lower()):
                fields[key] = stripped[len(prefix) :].strip()
                current = key
                matched = True
                break
        if matched:
            continue

        if current:
            fields[current] = (fields[current] + " " + stripped).strip()
        else:
            fields["outcome"] = (fields["outcome"] + " " + stripped).strip()

    if not any(fields.values()):
        return {}
    return fields


def generate_call_summary(assistant, call: CallSession) -> SummaryResult:
    """Ask the assistant to summarise a finished call.

    Raises :class:`SummaryError` with a user-safe message when the assistant
    is missing or fails. The caller keeps the call either way.
    """
    if assistant is None:
        raise SummaryError(
            "The assistant is not configured on the server, so no summary could be generated.",
            detail="assistant is None",
        )

    generate = getattr(assistant, "generate_summary", None)
    if generate is None:
        raise SummaryError(
            "The assistant cannot generate summaries, so no summary could be created.",
            detail="assistant has no generate_summary method",
        )

    transcript = call.transcript_text()
    if not transcript.strip():
        raise SummaryError(
            "This call has no transcript, so there is nothing to summarise.",
            detail="empty transcript",
        )

    try:
        raw = generate(transcript)
    except Exception as exc:
        public = getattr(exc, "public_message", None)
        detail = getattr(exc, "detail", "") or type(exc).__name__
        raise SummaryError(
            public or "The summary could not be generated. The call was saved unchanged.",
            detail=detail,
        ) from exc

    return parse_summary(raw)


def apply_suggested_status(database, call: CallSession, result: SummaryResult) -> CallSession:
    """Apply the suggested status unless the user already picked one."""
    if call.sales_status_source == "manual":
        return call
    if result.suggested_status not in SALES_STATUSES:
        return call
    updated = database.set_sales_status(call.id, result.suggested_status, "suggested")
    return updated or call
