"""Six structural decay-category detectors (per spec § C-008 v1-ships column).

Each detector takes (manifest, bodies, context) and returns list[DecayEvent].
- manifest: Manifest (chronologically sorted)
- bodies: dict[manifest_index] -> str (artefact text)
- context: DetectionContext with config + helpers

Pre-playbook (C-009) handling: Cat 2 disabled for the whole run when ALL
entries are pre-playbook; Cat 6 substitutes first-occurrence-as-glossary
for pre-playbook artefacts.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib import work_items_extract  # noqa: E402

from .anchors import file_anchor, qdrant_anchor  # noqa: E402
from .events import CATEGORIES, DecayEvent  # noqa: E402
from .manifest import Manifest, ManifestEntry  # noqa: E402

CAT_1, CAT_2, CAT_3, CAT_4, CAT_5, CAT_6 = CATEGORIES

DECISION_MARKER_RE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?(decision|agreed|resolved|settled|conclusion|decided)(?:\*\*)?\s*:\s*(.+?)$"
)

TERM_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_\-/]{3,}\b")
CAPITALISED_PHRASE_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9]+(?:[ -][A-Z][A-Za-z0-9]+){0,3})\b"
)
BACKTICK_TOKEN_RE = re.compile(r"`([^`\n]{3,40})`")
GLOSSARY_HEADING_RE = re.compile(r"(?im)^#{1,6}\s*[\d.\s]*Glossary\b")
GLOSSARY_ROW_RE = re.compile(
    r"^\|\s*\*?\*?([A-Za-z][^|*]{1,80}?)\*?\*?\s*\|\s*([^|]+?)\s*\|"
)


REACTIVE_TYPES = {"session", "outbox", "transcript-ref"}
PRIMARY_TYPES = {"brief", "pr-diff"}


@dataclass
class DetectionContext:
    project_dir: Optional[Path] = None
    filler_phrases: list[str] = field(default_factory=list)
    filler_regex: Optional[re.Pattern] = None
    noise_budget: str = "low"
    # cached parsed structures
    glossary_terms: list[str] = field(default_factory=list)
    pre_playbook_only: bool = False


# ---------------------------------------------------------------------------
# Helpers


_SUBJECT_KEY_WORDS = 4
_STOP_WORDS = {"the", "a", "an", "to", "of", "for"}
_SPLITTER_RE = re.compile(
    r"\b(is|are|was|were|will|should|must|may|can|defaults|equals|uses|"
    r"becomes)\b",
    re.IGNORECASE,
)


def _subject_key(line: str) -> str:
    """Fingerprint a decision line by the subject phrase ahead of a copula.

    Examples (all yield "glossary load"):
      "glossary load is required for category 6" → "glossary load"
      "glossary load is optional for category 6" → "glossary load"
    Falls back to the first N non-stop words if no copula is present, so
    decisions phrased as imperative or noun-only headlines still cluster.
    """
    text = line.lower().strip().rstrip(".")
    parts = _SPLITTER_RE.split(text, maxsplit=1)
    subject = parts[0] if len(parts) > 1 else text
    words = [w for w in re.findall(r"[A-Za-z0-9]+", subject)
             if w not in _STOP_WORDS]
    if len(words) < 2:
        words = [w for w in re.findall(r"[A-Za-z0-9]+", text)
                 if w not in _STOP_WORDS]
    return " ".join(words[:_SUBJECT_KEY_WORDS])


def _line_no(body: str, match_start: int) -> int:
    return body.count("\n", 0, match_start) + 1


def _entry_anchor(entry: ManifestEntry, line_no: Optional[int] = None,
                  section: Optional[str] = None) -> dict:
    if entry.is_file():
        if line_no:
            return file_anchor(entry.path or "", f"L{line_no}")
        if section:
            return file_anchor(entry.path or "", f"§ {section}")
        return file_anchor(entry.path or "", "")
    return qdrant_anchor(
        entry.qdrant_collection or "", entry.qdrant_id or ""
    )


# ---------------------------------------------------------------------------
# Cat 1 — Oscillation: same decision subject re-resolved across versions of
# the same artefact path.


def detect_oscillation(manifest: Manifest, bodies: dict[int, str],
                       ctx: DetectionContext) -> list[DecayEvent]:
    """Same decision-subject resolved with different values across the
    trajectory (R-002 oscillation signature)."""
    per_subject: dict[str, list[tuple[ManifestEntry, str, int]]] = {}
    for entry in manifest:
        body = bodies.get(entry.index, "")
        for m in DECISION_MARKER_RE.finditer(body):
            value = m.group(2).strip()
            subject = _subject_key(value)
            if not subject:
                continue
            per_subject.setdefault(subject, []).append(
                (entry, value, _line_no(body, m.start()))
            )

    events: list[DecayEvent] = []
    for subj, occurrences in per_subject.items():
        if len(occurrences) < 2:
            continue
        distinct_values = {v.strip().lower() for _, v, _ in occurrences}
        if len(distinct_values) < 2:
            continue
        first_entry, first_value, _first_line = occurrences[0]
        last_entry, last_value, last_line = occurrences[-1]
        events.append(DecayEvent(
            timestamp=last_entry.timestamp,
            source_anchor=_entry_anchor(last_entry, last_line),
            category=CAT_1,
            description=(
                f"Decision on \"{subj}\" re-litigated across trajectory: "
                f"{first_value[:60]!r} → {last_value[:60]!r}"
            ),
            refactor=(
                f"insert resolved decision for \"{subj}\" into spec.md as a "
                "canonical line; archive earlier wording to rejected-paths annex"
            ),
            first_index=first_entry.index,
            last_index=last_entry.index,
            origin_session=first_entry.display_ref(),
        ))
    return events


# ---------------------------------------------------------------------------
# Cat 2 — Lost decisions: decision marker in non-spec artefact whose subject
# is absent from the canonical spec body.


def _canonical_spec_body(manifest: Manifest,
                         bodies: dict[int, str]) -> Optional[str]:
    """Latest spec.md content among manifest entries (file-type only)."""
    candidates = [e for e in manifest
                  if e.is_file() and "spec.md" in (e.path or "")]
    if not candidates:
        return None
    candidates.sort(key=lambda e: e.index)
    return bodies.get(candidates[-1].index, "")


def detect_lost_decisions(manifest: Manifest, bodies: dict[int, str],
                          ctx: DetectionContext) -> list[DecayEvent]:
    if ctx.pre_playbook_only:
        return []

    spec_body = _canonical_spec_body(manifest, bodies)
    if spec_body is None:
        return []
    spec_subjects: set[str] = set()
    for m in DECISION_MARKER_RE.finditer(spec_body):
        spec_subjects.add(_subject_key(m.group(2)))
    # also: any subject phrase appearing verbatim in spec body
    spec_lower = spec_body.lower()

    events: list[DecayEvent] = []
    per_subject_first: dict[str, ManifestEntry] = {}
    per_subject_last: dict[str, tuple[ManifestEntry, int]] = {}
    per_subject_value: dict[str, str] = {}

    for entry in manifest:
        if entry.pre_playbook:
            continue
        if entry.is_file() and "spec.md" in (entry.path or ""):
            continue
        body = bodies.get(entry.index, "")
        for m in DECISION_MARKER_RE.finditer(body):
            subj = _subject_key(m.group(2))
            if not subj:
                continue
            if subj in spec_subjects:
                continue
            if subj and subj in spec_lower:
                continue
            per_subject_first.setdefault(subj, entry)
            per_subject_last[subj] = (entry, _line_no(body, m.start()))
            per_subject_value.setdefault(subj, m.group(2).strip())

    for subj, first_entry in per_subject_first.items():
        last_entry, line = per_subject_last[subj]
        value = per_subject_value[subj]
        events.append(DecayEvent(
            timestamp=last_entry.timestamp,
            source_anchor=_entry_anchor(last_entry, line),
            category=CAT_2,
            description=(
                f"Decision \"{value[:90]}\" recorded outside spec.md "
                f"(subject key: {subj})"
            ),
            refactor=(
                f"insert decision \"{subj}\" into spec.md § Requirements or "
                "§ Constraints with provenance link to source artefact"
            ),
            first_index=first_entry.index,
            last_index=last_entry.index,
            origin_session=first_entry.display_ref(),
        ))
    return events


# ---------------------------------------------------------------------------
# Cat 3 — Over-emphasis: term first appears in a reactive (session / outbox /
# transcript-ref) artefact and is then referenced in ≥ 3 subsequent artefacts.


def _extract_terms(body: str) -> set[str]:
    terms: set[str] = set()
    for m in CAPITALISED_PHRASE_RE.finditer(body):
        terms.add(m.group(1).strip())
    for m in BACKTICK_TOKEN_RE.finditer(body):
        token = m.group(1).strip()
        # Skip path-y or code-y tokens that aren't conceptual terms.
        if token.count("/") <= 1 and token.count(" ") <= 3 and len(token) >= 4:
            terms.add(token)
    return terms


def detect_over_emphasis(manifest: Manifest, bodies: dict[int, str],
                         ctx: DetectionContext) -> list[DecayEvent]:
    first_seen: dict[str, ManifestEntry] = {}
    later_mentions: dict[str, list[ManifestEntry]] = {}

    for entry in manifest:
        body = bodies.get(entry.index, "")
        for term in _extract_terms(body):
            if term not in first_seen:
                first_seen[term] = entry
            else:
                later_mentions.setdefault(term, []).append(entry)

    events: list[DecayEvent] = []
    for term, first_entry in first_seen.items():
        if first_entry.type not in REACTIVE_TYPES:
            continue
        downstream = later_mentions.get(term, [])
        distinct_artefacts = {e.index for e in downstream}
        if len(distinct_artefacts) < 3:
            continue
        last_entry = max(downstream, key=lambda e: e.index)
        events.append(DecayEvent(
            timestamp=last_entry.timestamp,
            source_anchor=_entry_anchor(first_entry),
            category=CAT_3,
            description=(
                f"Term \"{term}\" first introduced in "
                f"{first_entry.type} ({first_entry.display_ref()}) and "
                f"propagates into {len(distinct_artefacts)} downstream "
                "artefacts without primary-spec endorsement"
            ),
            refactor=(
                f"either promote \"{term}\" into spec.md as a primary edit "
                "or move to rejected-paths annex if disproportionate"
            ),
            first_index=first_entry.index,
            last_index=last_entry.index,
            origin_session=first_entry.display_ref(),
        ))
    return events


# ---------------------------------------------------------------------------
# Cat 4 — Off-topic noise: T-1 (turn-length) + T-2 (lexical filler regex).
# Sessions are split into turns at blank-line paragraph breaks; for other
# artefact types only T-2 applies.


def detect_off_topic(manifest: Manifest, bodies: dict[int, str],
                     ctx: DetectionContext) -> list[DecayEvent]:
    if not ctx.filler_regex:
        return []

    events: list[DecayEvent] = []
    for entry in manifest:
        body = bodies.get(entry.index, "")
        if not body:
            continue

        short_turn_count = 0
        filler_hits = 0
        first_filler_line: Optional[int] = None

        if entry.type == "session":
            # Split into paragraph-like turns.
            for para_match in re.finditer(r"(.+?)(?:\n\s*\n|\Z)",
                                          body, re.DOTALL):
                para = para_match.group(1).strip()
                if not para:
                    continue
                words = re.findall(r"\b\w+\b", para)
                if 0 < len(words) <= 3:
                    short_turn_count += 1

        for m in ctx.filler_regex.finditer(body):
            filler_hits += 1
            if first_filler_line is None:
                first_filler_line = _line_no(body, m.start())

        if short_turn_count == 0 and filler_hits < 2:
            continue
        line = first_filler_line or 1
        desc_parts = []
        if short_turn_count:
            desc_parts.append(f"{short_turn_count} ≤3-word turn(s) [T-1]")
        if filler_hits:
            desc_parts.append(f"{filler_hits} filler-phrase hit(s) [T-2]")
        events.append(DecayEvent(
            timestamp=entry.timestamp,
            source_anchor=_entry_anchor(entry, line),
            category=CAT_4,
            description=(
                f"Off-topic noise in {entry.type} "
                f"({entry.display_ref()}): " + ", ".join(desc_parts)
            ),
            refactor=(
                "move social aside / filler turns to rejected-paths.md or "
                "trim from session record before consolidation"
            ),
            first_index=entry.index,
            last_index=entry.index,
            origin_session=entry.display_ref(),
        ))
    return events


# ---------------------------------------------------------------------------
# Cat 5 — Cross-agent briefing gaps: at role-transitions, work_items present
# in the earlier artefact but missing from the later one.


def detect_briefing_gaps(manifest: Manifest, bodies: dict[int, str],
                         ctx: DetectionContext) -> list[DecayEvent]:
    events: list[DecayEvent] = []
    entries = list(manifest)
    cwd = ""
    for i in range(len(entries) - 1):
        a, b = entries[i], entries[i + 1]
        if a.role == b.role:
            continue
        a_items = set(work_items_extract.extract(
            [bodies.get(a.index, "")], cwd=cwd
        )["work_items"])
        b_items = set(work_items_extract.extract(
            [bodies.get(b.index, "")], cwd=cwd
        )["work_items"])
        dropped = a_items - b_items
        if not dropped:
            continue
        dropped_sorted = sorted(dropped)[:5]
        events.append(DecayEvent(
            timestamp=b.timestamp,
            source_anchor=_entry_anchor(b),
            category=CAT_5,
            description=(
                f"Work items mentioned by {a.role} ({a.display_ref()}) "
                f"not carried into {b.role} {b.type} "
                f"({b.display_ref()}): {', '.join(dropped_sorted)}"
            ),
            refactor=(
                f"propagate {', '.join(dropped_sorted)} into {b.role}'s "
                "next brief or acknowledge as out-of-scope"
            ),
            first_index=a.index,
            last_index=b.index,
            origin_session=a.display_ref(),
        ))
    return events


# ---------------------------------------------------------------------------
# Cat 6 — Terminology drift: Damerau-Levenshtein near-miss vs glossary.


def _damerau_levenshtein(a: str, b: str, cap: int = 3) -> int:
    """Bounded Damerau-Levenshtein distance; returns cap+1 if exceeded."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > cap:
        return cap + 1
    if la == 0:
        return lb
    if lb == 0:
        return la
    matrix = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        matrix[i][0] = i
    for j in range(lb + 1):
        matrix[0][j] = j
    for i in range(1, la + 1):
        row_min = la + lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            v = min(
                matrix[i - 1][j] + 1,
                matrix[i][j - 1] + 1,
                matrix[i - 1][j - 1] + cost,
            )
            if (i > 1 and j > 1 and a[i - 1] == b[j - 2]
                    and a[i - 2] == b[j - 1]):
                v = min(v, matrix[i - 2][j - 2] + 1)
            matrix[i][j] = v
            if v < row_min:
                row_min = v
        if row_min > cap:
            return cap + 1
    return matrix[la][lb]


def _load_glossary(project_dir: Optional[Path]) -> list[str]:
    if project_dir is None:
        return []
    spec_path = project_dir / "spec.md"
    if not spec_path.exists():
        return []
    text = spec_path.read_text(encoding="utf-8", errors="replace")
    head = GLOSSARY_HEADING_RE.search(text)
    if not head:
        return []
    section = text[head.end():]
    next_head = re.search(r"(?im)^#{1,6}\s", section)
    if next_head:
        section = section[:next_head.start()]
    terms: list[str] = []
    for row in section.splitlines():
        m = GLOSSARY_ROW_RE.match(row.strip())
        if not m:
            continue
        term = m.group(1).strip().strip("*").strip()
        if term and term.lower() not in {"term", "---"}:
            terms.append(term)
    return terms


def _interim_glossary(manifest: Manifest,
                      bodies: dict[int, str]) -> list[str]:
    """First-occurrence-as-glossary substitution (C-009 pre-playbook mode)."""
    seen: dict[str, int] = {}
    for entry in manifest:
        body = bodies.get(entry.index, "")
        for term in _extract_terms(body):
            seen.setdefault(term, entry.index)
    return list(seen.keys())


def detect_terminology_drift(manifest: Manifest, bodies: dict[int, str],
                             ctx: DetectionContext) -> list[DecayEvent]:
    if ctx.pre_playbook_only:
        glossary = _interim_glossary(manifest, bodies)
    else:
        glossary = _load_glossary(ctx.project_dir) or ctx.glossary_terms
        if not glossary:
            glossary = _interim_glossary(manifest, bodies)

    # Normalise to lowercase tokens for comparison; preserve display form.
    canonical = {t.lower(): t for t in glossary if len(t) >= 4}
    if not canonical:
        return []

    near_misses: dict[tuple[str, str], list[ManifestEntry]] = {}
    near_miss_lines: dict[tuple[str, str], int] = {}
    canon_lower_set = set(canonical.keys())

    for entry in manifest:
        body = bodies.get(entry.index, "")
        for m in TERM_TOKEN_RE.finditer(body):
            token = m.group(0)
            tl = token.lower()
            if tl in canon_lower_set:
                continue
            for canon_l, canon_display in canonical.items():
                # quick length filter to avoid O(N*M) DL on every token
                if abs(len(tl) - len(canon_l)) > 2:
                    continue
                d = _damerau_levenshtein(tl, canon_l, cap=2)
                if 0 < d <= 2:
                    key = (canon_display, token)
                    near_misses.setdefault(key, []).append(entry)
                    near_miss_lines.setdefault(
                        key, _line_no(body, m.start())
                    )
                    break

    events: list[DecayEvent] = []
    for (canon_display, token), entries in near_misses.items():
        # Cap to one event per (canon, mis-spelling) pair; severity computed
        # over first/last entry.
        first = min(entries, key=lambda e: e.index)
        last = max(entries, key=lambda e: e.index)
        line = near_miss_lines.get((canon_display, token), 1)
        events.append(DecayEvent(
            timestamp=last.timestamp,
            source_anchor=_entry_anchor(first, line),
            category=CAT_6,
            description=(
                f"Possible terminology drift: \"{token}\" appears in "
                f"{len(entries)} artefact(s); glossary canonical is "
                f"\"{canon_display}\""
            ),
            refactor=(
                f"verify \"{token}\" vs spec.md § Glossary \"{canon_display}\"; "
                "correct in source artefact if drift"
            ),
            first_index=first.index,
            last_index=last.index,
            origin_session=first.display_ref(),
        ))
    return events


# ---------------------------------------------------------------------------
# Aggregator


DETECTORS = (
    ("cat1", detect_oscillation),
    ("cat2", detect_lost_decisions),
    ("cat3", detect_over_emphasis),
    ("cat4", detect_off_topic),
    ("cat5", detect_briefing_gaps),
    ("cat6", detect_terminology_drift),
)


def run_all(manifest: Manifest, bodies: dict[int, str],
            ctx: DetectionContext) -> list[DecayEvent]:
    events: list[DecayEvent] = []
    for _, fn in DETECTORS:
        events.extend(fn(manifest, bodies, ctx))
    return events
