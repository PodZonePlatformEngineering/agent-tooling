"""Markdown report writer (per spec § R-006, R-007, R-008, § 11.1).

Layout:
  # PROJ-038 Decay Report — <design-name>

  _Generated 2026-XX-XX UTC; manifest <path>; detector <version>_
  _<pre-playbook header lines per C-009 if any>_

  ## Summary
  - Total events: N
  - High severity: N
  - Medium severity: N
  - Low severity: N
  - Noise budget: <low|medium|high>

  ## Cat 1 — Oscillation
  - **EVENT-id** — severity — description
    - Source: `<anchor>`
    - Recommended refactor: ...

  ...

  ## Coverage gaps (BL-NEW-E)
  - Cat 2: free-form lost decisions (semantic) — NOT scanned
  ...
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .anchors import render_anchor_markdown
from .events import CATEGORIES, DecayEvent
from .manifest import Manifest


COVERAGE_GAPS = [
    "**Cat 2** — free-form lost decisions (semantic) NOT scanned "
    "(parked BL-NEW-E)",
    "**Cat 3** — weight / propagation analysis NOT scanned "
    "(parked BL-NEW-E)",
    "**Cat 4** — substantive on-topic-grammar tangents NOT scanned "
    "(parked BL-NEW-E)",
    "**Cat 6** — conceptual / definitional drift NOT scanned "
    "(parked BL-NEW-E)",
]

NOISE_BUDGET_LEVELS = {"low": 0, "medium": 1, "high": 2}
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


def _below_budget(event_severity: str, budget: str) -> bool:
    if budget == "low":
        return False  # show all
    if budget == "medium":
        return event_severity == "low"
    if budget == "high":
        return event_severity in ("low", "medium")
    return False


def _sort_key(e: DecayEvent) -> tuple:
    return (
        CATEGORIES.index(e.category),
        -SEVERITY_RANK.get(e.severity, 0),
        e.first_index,
        e.event_id,
    )


def render_report(events: Iterable[DecayEvent],
                  manifest: Manifest,
                  *,
                  design_name: str = "",
                  noise_budget: str = "low",
                  pre_playbook_entries: Optional[list[str]] = None,
                  generated_at: Optional[datetime] = None,
                  qdrant_unreachable: bool = False) -> str:
    """Render the full Markdown report."""
    events = sorted(events, key=_sort_key)
    pre_playbook_entries = pre_playbook_entries or []
    generated_at = generated_at or datetime.now(timezone.utc)

    high = sum(1 for e in events if e.severity == "high")
    medium = sum(1 for e in events if e.severity == "medium")
    low = sum(1 for e in events if e.severity == "low")
    total = len(events)

    out: list[str] = []
    out.append(f"# PROJ-038 Decay Report — {design_name or 'unnamed design'}")
    out.append("")
    out.append(
        f"_Generated {generated_at.strftime('%Y-%m-%d %H:%M:%S')} UTC; "
        f"manifest `{manifest.source_path}`_"
    )
    if pre_playbook_entries:
        out.append("")
        out.append("**Degraded mode (C-009 pre-playbook):**")
        for ref in pre_playbook_entries[:20]:
            out.append(f"- pre-playbook: `{ref}`")
        if len(pre_playbook_entries) > 20:
            out.append(f"- … and {len(pre_playbook_entries) - 20} more")
    if qdrant_unreachable:
        out.append("")
        out.append(
            "**Qdrant unreachable:** one or more `qdrant_*` manifest entries "
            "could not be loaded (no API key, or fetch failed). Their bodies "
            "were treated as empty for this run."
        )
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append(f"- Total events: {total}")
    out.append(f"- High severity: {high}")
    out.append(f"- Medium severity: {medium}")
    out.append(f"- Low severity: {low}")
    out.append(f"- Noise budget: `{noise_budget}`")
    out.append("")

    by_cat: dict[str, list[DecayEvent]] = {c: [] for c in CATEGORIES}
    for e in events:
        by_cat[e.category].append(e)

    for cat in CATEGORIES:
        cat_events = by_cat.get(cat, [])
        out.append(f"## {cat}")
        out.append("")
        if not cat_events:
            out.append("_No events detected._")
            out.append("")
            continue
        suppressed: list[DecayEvent] = []
        shown_count = 0
        for e in cat_events:
            if _below_budget(e.severity, noise_budget):
                suppressed.append(e)
                continue
            shown_count += 1
            out.append(
                f"- **{e.event_id}** — `{e.severity}` — {e.description}"
            )
            out.append(f"  - Source: {render_anchor_markdown(e.source_anchor)}")
            out.append(f"  - Timestamp: `{e.timestamp}`")
            out.append(f"  - Recommended refactor: {e.refactor}")
            if not e.origin_traced:
                out.append("  - ⚠️ origin-untraced (R-014 fallback)")
        if suppressed:
            out.append(
                f"- _{len(suppressed)} sub-threshold event(s) suppressed by "
                f"noise budget `{noise_budget}`_"
            )
        if shown_count == 0 and not suppressed:
            out.append("_No events detected._")
        out.append("")

    out.append("## Coverage gaps (BL-NEW-E)")
    out.append("")
    out.append("Per spec.md § 11.1, the following semantic sub-cases were "
               "NOT scanned in this v1 run:")
    out.append("")
    for gap in COVERAGE_GAPS:
        out.append(f"- {gap}")
    out.append("")

    out.append("---")
    out.append("")
    out.append("_Detector: PROJ-038 decay-detector v1 (structural-only, "
               "zero-LLM per C-006/C-008)._")
    out.append("")
    return "\n".join(out)


def write_report(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
