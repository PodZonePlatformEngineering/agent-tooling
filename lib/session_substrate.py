"""
session_substrate.py — operations on the unified `session_substrate` collection.

PROJ-039 Stage 5 (CC-318). The single place that knows the shape of the canonical
`session` point (DTD § 3.1) and the upsert discipline that protects its named
vectors (SD-3-001). Built on :mod:`lib.qdrant_http` (the stdlib write path from
PROJ-033/T-016) so there is no third-party dependency and a missing API key is
loud, never a silent zero-write.

Upsert discipline (the load-bearing invariant, repeated at every call site):
  - **Creation only** → full upsert (:func:`create_session_point`). This is the
    one legitimate ``PUT …/points`` on a `session` point (R-007, Team-Lead side).
  - **Every later write** → ``set_payload`` (per-Stop append, response, rollups,
    event_refs). A full upsert on an existing point would null any named vectors
    the point carries (F-2-008).

Vector policy (PROJ-041/T-002, operator decision 2026-07-11): hooks and the
session-end lifecycle write **payload-only** points — no embedding anywhere on a
hook path (hook hosts generally have no embed endpoint). Qdrant accepts a
vector-less point in a named-vector collection when the upsert carries
``"vector": {}`` (an empty named-vector map — omitting the key entirely is a
400; proven live against cloud Qdrant 2026-07-12). Vectors are added in
retrospect by the PROJ-042 enrichment batch job where a semantic-search
requirement arises. Authoring paths (:func:`create_session_point`, brief
authoring) are **embed-optional**: they embed via ``OLLAMA_HOST`` (an explicit
``ollama_host=`` argument, the env var, or — since the WF friction fix,
2026-08-16 — a ``http://localhost:11434`` default when the env var is unset)
and otherwise (env var explicitly set empty) write vector-less points, saying
so on stderr. An unreachable endpoint also degrades to vector-less rather than
raising (:func:`maybe_embed`).

The point ID is ``uuid5(NAMESPACE_DNS, session_id)`` — identical to
``lib/sessions_upsert.point_id_for`` — so the per-Stop / session-end target is
deterministically addressable without a lookup (DTD § 2.3).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import jsonl_scrape, qdrant_http

COLLECTION = "session_substrate"

EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768

# --------------------------------------------------------------------------- #
# Embed input bounding (PROJ-039/T-027 / CC-326)
# --------------------------------------------------------------------------- #
# nomic-embed-text returns a hard HTTP 500 once its input exceeds the ~2048-token
# context window (empirically a ~6 KB brief: a 7275-char brief failed where a
# 5673-char one squeaked through). The brief/response *embed inputs* are
# unbounded user/agent text, so a large brief silently 500s and its `brief`
# vector is lost — DT-001 semantic recall degrades on exactly the briefs most
# worth recalling. We bound the embed *input* (never the stored text) to a safe
# budget below that ceiling.
#
# Strategy: **head-truncate** (the MVP-acceptable choice in T-027). A brief's
# load-bearing content — title, summary, scope, acceptance — sits at the top, so
# the head carries the signal a `brief`/`response` vector needs; chunk+mean-pool
# would add per-chunk embed round-trips and a semantically muddier pooled vector
# for marginal gain on a dispatch-time path. We have no stdlib tokenizer, so the
# token count is *estimated* from characters with a deliberately conservative
# ratio (dense markdown/code runs ~3 chars/token; prose ~4) — over-counting
# tokens means we truncate early, the safe direction relative to the 500 cliff.
EMBED_TOKEN_BUDGET = 1800             # safe headroom below the ~2048 ceiling
_CHARS_PER_TOKEN = 3                  # conservative (over-counts tokens)
_EMBED_CHAR_BUDGET = EMBED_TOKEN_BUDGET * _CHARS_PER_TOKEN  # 5400 chars


def _bound_embed_input(text: str, *, label: str = "embed") -> str:
    """Head-truncate ``text`` to a safe embed budget for nomic-embed-text.

    Bounds the *embed input* only — the caller still stores the full, unmodified
    text in the payload. Returns ``text`` unchanged when within budget; otherwise
    returns the leading ``_EMBED_CHAR_BUDGET`` characters and emits a one-line
    stderr warning (naming the char + estimated-token counts) so the degradation
    is visible rather than silent. See the module-level note for the strategy and
    why head-truncate is chosen over chunk+mean-pool.
    """
    if len(text) <= _EMBED_CHAR_BUDGET:
        return text
    est_tokens = len(text) // _CHARS_PER_TOKEN
    print(
        f"[session_substrate] {label} embed input bounded: {len(text)} chars "
        f"(~{est_tokens} est. tokens) head-truncated to {_EMBED_CHAR_BUDGET} "
        f"chars (~{EMBED_TOKEN_BUDGET}-token budget) to stay under the "
        f"nomic-embed-text ~2048-token limit; stored text is unchanged.",
        file=sys.stderr,
    )
    return text[:_EMBED_CHAR_BUDGET]


def point_id_for(session_id: str) -> str:
    """Deterministic Qdrant point ID for a session_id (matches sessions_upsert)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, session_id))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def embed_endpoint() -> Optional[str]:
    """The configured embed endpoint (``OLLAMA_HOST`` env), defaulting to
    ``http://localhost:11434`` when unset.

    PROJ-041/T-002 originally made embedding opt-in with no default, on the
    assumption that an explicit unset OLLAMA_HOST reliably signalled "no
    Ollama available here." In practice (WF friction, 2026-08-16) Ollama is
    running locally on essentially every workstation and dispatch sandbox,
    but OLLAMA_HOST is rarely actually exported — so nearly every brief/
    provenance/session write went vector-less by default, silently, with no
    enrichment job ever built to backfill it (PROJ-042 is unbuilt). Defaulting
    to localhost here matches the fallback ``primitives/ollama/embed-text.sh``
    already used. A genuinely embed-less environment can still opt out with
    ``OLLAMA_HOST=""`` or by passing ``ollama_host=""`` explicitly.
    """
    host = os.environ.get("OLLAMA_HOST")
    if host is None:
        return "http://localhost:11434"
    return host or None


def embed_text(text: str, *, ollama_host: Optional[str] = None,
               timeout: float = 30.0) -> list[float]:
    """Embed ``text`` with nomic-embed-text; returns a 768-dim vector.

    Requires a resolvable endpoint — ``ollama_host`` argument or
    :func:`embed_endpoint` (which defaults to ``http://localhost:11434`` when
    ``OLLAMA_HOST`` is unset); raises RuntimeError when both resolve empty
    (``OLLAMA_HOST=""`` explicit opt-out). Stdlib-only (urllib) to match
    qdrant_http — no `requests` dependency. Raises on transport/decoding
    failure — callers wanting graceful degradation on an unreachable endpoint
    should go through :func:`maybe_embed`, which catches that case.
    """
    host = ollama_host or embed_endpoint()
    if not host:
        raise RuntimeError(
            "no embed endpoint configured (set OLLAMA_HOST or pass ollama_host)"
        )
    body = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(
        url=f"{host}/api/embeddings",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        parsed = json.loads(resp.read().decode("utf-8"))
    vec = parsed.get("embedding")
    if not isinstance(vec, list) or not vec:
        raise ValueError("embed endpoint returned no embedding")
    return vec


def maybe_embed(text: str, *, label: str = "embed",
                ollama_host: Optional[str] = None) -> Optional[list[float]]:
    """Embed ``text`` when an endpoint is configured; else None, noted on stderr.

    The embed-optional authoring primitive (PROJ-041/T-002): with an endpoint
    (``ollama_host`` argument or ``OLLAMA_HOST`` env) the input is bounded
    (:func:`_bound_embed_input`) and embedded — an embed failure still raises,
    loud, because a configured endpoint that fails is a real fault. With no
    endpoint the point is written vector-less and this says so on stderr so the
    degradation is visible; the PROJ-042 enrichment job embeds in retrospect.
    """
    host = ollama_host or embed_endpoint()
    if not host:
        print(
            f"[session_substrate] {label}: no embed endpoint configured "
            f"(OLLAMA_HOST unset) — writing vector-less; the PROJ-042 "
            f"enrichment job embeds in retrospect.",
            file=sys.stderr,
        )
        return None
    try:
        return embed_text(_bound_embed_input(text, label=label), ollama_host=host)
    except (urllib.error.URLError, OSError) as exc:
        # host came from embed_endpoint()'s implicit localhost default in most
        # cases now (WF friction, 2026-08-16) — a genuinely unreachable
        # endpoint should degrade to vector-less, not hard-crash the caller,
        # since "unreachable" no longer implies "the operator explicitly
        # pointed us somewhere and it's broken."
        print(
            f"[session_substrate] {label}: embed endpoint {host} unreachable "
            f"({exc}) — writing vector-less; the PROJ-042 enrichment job "
            f"embeds in retrospect.",
            file=sys.stderr,
        )
        return None


# --------------------------------------------------------------------------- #
# Creation (full upsert — the one legitimate PUT …/points, R-007 / SD-3-001)
# --------------------------------------------------------------------------- #

def create_session_point(
    *,
    session_id: str,
    agent: str,
    work_item: str,
    brief_text: str,
    target_agent: Optional[str] = None,
    dispatch_ts: Optional[str] = None,
    brief_id: Optional[str] = None,
    api_key: Optional[str] = None,
    ollama_host: Optional[str] = None,
) -> dict:
    """Create the canonical `session` point carrying the brief (Team-Lead, dispatch).

    Full-point upsert — creation only. **Embed-optional** (PROJ-041/T-002): the
    `brief` named vector is set from ``brief_text`` only when an embed endpoint
    is explicitly configured (``ollama_host`` argument or ``OLLAMA_HOST`` env);
    otherwise the point is written vector-less (``"vector": {}``) with a stderr
    note — team leads on trainer workstations author briefs too. Returns
    ``{"point_id", "ok"}``.

    ``brief_id`` is the optional reverse link to a first-class `briefs`-collection
    point (PROJ-039/T-043): set on the **brief-first** materialise path so a
    session point records which brief it was stood up from (and session-end can
    stamp that brief complete). Absent on the legacy pinned-sid path
    (backwards-compatible — the field is simply omitted).

    When embedding, the *embed input* is head-truncated to a safe token budget
    (:func:`_bound_embed_input`) so a large brief cannot 500 nomic-embed-text and
    lose its `brief` vector; the **full** ``brief_text`` is always stored in the
    payload unchanged.
    """
    pid = point_id_for(session_id)
    dispatch_ts = dispatch_ts or _now_iso()
    brief_vector = maybe_embed(brief_text, label="brief", ollama_host=ollama_host)
    payload = {
        "point_type": "session",
        "session_id": session_id,
        "agent": agent,
        "work_item": work_item,
        "brief": {
            "text": brief_text,
            "dispatch_ts": dispatch_ts,
            "target_agent": target_agent or agent,
        },
        "session_stop": [],
        "response": None,
        "rollup": None,
    }
    if brief_id is not None:
        payload["brief_id"] = brief_id
    # Vector-less shape: an EMPTY named-vector map, not an omitted key — Qdrant
    # 400s on a missing `vector` field (proven live 2026-07-12).
    vector = {"brief": brief_vector} if brief_vector is not None else {}
    qdrant_http.upsert_points(
        [{"id": pid, "vector": vector, "payload": payload}],
        collection=COLLECTION,
        api_key=api_key,
    )
    return {"point_id": pid, "ok": True}


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

def get_session_point(
    session_id: str, *, api_key: Optional[str] = None, with_vector: bool = False
) -> Optional[dict]:
    """Fetch the `session` point (payload, or full result with ``with_vector``)."""
    return qdrant_http.get_point(
        point_id_for(session_id),
        collection=COLLECTION,
        api_key=api_key,
        with_vector=with_vector,
    )


ACTIVE_STATUSES = ("ready", "in_progress", "blocked")


def active_work_items(
    agent: str,
    *,
    statuses: tuple[str, ...] = ACTIVE_STATUSES,
    api_key: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Scroll the agent's active `task`/`work_item` points (§ 2.2 step 3).

    Returns the list of point payloads with ``point_type=task`` for ``agent``
    whose ``status`` is in ``statuses``. Used by the session-start materialise.
    """
    body = {
        "filter": {
            "must": [
                {"key": "point_type", "match": {"value": "task"}},
                {"key": "agent", "match": {"value": agent}},
                {"key": "status", "match": {"any": list(statuses)}},
            ]
        },
        "limit": limit,
        "with_payload": True,
    }
    resp = qdrant_http.scroll(collection=COLLECTION, body=body, api_key=api_key)
    points = resp.get("result", {}).get("points", [])
    return [p.get("payload", {}) for p in points]


def find_session_id_by_work_item(
    agent: str, work_item: str, *, api_key: Optional[str] = None
) -> Optional[str]:
    """Resolve a pre-dispatch session point by agent+work_item (§ 2.2 step 2)."""
    body = {
        "filter": {
            "must": [
                {"key": "point_type", "match": {"value": "session"}},
                {"key": "agent", "match": {"value": agent}},
                {"key": "work_item", "match": {"value": work_item}},
            ]
        },
        "limit": 1,
        "with_payload": True,
    }
    resp = qdrant_http.scroll(collection=COLLECTION, body=body, api_key=api_key)
    points = resp.get("result", {}).get("points", [])
    if points:
        return points[0].get("payload", {}).get("session_id")
    return None


# --------------------------------------------------------------------------- #
# Per-Stop append (set_payload, read-modify-write — R-009 / SD-3-001)
# --------------------------------------------------------------------------- #

def append_session_stop(
    session_id: str,
    entry: dict,
    *,
    api_key: Optional[str] = None,
) -> dict:
    """Append one entry to ``session_stop[]`` via set_payload (never full upsert).

    Read-modify-write: Qdrant has no native array-push, and ``set_payload``
    overwrites only the named key while preserving all named vectors (F-2-008),
    so this is safe where a full upsert would null `brief`/`response`.
    Returns ``{"point_id", "length", "ok"}``.
    """
    pid = point_id_for(session_id)
    existing = qdrant_http.get_point(pid, collection=COLLECTION, api_key=api_key)
    current = []
    if existing and isinstance(existing.get("session_stop"), list):
        current = existing["session_stop"]
    current = list(current) + [entry]
    qdrant_http.set_payload(
        {"session_stop": current}, [pid], collection=COLLECTION, api_key=api_key
    )
    return {"point_id": pid, "length": len(current), "ok": True}


# --------------------------------------------------------------------------- #
# session-end writes (set_payload — R-010..014 / SD-3-001)
# --------------------------------------------------------------------------- #

def upsert_response(
    session_id: str,
    *,
    text: str,
    status_transition: Optional[str] = None,
    event_refs: Optional[list] = None,
    end_ts: Optional[str] = None,
    api_key: Optional[str] = None,
) -> dict:
    """Write the `response` object via set_payload — never a full upsert
    (§ 2.4 step 1).

    Payload-only (PROJ-041/T-002): the former `response` vector patch is gone —
    this runs on the SessionEnd hook path, which never embeds. The full ``text``
    is stored unchanged; the PROJ-042 enrichment job embeds in retrospect."""
    pid = point_id_for(session_id)
    response = {
        "text": text,
        "status_transition": status_transition,
        "event_refs": event_refs or [],
        "end_ts": end_ts or _now_iso(),
    }
    qdrant_http.set_payload(
        {"response": response}, [pid], collection=COLLECTION, api_key=api_key
    )
    return {"point_id": pid, "ok": True}


def attach_event_refs(
    session_id: str, event_refs: list, *, api_key: Optional[str] = None
) -> dict:
    """Attach task-event measurement refs to the session point (R-012)."""
    pid = point_id_for(session_id)
    qdrant_http.set_payload(
        {"event_refs": event_refs}, [pid], collection=COLLECTION, api_key=api_key
    )
    return {"point_id": pid, "ok": True}


def compute_rollup(jsonl_path: str | Path) -> dict:
    """Derive ``{tool_usage, cost_tokens}`` from the session JSONL (R-013/R-014).

    Uses the canonical :func:`lib.jsonl_scrape.scrape` aggregation so the numbers
    reconcile exactly with a fresh re-scrape (DT-006). ``cost_tokens`` mirrors the
    per-model bucket; ``tool_usage`` is per-tool invocation counts. Both are
    unvectorised (Q-002).
    """
    scraped = jsonl_scrape.scrape(jsonl_path)
    return {
        "tool_usage": scraped.get("tool_usage", {}),
        "cost_tokens": scraped.get("model_usage", {}),
    }


def attach_rollup(
    session_id: str,
    rollup: dict,
    *,
    api_key: Optional[str] = None,
    tooling_version: Optional[str] = None,
) -> dict:
    """Attach ``rollup = {tool_usage, cost_tokens}`` via set_payload (unvectorised).

    ``tooling_version`` (PROJ-039/T-055), when given, is stamped onto the point as a
    top-level ``tooling`` field alongside the rollup — the telemetry-payload half of
    "log entries and git PRs record the tooling version" (absent when not given, so
    callers that don't pass it see no behaviour change)."""
    pid = point_id_for(session_id)
    payload = {"rollup": rollup}
    if tooling_version:
        payload["tooling"] = tooling_version
    qdrant_http.set_payload(
        payload, [pid], collection=COLLECTION, api_key=api_key
    )
    return {"point_id": pid, "ok": True}
