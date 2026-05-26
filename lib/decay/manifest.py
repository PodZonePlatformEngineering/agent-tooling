"""Trajectory manifest loader + validation (per spec § R-001 + AC-005 condition).

Manifest schema:

    artefacts:
      - path: team/.../session-XXXX-status.md
        type: outbox        # one of session|outbox|brief|pr-diff|transcript-ref
        timestamp: 2026-05-20T23:30:00+01:00
        role: hephaestus    # one of hermes|hephaestus|atlas|thoth|other
      - qdrant_collection: sessions
        qdrant_id: <uuid>
        type: session
        timestamp: 2026-05-21T01:53:00+01:00
        role: hephaestus

Validation: every entry must include `type`, `timestamp`, `role`; either `path`
or both (`qdrant_collection`, `qdrant_id`). Missing `type` is a hard error
(GO-WITH-CONDITION for Cat 3 per design-review.md § 3).

C-009 pre-playbook flag: any entry with timestamp < 2026-05-24 is marked
`pre_playbook=True` after load; affects Cat 2 (disabled) and Cat 6 baseline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import yaml

VALID_TYPES = {"session", "outbox", "brief", "pr-diff", "transcript-ref"}
VALID_ROLES = {"hermes", "hephaestus", "atlas", "thoth", "other"}
PLAYBOOK_EPOCH = datetime(2026, 5, 24, tzinfo=timezone.utc)


class ManifestError(ValueError):
    """Raised on malformed manifest input."""


@dataclass
class ManifestEntry:
    index: int
    type: str
    timestamp: str
    timestamp_dt: datetime
    role: str
    path: Optional[str] = None
    qdrant_collection: Optional[str] = None
    qdrant_id: Optional[str] = None
    pre_playbook: bool = False

    def is_file(self) -> bool:
        return self.path is not None

    def is_qdrant(self) -> bool:
        return self.qdrant_collection is not None and self.qdrant_id is not None

    def display_ref(self) -> str:
        if self.is_file():
            return self.path or ""
        return f"qdrant://{self.qdrant_collection}/{self.qdrant_id}"


@dataclass
class Manifest:
    entries: list[ManifestEntry]
    source_path: Optional[Path] = None
    raw: dict = field(default_factory=dict)

    def __iter__(self) -> Iterable[ManifestEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


def _parse_timestamp(raw: str, index: int) -> datetime:
    try:
        dt = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError) as exc:
        raise ManifestError(
            f"entry #{index}: timestamp {raw!r} is not ISO-8601"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _validate_entry(raw_entry: dict, index: int) -> ManifestEntry:
    if not isinstance(raw_entry, dict):
        raise ManifestError(
            f"entry #{index}: expected mapping, got {type(raw_entry).__name__}"
        )

    missing = [k for k in ("type", "timestamp", "role") if k not in raw_entry]
    if missing:
        raise ManifestError(
            f"entry #{index}: missing required field(s) {missing}; "
            f"manifest is invalid (Cat-3 condition per design-review § 3)"
        )

    entry_type = raw_entry["type"]
    if entry_type not in VALID_TYPES:
        raise ManifestError(
            f"entry #{index}: type {entry_type!r} not in {sorted(VALID_TYPES)}"
        )

    role = raw_entry["role"]
    if role not in VALID_ROLES:
        raise ManifestError(
            f"entry #{index}: role {role!r} not in {sorted(VALID_ROLES)}"
        )

    has_path = "path" in raw_entry
    has_qdrant = "qdrant_collection" in raw_entry and "qdrant_id" in raw_entry
    if not (has_path or has_qdrant):
        raise ManifestError(
            f"entry #{index}: must provide either `path` or both "
            f"`qdrant_collection` + `qdrant_id`"
        )

    timestamp_dt = _parse_timestamp(raw_entry["timestamp"], index)

    entry = ManifestEntry(
        index=index,
        type=entry_type,
        timestamp=str(raw_entry["timestamp"]),
        timestamp_dt=timestamp_dt,
        role=role,
        path=raw_entry.get("path"),
        qdrant_collection=raw_entry.get("qdrant_collection"),
        qdrant_id=raw_entry.get("qdrant_id"),
        pre_playbook=timestamp_dt < PLAYBOOK_EPOCH,
    )
    return entry


def load_manifest(path: str | os.PathLike) -> Manifest:
    """Load + validate a trajectory manifest from disk."""
    p = Path(path)
    if not p.exists():
        raise ManifestError(f"manifest not found at {p}")
    raw = yaml.safe_load(p.read_text()) or {}
    if not isinstance(raw, dict) or "artefacts" not in raw:
        raise ManifestError(
            "manifest must be a mapping with top-level `artefacts:` list"
        )
    artefacts = raw["artefacts"]
    if not isinstance(artefacts, list) or not artefacts:
        raise ManifestError("`artefacts:` must be a non-empty list")

    entries: list[ManifestEntry] = []
    for i, raw_entry in enumerate(artefacts):
        entries.append(_validate_entry(raw_entry, i))

    # Stable ordering: chronological by timestamp; manifest order tie-break.
    entries.sort(key=lambda e: (e.timestamp_dt, e.index))
    # Re-index after sort so `index` reflects chronological position.
    for new_idx, e in enumerate(entries):
        e.index = new_idx

    return Manifest(entries=entries, source_path=p, raw=raw)
