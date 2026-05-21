"""
work_items_extract.py — extract canonical work item and project IDs from a
session's text corpus.

Output is two lists of canonical strings (`PROJ-XXX/T-XXX[suffix]` for
work_items, `PROJ-XXX` for projects), deduped, alphabetically sorted, bounded
to 20 entries each. Ranking before truncation is by mention frequency
descending, then alphabetically; the kept subset is then sorted alphabetically
for stable downstream display.

Sources:
  - canonical/lowercase mentions in any string in the corpus
  - bare `PROJ-XXX` mentions
  - cwd basename slug (`hephaestus-2026-05-21-proj034-foundation` → PROJ-034)
"""

from __future__ import annotations

import os
import re
from collections import Counter

# Per-string scan cap to keep the regex pass cheap on pathological inputs.
_PER_STRING_BYTES = 4096

# Hard cap on output list size.
_MAX_ENTRIES = 20

# Canonical work item: PROJ-XXX/T-XXX[a-z]?, with flexible separators and
# digit-width, case-insensitive. `\b` anchors prevent matches like
# `PROJ-12345` (next char is a digit → no word boundary).
_WORK_ITEM_RE = re.compile(
    r"\bproj-?(\d{1,3})[\s/_-]*t-?(\d{1,3}[a-z]?)\b",
    re.IGNORECASE,
)

# Bare project. Will also match the project prefix of a work item; we dedupe
# via Counter so the double-count is benign (and the work-item pass adds the
# project anyway).
_BARE_PROJECT_RE = re.compile(r"\bproj-?(\d{1,3})\b", re.IGNORECASE)

# cwd slug parser: looks for `proj<digits>` anywhere in the basename.
_CWD_SLUG_RE = re.compile(r"proj-?(\d{1,3})", re.IGNORECASE)


def _normalise_task(raw: str) -> str:
    m = re.match(r"(\d{1,3})([a-z]?)", raw.lower())
    if not m:
        return raw.lower()
    return f"{int(m.group(1)):03d}{m.group(2)}"


def _normalise_project(raw: str) -> str:
    return f"PROJ-{int(raw):03d}"


def _top_n(counter: Counter, n: int) -> list[str]:
    # Frequency desc, then alphabetical for ties; keep n; then sort alpha.
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return sorted(k for k, _ in ranked[:n])


def extract(text_corpus: list[str], cwd: str = "") -> dict:
    """Extract work_items + projects from a session corpus.

    text_corpus: every user/assistant text block + tool input/output text
                 already collected by the caller.
    cwd:         session working dir; basename is parsed for a project slug.
    """
    work_freq: Counter[str] = Counter()
    project_freq: Counter[str] = Counter()

    for s in text_corpus:
        if not isinstance(s, str) or not s:
            continue
        chunk = s[:_PER_STRING_BYTES]

        for m in _WORK_ITEM_RE.finditer(chunk):
            wid = f"{_normalise_project(m.group(1))}/T-{_normalise_task(m.group(2))}"
            work_freq[wid] += 1

        for m in _BARE_PROJECT_RE.finditer(chunk):
            project_freq[_normalise_project(m.group(1))] += 1

    if cwd:
        base = os.path.basename(cwd.rstrip("/"))
        for m in _CWD_SLUG_RE.finditer(base):
            project_freq[_normalise_project(m.group(1))] += 1

    # Every work item implies its project.
    for wid, count in work_freq.items():
        proj = wid.split("/", 1)[0]
        project_freq[proj] += count

    return {
        "work_items": _top_n(work_freq, _MAX_ENTRIES),
        "projects": _top_n(project_freq, _MAX_ENTRIES),
    }
