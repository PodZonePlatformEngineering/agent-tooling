"""
planning_mirror.py — DB/Qdrant -> ``.planning/`` JSON mirror (PROJ-029
plannerapi, spec §6.2, build item 5/6 — the Business Continuity Plan).

Shape reused deliberately from PROJ-011/T-122 Build A
(``lib/session_log_mirror.py``): the same read/write logic runs twice — an
**interim** pass after every ``planning.*`` write (``PostToolUse``, via the
paired ``hooks/planning-postwrite-mirror.py``) and an **authoritative** full
pass at session close (``SessionEnd``, via
``hooks/planning-session-end-mirror.py``) — so a crash mid-session never
leaves the mirror more than one write stale.

**Direct-connection posture (interim, not permanent — spec §6.2's own
constraint note)**: ``planning.team_lead`` (build item 3,
``005_team_lead_auth.sql``) only has placeholder Neon Auth subs today, so
there is no real team-lead JWT to authenticate through the RLS-gated Data
API. This module connects directly to Postgres instead (service-role
connection string via ``PLANNING_DATABASE_URL``), the same posture build
item 4's ``migrate-from-markdown.py`` already established, and reads are
scoped in code to a single hardcoded team slug (``podzone-apex``) rather
than a JWT-resolved one — matching this build item's own scope boundary
(Hermes's home repo only, not a generalised rollout).

**Materialise is a full re-write every pass, v1** — not a per-row diff.
Build item 4's rehearsal put the whole scoped dataset at ~629 tasks plus a
much smaller handful of programme/project/session/roadmap/work_item rows:
a full re-materialise is a few hundred small JSON writes, fast enough that
building real incremental-delta tracking isn't justified yet. Revisit if
the row count grows enough to matter.

Every public function here degrades soft (catches its own failures, never
raises) except :func:`connect`, which stays loud on a missing/bad
connection so a misconfiguration is diagnosable rather than a silent
zero-write — callers (the hooks) call it inside their own try/except.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

TEAM_SLUG = "podzone-apex"
ORG_SLUG = "podzone"
DATABASE_URL_ENV = "PLANNING_DATABASE_URL"
RECONCILE_SUB_ENV = "PLANNING_RECONCILE_SUB"

# The dedicated headless/automation identity created by 007_service_role.sql
# (PROJ-029/T-021) specifically so tooling-driven writes (this module,
# launch-session's register_session call, etc.) don't get attributed to a
# human's sub. Prior to T-021 this defaulted to one of the three placeholder
# subs seeded by 005_team_lead_auth.sql — those now resolve to real human
# identities post-Phase-3, so they're no longer appropriate for automation.
# See call_rpc's docstring.
DEFAULT_RECONCILE_SUB = "fleet-automation-service"

PENDING_CHANGES_REL = ".planning/pending-changes.jsonl"

# --------------------------------------------------------------------------- #
# JSON helpers
# --------------------------------------------------------------------------- #


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    raise TypeError(f"planning_mirror: not JSON serialisable: {type(obj)!r}")


def _write_json_atomic(path: Path, obj: Any) -> None:
    """Write ``obj`` as pretty JSON, temp-file + atomic rename (T-098
    precedent) so a hook killed mid-write never leaves a half-written mirror
    file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True, default=_json_default)
        fh.write("\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #


def connect(database_url: Optional[str] = None, *, autocommit: bool = True):
    """Open a direct psycopg connection to podzone-planner.

    Raises (deliberately loud) if ``psycopg`` isn't installed or no URL is
    configured (``database_url`` argument or ``PLANNING_DATABASE_URL`` env
    — provisioning that value into the session environment, the same way
    ``PODZONE_QDRANT_APIKEY`` already is, is an operational prerequisite
    outside this build item's scope). Callers materialising/mirroring
    should call this inside their own try/except to degrade soft.
    """
    import psycopg  # imported lazily — hooks that never touch Postgres
                     # (e.g. a plain Qdrant briefs mirror) shouldn't require it

    url = database_url or os.environ.get(DATABASE_URL_ENV)
    if not url:
        raise RuntimeError(
            f"{DATABASE_URL_ENV} not set — cannot connect to podzone-planner "
            "directly (interim posture, spec §6.2: no real team-lead JWT "
            "exists yet, see this module's docstring)."
        )
    return psycopg.connect(url, autocommit=autocommit)


def _rows_as_dicts(cur) -> list[dict]:
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _get_team_id(conn, team_slug: str = TEAM_SLUG) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.id FROM planning.team t "
            "JOIN planning.organisation o ON o.id = t.org_id "
            "WHERE o.slug = %s AND t.slug = %s",
            (ORG_SLUG, team_slug),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(
                f"no planning.team row for org={ORG_SLUG!r} slug={team_slug!r}"
            )
        return row[0]


# --------------------------------------------------------------------------- #
# Materialise — full seed, DB -> .planning/ (spec §6.2.1)
# --------------------------------------------------------------------------- #


def _materialise_programmes(conn, team_id, planning_dir: Path) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM planning.programme WHERE team_id = %s", (team_id,)
        )
        rows = _rows_as_dicts(cur)
    for row in rows:
        _write_json_atomic(planning_dir / "programme" / f"{row['ref']}.json", row)
    return len(rows)


def _materialise_projects(conn, team_id, planning_dir: Path) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.* FROM planning.project p
            JOIN planning.programme pr ON pr.id = p.programme_id
            WHERE pr.team_id = %s
            """,
            (team_id,),
        )
        rows = _rows_as_dicts(cur)
    for row in rows:
        _write_json_atomic(planning_dir / "project" / f"{row['ref']}.json", row)
    return len(rows)


def _materialise_tasks(conn, team_id, planning_dir: Path) -> int:
    # project ref is the human-navigable directory name (spec §6.2's own
    # example: task/PROJ-011/T-081.json) — joined in, then popped back off
    # before writing the row so the file content matches the table exactly.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.*, p.ref AS "_project_ref" FROM planning.task t
            JOIN planning.project p ON p.id = t.project_id
            JOIN planning.programme pr ON pr.id = p.programme_id
            WHERE pr.team_id = %s
            """,
            (team_id,),
        )
        rows = _rows_as_dicts(cur)
    for row in rows:
        project_ref = row.pop("_project_ref")
        _write_json_atomic(
            planning_dir / "task" / project_ref / f"{row['ref']}.json", row
        )
    return len(rows)


def _materialise_sessions(conn, team_id, planning_dir: Path) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM planning.session WHERE team_id = %s", (team_id,)
        )
        rows = _rows_as_dicts(cur)
    for row in rows:
        _write_json_atomic(planning_dir / "session" / f"{row['id']}.json", row)
    return len(rows)


def _materialise_roadmap(conn, team_id, planning_dir: Path) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM planning.roadmap WHERE team_id = %s", (team_id,)
        )
        roadmaps = _rows_as_dicts(cur)
    for row in roadmaps:
        _write_json_atomic(planning_dir / "roadmap" / f"{row['id']}.json", row)

    roadmap_ids = [r["id"] for r in roadmaps]
    work_items: list[dict] = []
    if roadmap_ids:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM planning.work_item WHERE roadmap_id = ANY(%s)",
                (roadmap_ids,),
            )
            work_items = _rows_as_dicts(cur)
    for row in work_items:
        _write_json_atomic(planning_dir / "work_item" / f"{row['id']}.json", row)
    return len(roadmaps), len(work_items)


def materialise(
    repo_dir: str,
    *,
    database_url: Optional[str] = None,
    team_slug: str = TEAM_SLUG,
) -> dict:
    """Full DB -> ``.planning/`` re-seed, scoped to ``team_slug``. Never
    raises — returns ``{"ok", "counts", "error"}``."""
    planning_dir = Path(repo_dir) / ".planning"
    conn = None
    try:
        conn = connect(database_url)
        team_id = _get_team_id(conn, team_slug)
        counts: dict[str, int] = {
            "programme": _materialise_programmes(conn, team_id, planning_dir),
            "project": _materialise_projects(conn, team_id, planning_dir),
            "task": _materialise_tasks(conn, team_id, planning_dir),
            "session": _materialise_sessions(conn, team_id, planning_dir),
        }
        counts["roadmap"], counts["work_item"] = _materialise_roadmap(
            conn, team_id, planning_dir
        )
        return {"ok": True, "counts": counts, "error": None}
    except Exception as exc:
        return {"ok": False, "counts": {}, "error": str(exc)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Qdrant briefs mirror (spec §6.2.3)
# --------------------------------------------------------------------------- #


def mirror_briefs(
    repo_dir: str, *, team: str = ORG_SLUG, api_key: Optional[str] = None
) -> dict:
    """Pull every approved-or-later brief (``EXECUTABLE_STATUSES`` — approved
    / in_progress / complete) for ``team`` and write/refresh
    ``.planning/briefs/{brief_id}.json``. Briefs are additive-only (spec
    §6.2.3: "not edited in place the way task rows are") and comparatively
    rare next to task writes, so — like materialise above — this always
    re-fetches and overwrites the full set rather than tracking a
    since-bookmark; simpler, and still cheap at this volume. Uses
    ``brief_id`` directly as the relative path (e.g.
    ``briefs/podzone/2026-08-08-....json``), matching spec §6.2's own
    ``briefs/{brief_id}.json`` layout literally — brief_ids already look
    like ``org/slug``, so this nests naturally rather than needing an
    escaping scheme. Never raises."""
    planning_dir = Path(repo_dir) / ".planning"
    try:
        from . import brief_substrate, qdrant_http

        written = 0
        offset = None
        while True:
            body: dict[str, Any] = {
                "filter": {
                    "must": [
                        {"key": "point_type", "match": {"value": "brief"}},
                        {"key": "team", "match": {"value": team}},
                        {
                            "key": "status",
                            "match": {"any": list(brief_substrate.EXECUTABLE_STATUSES)},
                        },
                    ]
                },
                "limit": 100,
                "with_payload": True,
                "with_vector": False,
            }
            if offset:
                body["offset"] = offset
            resp = qdrant_http.scroll(
                collection=brief_substrate.COLLECTION, body=body, api_key=api_key
            )
            result = resp.get("result", {}) or {}
            points = result.get("points", []) or []
            for pt in points:
                payload = pt.get("payload", {}) or {}
                brief_id = payload.get("brief_id")
                if not brief_id:
                    continue
                _write_json_atomic(planning_dir / "briefs" / f"{brief_id}.json", payload)
                written += 1
            offset = result.get("next_page_offset")
            if not offset or not points:
                break
        return {"ok": True, "count": written, "error": None}
    except Exception as exc:
        return {"ok": False, "count": 0, "error": str(exc)}


# --------------------------------------------------------------------------- #
# Offline write journal (spec §6.2.4)
# --------------------------------------------------------------------------- #


def queue_pending_change(repo_dir: str, rpc: str, args: dict) -> bool:
    """Append an intended write as an RPC-call record to
    ``.planning/pending-changes.jsonl`` — the offline write path used when a
    live write attempt fails. Never edits a mirrored row file directly (spec
    §6.2.4: "as an append-only journal ... not direct edits to the mirrored
    row files"). Returns ``False`` (never raises) on any I/O failure."""
    try:
        path = Path(repo_dir) / PENDING_CHANGES_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "rpc": rpc,
            "args": args,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=_json_default) + "\n")
        return True
    except Exception:
        return False


def read_pending_changes(repo_dir: str) -> list[dict]:
    path = Path(repo_dir) / PENDING_CHANGES_REL
    if not path.is_file():
        return []
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def clear_pending_changes(repo_dir: str) -> None:
    path = Path(repo_dir) / PENDING_CHANGES_REL
    if path.is_file():
        path.unlink()


# --------------------------------------------------------------------------- #
# Reconcile — replay the journal against the real RPCs (spec §6.2.4)
# --------------------------------------------------------------------------- #

# The 4 write RPCs (006_rpcs.sql), param names with the SQL "p_" prefix
# stripped — queue_pending_change's args are keyed on the stripped name
# (e.g. "task_id", not "p_task_id"); call_rpc accepts either.
_RPC_PARAMS = {
    "close_task": ["task_id", "reason", "status"],
    "supersede_task": ["task_id", "superseded_by", "reason"],
    "register_session": ["brief_id", "agent", "task_ids", "home_repo"],
    "conclude_session": [
        "session_id",
        "status",
        "outcome_note",
        "pr_refs",
        "task_status",
    ],
    "create_task": [
        "project_id",
        "title",
        "summary",
        "owner",
        "status",
        "cc_ref",
    ],
}


def call_rpc(conn, rpc: str, args: dict, *, sub: Optional[str] = None):
    """Call one of the write RPCs directly (bypassing the Data API, same
    direct-connection posture as materialise) under a session-local JWT-claims
    impersonation.

    **Interim posture**: every write RPC checks ``planning.current_team_id()``
    explicitly in its own body (not via RLS — 006_rpcs.sql's own header
    comment explains why), which resolves off the ``request.jwt.claims`` GUC
    via ``planning.jwt_user_id()``. With no real team-lead JWT to present
    for headless/tooling callers, this sets that GUC to the
    ``fleet-automation-service`` sub (PROJ-029/T-021's purpose-built
    headless identity, ``planning_automation`` role) for the duration of the
    call — the same RLS-impersonation-then-rollback shape already proven for
    Academy (PROJ-011/T-055). ``sub`` defaults to ``PLANNING_RECONCILE_SUB``
    env or :data:`DEFAULT_RECONCILE_SUB`.

    Raises on an unknown/unsupported RPC name, a param-count mismatch, or
    whatever the RPC itself raises — reconcile callers are expected to
    catch per-record so one bad line doesn't abort the whole replay.
    """
    if rpc == "raw_sql":
        return _replay_raw_sql(conn, args, sub=sub)
    if rpc not in _RPC_PARAMS:
        raise ValueError(f"call_rpc: unsupported planning RPC {rpc!r}")
    param_names = _RPC_PARAMS[rpc]
    values = [args.get(name) for name in param_names]
    placeholders = ", ".join(["%s"] * len(values))
    sub = sub or os.environ.get(RECONCILE_SUB_ENV, DEFAULT_RECONCILE_SUB)
    # SET LOCAL does not accept bound parameters (parsed before bind, fails
    # with a syntax error on $1) — the claims value is embedded as a safely
    # quoted SQL literal instead. And SET LOCAL's scope is the current
    # transaction: with connect()'s default autocommit=True, each cur.execute()
    # is its own implicit transaction, so a bare SET LOCAL followed by a
    # separate execute() would silently lose the impersonation before the RPC
    # call ever saw it. conn.transaction() forces both statements into one
    # real transaction regardless of the connection's autocommit setting.
    import psycopg.sql  # lazy, mirrors connect()'s lazy psycopg import

    claims_literal = psycopg.sql.Literal(json.dumps({"sub": sub})).as_string(conn)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"SET LOCAL request.jwt.claims = {claims_literal}")
        cur.execute(f"SELECT planning.{rpc}({placeholders})", values)
        return cur.fetchone()


def _replay_raw_sql(conn, args: dict, *, sub: Optional[str] = None):
    """Fallback replay path: when the failed write couldn't be parsed into a
    clean {rpc, args} record (see hooks/planning-postwrite-mirror.py's
    best-effort SQL parser), the queued record instead carries the original
    ``sql``/``sql_statements`` text and is replayed as-is under the same
    impersonation. Less legible in the journal, but never loses a write to a
    parse failure."""
    sub = sub or os.environ.get(RECONCILE_SUB_ENV, DEFAULT_RECONCILE_SUB)
    sql = args.get("sql") or ""
    statements = args.get("sql_statements") or ([sql] if sql else [])
    import psycopg.sql  # lazy, mirrors connect()'s lazy psycopg import

    claims_literal = psycopg.sql.Literal(json.dumps({"sub": sub})).as_string(conn)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"SET LOCAL request.jwt.claims = {claims_literal}")
        result = None
        for stmt in statements:
            cur.execute(stmt)
            try:
                result = cur.fetchone()
            except Exception:
                result = None
        return result


def reconcile(
    repo_dir: str, *, database_url: Optional[str] = None, sub: Optional[str] = None
) -> dict:
    """Replay ``.planning/pending-changes.jsonl`` in order against the real
    RPCs, commit per line so a failure partway through doesn't lose earlier
    successes, then clear the journal and re-materialise. Returns
    ``{"ok", "replayed", "failed": [...], "materialise": {...}, "error"}``.
    Never raises."""
    records = read_pending_changes(repo_dir)
    if not records:
        mat = materialise(repo_dir, database_url=database_url)
        return {"ok": mat["ok"], "replayed": 0, "failed": [], "materialise": mat, "error": None}

    conn = None
    replayed = 0
    failed = []
    try:
        conn = connect(database_url, autocommit=False)
        for record in records:
            try:
                call_rpc(conn, record.get("rpc", ""), record.get("args", {}), sub=sub)
                conn.commit()
                replayed += 1
            except Exception as exc:
                conn.rollback()
                failed.append({"record": record, "error": str(exc)})
    except Exception as exc:
        return {
            "ok": False,
            "replayed": replayed,
            "failed": failed,
            "materialise": {},
            "error": str(exc),
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    if failed:
        # Loud log line, last-write-wins-adjacent posture (spec §6.2.4:
        # "flag as a build-time decision ... rather than something to solve
        # exhaustively here") — clear only the records that replayed clean,
        # leave the rest queued for the next reconcile attempt.
        remaining = [f["record"] for f in failed]
        try:
            path = Path(repo_dir) / PENDING_CHANGES_REL
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                for rec in remaining:
                    fh.write(json.dumps(rec, default=_json_default) + "\n")
        except Exception:
            pass
    else:
        clear_pending_changes(repo_dir)

    mat = materialise(repo_dir, database_url=database_url)
    return {
        "ok": mat["ok"] and not failed,
        "replayed": replayed,
        "failed": failed,
        "materialise": mat,
        "error": None,
    }
