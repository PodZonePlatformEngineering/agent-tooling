"""
cross_cutting.py — the AC-008 / DT-012 cross-cutting query.

Answers **"what did the agent do vs what the brief asked?"** for one session by
joining the two sides of the record that now co-locate on the *same* cloud Qdrant
instance (PROJ-033/T-019):

  * the **tasking** side — `session_substrate`, the canonical `session` point
    carrying `brief` (+ optional `response`, `rollup`, `session_stop[]`); and
  * the **observability** side — `claude_session_telemetry` (CST), the raw hook
    events (`PreToolUse`/`PostToolUse`/`Stop`/…) keyed by `session_id`.

Because both reads target ``CLOUD_QDRANT_URL`` the join is **single-instance**:
there is no cross-instance application join. That cross-instance join was the
F-2-007 blocker that made AC-008 unachievable back in iteration 1 (CST lived on
agentsonly); PROJ-033/T-019 co-located CST on cloud and cleared it. This module
*records the instance each read touched* and exposes it on the result so the
single-instance property is assertable (DT-012 pass criterion: "no second-instance
request in the trace").

Stdlib-only, built on :mod:`lib.qdrant_http` — a missing API key is loud, never a
silent empty answer.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from . import qdrant_http, session_substrate
from .cst_cleanup import CST_COLLECTION

SUBSTRATE_COLLECTION = session_substrate.COLLECTION

# Raw tool events carry a tool_name worth tallying; the rest are lifecycle events.
_TOOL_EVENT_TYPES = ("PreToolUse", "PostToolUse")


@dataclass
class CrossCuttingResult:
    """The joined brief-vs-activity record for one session (DT-012)."""

    session_id: str
    # tasking side (session_substrate)
    found: bool = False
    agent: Optional[str] = None
    work_item: Optional[str] = None
    brief_text: Optional[str] = None
    brief_dispatch_ts: Optional[str] = None
    response_text: Optional[str] = None
    status_transition: Optional[str] = None
    response_end_ts: Optional[str] = None
    rollup: Optional[dict] = None
    # observability side (CST)
    cst_event_count: int = 0
    event_type_counts: dict = field(default_factory=dict)
    tool_use_counts: dict = field(default_factory=dict)
    activity_first_ts: Optional[str] = None
    activity_last_ts: Optional[str] = None
    repositories: list = field(default_factory=list)
    # provenance — the set of Qdrant base URLs touched by the whole query
    instances_touched: list = field(default_factory=list)

    @property
    def single_instance(self) -> bool:
        """True iff every read in the join hit exactly one Qdrant instance."""
        return len(self.instances_touched) == 1

    def render(self) -> str:
        """Human-readable 'brief asked vs agent did' comparison."""
        lines: list[str] = []
        lines.append(f"=== Cross-cutting query — session {self.session_id} ===")
        lines.append(
            f"single-instance: {self.single_instance} "
            f"(instances touched: {', '.join(self.instances_touched) or 'none'})"
        )
        lines.append("")
        lines.append("-- WHAT THE BRIEF ASKED (session_substrate) --")
        if not self.found:
            lines.append("  (no session point in session_substrate for this id)")
        else:
            lines.append(f"  agent:      {self.agent}")
            lines.append(f"  work_item:  {self.work_item}")
            lines.append(f"  dispatched: {self.brief_dispatch_ts}")
            lines.append(f"  brief:      {_clip(self.brief_text)}")
        lines.append("")
        lines.append("-- WHAT THE AGENT DID (claude_session_telemetry / CST) --")
        lines.append(f"  observed events: {self.cst_event_count}")
        if self.activity_first_ts:
            lines.append(
                f"  activity span:   {self.activity_first_ts} → {self.activity_last_ts}"
            )
        if self.event_type_counts:
            ev = ", ".join(f"{k}={v}" for k, v in sorted(self.event_type_counts.items()))
            lines.append(f"  event types:     {ev}")
        if self.tool_use_counts:
            tu = ", ".join(
                f"{k}={v}" for k, v in sorted(
                    self.tool_use_counts.items(), key=lambda kv: -kv[1]
                )
            )
            lines.append(f"  tools used:      {tu}")
        if self.repositories:
            lines.append(f"  repositories:    {', '.join(self.repositories)}")
        lines.append("")
        lines.append("-- WHAT THE AGENT REPORTED (response) --")
        if self.response_text:
            lines.append(f"  status:   {self.status_transition}")
            lines.append(f"  ended:    {self.response_end_ts}")
            lines.append(f"  response: {_clip(self.response_text)}")
        else:
            lines.append("  (no response captured on the session point)")
        return "\n".join(lines)


def _clip(text: Optional[str], limit: int = 280) -> str:
    if not text:
        return "(none)"
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + " …"


def _repo_name(repo) -> Optional[str]:
    """CST ``repository`` is sometimes a str, sometimes a dict — normalise to a name."""
    if isinstance(repo, str):
        return repo or None
    if isinstance(repo, dict):
        return repo.get("name") or repo.get("repository") or repo.get("url")
    return None


def _base_url(qdrant_url: str) -> str:
    """Normalise a Qdrant URL to its instance origin (scheme://host[:port])."""
    return qdrant_url.rstrip("/")


def cross_cutting_query(
    session_id: str,
    *,
    api_key: Optional[str] = None,
    qdrant_url: str = qdrant_http.CLOUD_QDRANT_URL,
    cst_collection: str = CST_COLLECTION,
    cst_limit: int = 20000,
) -> CrossCuttingResult:
    """Join brief (session_substrate) + activity (CST) for ``session_id``.

    Both reads target ``qdrant_url`` — the join is single-instance by construction;
    :attr:`CrossCuttingResult.instances_touched` records the origins actually hit so
    a caller (or DT-012) can assert it.
    """
    result = CrossCuttingResult(session_id=session_id)
    touched: list[str] = []

    # --- tasking side: the canonical session point (brief + response) ---
    point = qdrant_http.get_point(
        session_substrate.point_id_for(session_id),
        collection=SUBSTRATE_COLLECTION,
        qdrant_url=qdrant_url,
        api_key=api_key,
    )
    touched.append(_base_url(qdrant_url))
    # get_point(with_vector=False) returns the payload dict directly (or None on 404).
    payload = point
    if payload:
        result.found = True
        result.agent = payload.get("agent")
        result.work_item = payload.get("work_item")
        brief = payload.get("brief") or {}
        result.brief_text = brief.get("text")
        result.brief_dispatch_ts = brief.get("dispatch_ts")
        response = payload.get("response") or {}
        if isinstance(response, dict):
            result.response_text = response.get("text")
            result.status_transition = response.get("status_transition")
            result.response_end_ts = response.get("end_ts")
        result.rollup = payload.get("rollup")

    # --- observability side: CST events for this session ---
    body = {
        "filter": {"must": [{"key": "session_id", "match": {"value": session_id}}]},
        "limit": cst_limit,
        "with_payload": True,
        "with_vector": False,
    }
    resp = qdrant_http.scroll(
        collection=cst_collection, body=body, qdrant_url=qdrant_url, api_key=api_key
    )
    touched.append(_base_url(qdrant_url))
    events = resp.get("result", {}).get("points", [])

    ev_counts: Counter = Counter()
    tool_counts: Counter = Counter()
    repos: set = set()
    timestamps: list[str] = []
    for ev in events:
        epl = ev.get("payload") or {}
        etype = epl.get("event_type")
        ev_counts[etype] += 1
        if etype in _TOOL_EVENT_TYPES and epl.get("tool_name"):
            tool_counts[epl["tool_name"]] += 1
        repo = _repo_name(epl.get("repository"))
        if repo:
            repos.add(repo)
        if epl.get("timestamp"):
            timestamps.append(epl["timestamp"])

    result.cst_event_count = len(events)
    result.event_type_counts = dict(ev_counts)
    result.tool_use_counts = dict(tool_counts)
    result.repositories = sorted(repos)
    if timestamps:
        timestamps.sort()
        result.activity_first_ts = timestamps[0]
        result.activity_last_ts = timestamps[-1]

    # de-duplicate while preserving the single-instance assertion semantics
    result.instances_touched = sorted(set(touched))
    return result
