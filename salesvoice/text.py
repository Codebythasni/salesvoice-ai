"""Small text helpers for customer utterances.

Speech recognition often splits or mangles product names ("task flow crm",
"taskflowcrm"). These helpers repair the obvious cases before the utterance is
sent to the model, so the assistant hears the product name the customer meant.
"""

from __future__ import annotations

import re

# Ordered longest first so the longer alias wins.
PRODUCT_ALIASES: tuple[tuple[str, str], ...] = (
    (r"\btask[\s\-]*flow[\s\-]*c\s*r\s*m\b", "TaskFlow CRM"),
    (r"\btask[\s\-]*flow\b", "TaskFlow"),
    (r"\btaskflowcrm\b", "TaskFlow CRM"),
    (r"\bsank(?:ar|er)[\s\-]*group\b", "Sankar Group"),
)

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_utterance(text: str) -> str:
    """Repair whitespace and common product-name mis-hearings."""
    if not text:
        return ""
    cleaned = _WHITESPACE_RE.sub(" ", text).strip()
    for pattern, replacement in PRODUCT_ALIASES:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned


def was_normalized(original: str, normalized: str) -> bool:
    """True when the text was changed by :func:`normalize_utterance`."""
    return bool(original) and original.strip() != normalized
