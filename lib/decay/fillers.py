"""Off-topic filler vocabulary loader (Cat 4 GO-WITH-CONDITION).

Loads a YAML vocabulary file shipped alongside the tool (default:
`agent-tooling/data/off-topic-fillers.yaml`). Per design-review § 3, ≥ 20
phrases drawn from Jurafsky 1998 lexical-cue research must be enumerated.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import yaml

_MIN_PHRASES = 20


class FillerVocabularyError(ValueError):
    pass


def load_fillers(path: str | Path) -> list[str]:
    p = Path(path)
    if not p.exists():
        raise FillerVocabularyError(f"filler vocabulary not found at {p}")
    raw = yaml.safe_load(p.read_text()) or {}
    if not isinstance(raw, dict) or "phrases" not in raw:
        raise FillerVocabularyError(
            "vocabulary file must be a mapping with top-level `phrases:` list"
        )
    phrases = raw["phrases"]
    if not isinstance(phrases, list) or len(phrases) < _MIN_PHRASES:
        raise FillerVocabularyError(
            f"vocabulary must enumerate ≥ {_MIN_PHRASES} phrases "
            f"(got {len(phrases) if isinstance(phrases, list) else 'invalid'})"
        )
    return [str(p).strip().lower() for p in phrases if str(p).strip()]


def compile_filler_regex(phrases: Iterable[str]) -> re.Pattern:
    # Word-boundary anchored, case-insensitive, longest-first to favour
    # multi-word phrases over their single-word prefixes.
    ordered = sorted({p for p in phrases if p}, key=lambda s: -len(s))
    pattern = r"\b(?:" + "|".join(re.escape(p) for p in ordered) + r")\b"
    return re.compile(pattern, re.IGNORECASE)
