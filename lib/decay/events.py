"""DecayEvent model + C-010 severity hint algorithm."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

CATEGORIES = (
    "Cat 1 — Oscillation",
    "Cat 2 — Lost decisions",
    "Cat 3 — Out-of-context over-emphasis",
    "Cat 4 — Off-topic noise",
    "Cat 5 — Cross-agent briefing gaps",
    "Cat 6 — Terminology drift",
)


def severity_for_span(span: int) -> str:
    """C-010: span = last_index − first_index across manifest occurrences."""
    if span <= 2:
        return "low"
    if span <= 6:
        return "medium"
    return "high"


@dataclass
class DecayEvent:
    timestamp: str
    source_anchor: dict
    category: str
    description: str
    refactor: str
    first_index: int
    last_index: int
    origin_session: Optional[str] = None
    severity: str = field(init=False)
    event_id: str = field(init=False)
    origin_traced: bool = True

    def __post_init__(self) -> None:
        self.severity = severity_for_span(self.last_index - self.first_index)
        # Stable, deterministic id based on category + anchor + description.
        anchor_key = repr(sorted(self.source_anchor.items()))
        digest_input = f"{self.category}|{anchor_key}|{self.description}"
        self.event_id = hashlib.sha1(
            digest_input.encode("utf-8")
        ).hexdigest()[:10]
