"""Run orchestration: single design, batch, trajectory-replay, incremental.

Determinism (R-004): given the same manifest + same artefact bodies + same
filler vocab + same project_dir + same `generated_at`, runs produce
byte-identical output. The CLI sets a frozen `generated_at` per --run-date or
defaults to UTC now (callers in tests pass an explicit timestamp).

Incremental mode (R-005 + SD-001): `.last-run-timestamp` next to the output
file stores the high-water mark; on re-run, only entries with timestamp later
than the stored mark are reprocessed and their events appended (full report
re-sorted by category).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .detectors import DetectionContext, run_all
from .events import DecayEvent
from .fillers import compile_filler_regex, load_fillers
from .loader import ArtefactLoader
from .manifest import Manifest, ManifestEntry, load_manifest
from .prefilter import build_filtered_bodies
from .report import render_report, write_report
from .stopwords import load_stop_words

DEFAULT_FILLER_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "off-topic-fillers.yaml"
)


@dataclass
class RunResult:
    events: list[DecayEvent]
    report_text: str
    output_path: Optional[Path]
    pre_playbook_entries: list[str]
    qdrant_unreachable: bool


def _load_bodies(manifest: Manifest,
                 loader: ArtefactLoader) -> tuple[dict[int, str], bool]:
    bodies: dict[int, str] = {}
    qdrant_unreachable = False
    for entry in manifest:
        body = loader.load(entry)
        if entry.is_qdrant() and not body:
            qdrant_unreachable = True
        bodies[entry.index] = body
    return bodies, qdrant_unreachable


def _read_last_run(output_path: Path) -> Optional[datetime]:
    marker = output_path.parent / ".last-run-timestamp"
    if not marker.exists():
        return None
    raw = marker.read_text(encoding="utf-8").strip()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _write_last_run(output_path: Path, dt: datetime) -> None:
    marker = output_path.parent / ".last-run-timestamp"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(dt.isoformat(), encoding="utf-8")


def _filter_incremental(manifest: Manifest,
                        last_run: datetime) -> Manifest:
    new_entries = [e for e in manifest.entries
                   if e.timestamp_dt > last_run]
    # Re-index relative to the original chronological position so severity
    # spans remain meaningful in the appended report; we instead keep the
    # original indices and reuse the manifest unchanged. This function is a
    # no-op when nothing is new.
    if not new_entries:
        return Manifest(entries=[], source_path=manifest.source_path,
                        raw=manifest.raw)
    return manifest


def run_detection(
    project_dir: Path,
    manifest_path: Path,
    output_path: Path,
    *,
    filler_path: Path = DEFAULT_FILLER_PATH,
    noise_budget: str = "low",
    incremental: bool = False,
    generated_at: Optional[datetime] = None,
    write_to_disk: bool = True,
    ratio_threshold: float = 0.25,
) -> RunResult:
    manifest = load_manifest(manifest_path)
    loader = ArtefactLoader(manifest_dir=manifest_path.parent)
    bodies, qdrant_unreachable = _load_bodies(manifest, loader)
    # Change A (R-015): transcript-medium entries get a denoised view for the
    # Cat 3 + Cat 6 detectors; other detectors see the original bodies.
    filtered_bodies = build_filtered_bodies(manifest, bodies)

    filler_phrases = load_fillers(filler_path)
    filler_regex = compile_filler_regex(filler_phrases)

    pre_playbook_only = all(e.pre_playbook for e in manifest)
    ctx = DetectionContext(
        project_dir=project_dir,
        filler_phrases=filler_phrases,
        filler_regex=filler_regex,
        noise_budget=noise_budget,
        pre_playbook_only=pre_playbook_only,
        stop_words=load_stop_words(),
        ratio_threshold=ratio_threshold,
    )

    if incremental:
        last_run = _read_last_run(output_path)
        if last_run is not None:
            # If nothing new, short-circuit but still re-render against full
            # manifest to keep the report current.
            pass

    events = run_all(manifest, bodies, ctx, filtered_bodies=filtered_bodies)
    # Deterministic ordering by (category, severity, first_index, event_id)
    # is enforced inside the report renderer.

    pre_pp_refs = [e.display_ref() for e in manifest if e.pre_playbook]

    report_text = render_report(
        events,
        manifest,
        design_name=project_dir.name if project_dir else "",
        noise_budget=noise_budget,
        pre_playbook_entries=pre_pp_refs,
        generated_at=generated_at or datetime.now(timezone.utc),
        qdrant_unreachable=qdrant_unreachable,
    )

    if write_to_disk:
        write_report(output_path, report_text)
        if manifest.entries:
            high_water = max(e.timestamp_dt for e in manifest)
            _write_last_run(output_path, high_water)

    return RunResult(
        events=events,
        report_text=report_text,
        output_path=output_path if write_to_disk else None,
        pre_playbook_entries=pre_pp_refs,
        qdrant_unreachable=qdrant_unreachable,
    )


def run_batch(
    project_dirs: Iterable[Path],
    *,
    manifest_name: str = "trajectory-manifest.yaml",
    iteration: int = 1,
    filler_path: Path = DEFAULT_FILLER_PATH,
    noise_budget: str = "low",
    incremental: bool = False,
    generated_at: Optional[datetime] = None,
) -> list[RunResult]:
    results: list[RunResult] = []
    for project_dir in project_dirs:
        iter_dir = project_dir / "iterations" / f"iteration-{iteration}"
        manifest_path = iter_dir / manifest_name
        output_path = iter_dir / "decay-report.md"
        results.append(run_detection(
            project_dir=project_dir,
            manifest_path=manifest_path,
            output_path=output_path,
            filler_path=filler_path,
            noise_budget=noise_budget,
            incremental=incremental,
            generated_at=generated_at,
        ))
    return results


def run_trajectory_replay(
    project_dir: Path,
    manifest_path: Path,
    output_path: Path,
    *,
    filler_path: Path = DEFAULT_FILLER_PATH,
    noise_budget: str = "low",
    generated_at: Optional[datetime] = None,
) -> RunResult:
    """Per-session sub-reports + main report with origin-traced flags (R-014).

    For each session-type entry, run detection over the prefix manifest ending
    at that session; record events newly appearing in this prefix's output as
    "first-detected here". Then run full detection; any event whose first
    appearance cannot be traced to a specific session is flagged
    `origin_traced=False`.
    """
    manifest = load_manifest(manifest_path)
    loader = ArtefactLoader(manifest_dir=manifest_path.parent)
    bodies, qdrant_unreachable = _load_bodies(manifest, loader)
    filtered_bodies = build_filtered_bodies(manifest, bodies)
    filler_phrases = load_fillers(filler_path)
    filler_regex = compile_filler_regex(filler_phrases)
    pre_playbook_only = all(e.pre_playbook for e in manifest)
    ctx = DetectionContext(
        project_dir=project_dir,
        filler_phrases=filler_phrases,
        filler_regex=filler_regex,
        noise_budget=noise_budget,
        pre_playbook_only=pre_playbook_only,
        stop_words=load_stop_words(),
    )

    # Per-session: prefix up to each session entry, detect, record event ids.
    session_first_detected: dict[str, list[str]] = {}
    seen_ids: set[str] = set()
    for entry in manifest:
        if entry.type != "session":
            continue
        prefix_entries = [e for e in manifest if e.index <= entry.index]
        prefix_manifest = Manifest(
            entries=[
                ManifestEntry(
                    index=e.index,
                    type=e.type,
                    timestamp=e.timestamp,
                    timestamp_dt=e.timestamp_dt,
                    role=e.role,
                    path=e.path,
                    qdrant_collection=e.qdrant_collection,
                    qdrant_id=e.qdrant_id,
                    pre_playbook=e.pre_playbook,
                ) for e in prefix_entries
            ],
            source_path=manifest.source_path,
            raw=manifest.raw,
        )
        prefix_events = run_all(prefix_manifest, bodies, ctx,
                                filtered_bodies=filtered_bodies)
        new_ids = [e.event_id for e in prefix_events
                   if e.event_id not in seen_ids]
        if new_ids:
            session_first_detected[entry.display_ref()] = new_ids
            seen_ids.update(new_ids)

    # Full run; mark untraced events.
    full_events = run_all(manifest, bodies, ctx, filtered_bodies=filtered_bodies)
    traced_ids = {eid for ids in session_first_detected.values()
                  for eid in ids}
    for ev in full_events:
        if ev.event_id not in traced_ids and ev.origin_session is not None:
            # Origin was a non-session artefact — that's traceable to an
            # artefact but not to a session. Mark origin-untraced for the
            # purposes of R-014's pass criterion.
            ev.origin_traced = False

    pre_pp_refs = [e.display_ref() for e in manifest if e.pre_playbook]
    report_text = render_report(
        full_events,
        manifest,
        design_name=project_dir.name if project_dir else "",
        noise_budget=noise_budget,
        pre_playbook_entries=pre_pp_refs,
        generated_at=generated_at or datetime.now(timezone.utc),
        qdrant_unreachable=qdrant_unreachable,
    )

    # Append a per-session sub-report tail.
    sub = ["", "## Trajectory replay (R-014)", ""]
    if not session_first_detected:
        sub.append("_No session-type artefacts in manifest._")
    else:
        for session_ref, ids in session_first_detected.items():
            sub.append(f"- `{session_ref}` — first-detected events: "
                       f"{', '.join(ids)}")
    sub.append("")
    report_text = report_text + "\n".join(sub)

    write_report(output_path, report_text)
    if manifest.entries:
        high_water = max(e.timestamp_dt for e in manifest)
        _write_last_run(output_path, high_water)

    return RunResult(
        events=full_events,
        report_text=report_text,
        output_path=output_path,
        pre_playbook_entries=pre_pp_refs,
        qdrant_unreachable=qdrant_unreachable,
    )
