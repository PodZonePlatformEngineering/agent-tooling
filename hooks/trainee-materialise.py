#!/usr/bin/env python3
"""
trainee-materialise.py — SessionStart operational-brief materialise for the
trainee runtime (PROJ-011/T-030, trainee-repo template v3).

Config-driven from the committed ``training-config.yaml`` (R2-2): reads the
repo's OPERATIONAL brief — point ``uuid5(operational_brief_id)`` in
``training_briefs`` — and, when one is ``active``, injects it as SessionStart
context and appends the runtime sid to the brief point's ``session_ids[]``.
This replaces the v2 first-prompt-brief path for the trainee role: the brief
id lives in the config file, not the first prompt.

OFFLINE-FIRST (R2-4): the briefing files (AGENTS.md / trainee-brief.md), not
this hook, carry the agent's operating knowledge. Qdrant unreachable, config
unfilled, or no active brief are all NORMAL — the hook logs, says at most one
quiet line, and exits 0. It never blocks a session and never touches any
fleet collection (the config loader cannot name one — R2-3).

``--apply <revision>`` mode (agent-invoked, in-session, ONCE the trainee has
said yes — PROJ-011/T-121 #14): the HOOK writes the brief's ``files`` to disk
and then writes the ack. The tutor never reads an operational instruction and
rewrites its own persona/protocol files mid-conversation — a beginner should
never see that operation. Alex's whole part is: relay the one-line summary,
ask for approval, run this command.

``--ack <revision>`` mode: the legacy prose-only round-trip close — writes the
``message_type: ack`` write-back point to ``training_briefs`` so the trainer
sees the instruction landed. Idempotent per (session, revision). ``--apply``
writes the same ack itself; ``--ack`` remains for briefs that carry no
``files`` (a config change the trainee performs by hand, say).

**Approval semantics are unchanged.** The trainee still consents in-session
before anything is written, and the ack is still what the trainer sees. Only
*where the write happens* moved: conversation → hook.

Always exits 0 in hook mode. Reads stdin JSON: ``session_id``, ``cwd``.
Tested by tests/proj011/test_trainee_routing.py.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

QDRANT_TIMEOUT = 6.0  # SessionStart must stay snappy — offline is normal


def _log(message: str, session_id: str = "") -> None:
    try:
        from lib.runtime_log import log_library
        log_library("training", message, session_id=session_id or None)
    except Exception:
        pass


def _emit_context(text: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }))


def _repo_root(cwd: str) -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR") or cwd or os.getcwd()


def fetch_operational_brief(cfg: dict, *, session_id: str) -> dict | None:
    """The active operational brief payload, or ``None`` (absent/paused/
    retired). Appends the runtime sid to ``session_ids[]`` on a hit."""
    from lib import qdrant_http, training_config, training_substrate

    brief_id = cfg["operational_brief_id"]
    point_id = training_substrate.brief_point_id(brief_id)
    kwargs = training_config.qdrant_kwargs(cfg)
    payload = qdrant_http.get_point(
        point_id, collection=cfg["briefs_collection"],
        timeout=QDRANT_TIMEOUT, **kwargs)
    if not payload or payload.get("status") != "active":
        return None
    sids = list(payload.get("session_ids") or [])
    if session_id and session_id not in sids:
        sids.append(session_id)
        try:
            qdrant_http.set_payload(
                {"session_ids": sids,
                 "updated_at": training_substrate.now_iso()},
                [point_id], collection=cfg["briefs_collection"],
                timeout=QDRANT_TIMEOUT, **kwargs)
        except Exception as exc:
            _log(f"session_ids append failed (soft): {exc}", session_id)
    return payload


def brief_files(brief: dict) -> list:
    """The brief's normalised ``files`` list — [] for a prose-only brief.

    A malformed ``files`` payload is NOT fatal here: the trainee's session is
    not the place to discover a trainer's authoring error. Log and treat the
    brief as prose-only.
    """
    from lib import training_substrate
    try:
        return training_substrate.normalise_brief_files(brief.get("files"))
    except ValueError as exc:
        _log(f"operational brief files rejected (treated as prose-only): {exc}")
        return []


def resolve_target(repo_root: str, rel_path: str) -> Path:
    """Absolute path for a brief file, proven to stay inside the repo.

    ``normalise_brief_files`` already rejects the obvious escapes; this is the
    resolved-path backstop that also catches a symlinked directory pointing
    out of the repo. Raises ValueError if the target escapes.
    """
    root = Path(repo_root).resolve()
    target = (root / rel_path).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"{rel_path!r} resolves outside the repo — refusing to write")
    return target


def files_already_applied(repo_root: str, files: list) -> bool:
    """True when every brief file is already on disk with exactly this content.

    This is the re-prompt guard, and it is deliberately stateless: a recurring
    brief stays ``active`` forever, so without it every later session would ask
    the trainee to approve a change that has already landed. Comparing content
    needs no local state file and survives a fresh clone.
    """
    if not files:
        return False
    for spec in files:
        try:
            target = resolve_target(repo_root, spec["path"])
            if target.read_text(encoding="utf-8") != spec["content"]:
                return False
        except (OSError, ValueError, UnicodeDecodeError):
            return False
    return True


def apply_files(repo_root: str, files: list) -> list:
    """Write the brief's files. Returns [(path, "written"|"unchanged"), ...].

    Parent directories are created; existing files are overwritten in place
    (the trainee's session branch + close PR are the undo path — the trainer
    sees every applied change in the diff they review).
    """
    results = []
    for spec in files:
        target = resolve_target(repo_root, spec["path"])
        try:
            current = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            current = None
        if current == spec["content"]:
            results.append((spec["path"], "unchanged"))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(spec["content"], encoding="utf-8")
        results.append((spec["path"], "written"))
    return results


def write_ack(cfg: dict, *, session_id: str, revision: int,
              applied: list | None = None) -> dict:
    """The R2-1 round-trip close: an ``ack`` message point on the operational
    thread. seq=revision → idempotent per (session, revision).

    ``applied`` (from ``apply_files``) is recorded in the body so the trainer
    sees exactly which files the hook wrote, not just that "it landed".
    """
    from lib import qdrant_http, training_config, training_substrate

    body = f"Applied with in-session trainee approval (revision {revision})."
    if applied:
        listing = ", ".join(f"{path} ({state})" for path, state in applied)
        body += f" Files written by the hook: {listing}."
    point = training_substrate.build_message_point(
        brief_id=cfg["operational_brief_id"], trainee=cfg["trainee"],
        session_id=session_id, seq=revision, message_type="ack",
        channel="operational", ack_of_revision=revision,
        summary=f"operational brief revision {revision} applied",
        body=body)
    return qdrant_http.upsert_points(
        [point], collection=cfg["briefs_collection"],
        timeout=QDRANT_TIMEOUT, **training_config.qdrant_kwargs(cfg))


def _load_config(repo_root: str, session_id: str = ""):
    """Config, or None with the reason logged — every caller degrades soft."""
    from lib import training_config
    try:
        cfg = training_config.load(repo_root)
    except training_config.TrainingConfigError as exc:
        _log(f"materialise skipped: {exc}", session_id)
        return None
    if not training_config.is_configured(cfg):
        _log("materialise skipped: training-config.yaml not filled yet "
             "(take-on Phase A pending)", session_id)
        return None
    return cfg


def _parse_revision(flag: str, revision_arg: str):
    try:
        return int(revision_arg)
    except ValueError:
        print(f"{flag} needs an integer revision, got {revision_arg!r}",
              file=sys.stderr)
        return None


def _ack_main(revision_arg: str) -> int:
    revision = _parse_revision("--ack", revision_arg)
    if revision is None:
        return 1
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    cfg = _load_config(_repo_root(""), session_id)
    if cfg is None:
        print("training-config.yaml missing/unfilled — cannot ack", file=sys.stderr)
        return 1
    try:
        write_ack(cfg, session_id=session_id or "no-session", revision=revision)
    except Exception as exc:
        print(f"ack write failed: {exc}", file=sys.stderr)
        return 1
    print(f"ack recorded for operational brief revision {revision}")
    return 0


def _apply_main(revision_arg: str) -> int:
    """T-121 #14: the hook applies the brief's files, then acks.

    Run ONLY after the trainee has approved in-session. Refetches the brief
    (rather than trusting anything relayed through the conversation) so what
    lands on disk is exactly what the trainer authored.
    """
    revision = _parse_revision("--apply", revision_arg)
    if revision is None:
        return 1
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    repo_root = _repo_root("")
    cfg = _load_config(repo_root, session_id)
    if cfg is None:
        print("training-config.yaml missing/unfilled — cannot apply", file=sys.stderr)
        return 1

    try:
        brief = fetch_operational_brief(cfg, session_id=session_id)
    except Exception as exc:
        print(f"could not reach the operational brief channel: {exc}\n"
              "Nothing was changed. This is safe to retry when back online.",
              file=sys.stderr)
        return 1
    if brief is None:
        print("no active operational brief to apply — nothing changed",
              file=sys.stderr)
        return 1

    live_revision = brief.get("revision", 1)
    if live_revision != revision:
        print(f"revision mismatch: the trainee approved revision {revision} but "
              f"the live brief is now revision {live_revision}. Nothing changed — "
              "show the trainee the new version and get approval for that one.",
              file=sys.stderr)
        return 1

    files = brief_files(brief)
    if not files:
        print("this operational brief carries no files to apply — it is a prose "
              f"instruction. Acknowledge it with --ack {revision} once done.",
              file=sys.stderr)
        return 1

    try:
        applied = apply_files(repo_root, files)
    except Exception as exc:
        print(f"apply failed: {exc}", file=sys.stderr)
        return 1

    for path, state in applied:
        print(f"  {state:9} {path}")
    try:
        write_ack(cfg, session_id=session_id or "no-session", revision=revision,
                  applied=applied)
    except Exception as exc:
        # The files ARE applied; only the trainer's receipt failed. Say so
        # precisely — a retry re-acks idempotently and re-writes nothing.
        print(f"files applied, but the ack write-back failed: {exc}\n"
              "Retry this command when back online; it is idempotent.",
              file=sys.stderr)
        return 1
    print(f"Applied operational brief revision {revision} and acknowledged it.")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--ack":
        return _ack_main(argv[1] if len(argv) > 1 else "")
    if argv and argv[0] == "--apply":
        return _apply_main(argv[1] if len(argv) > 1 else "")

    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}
    session_id = str(data.get("session_id", ""))
    cwd = str(data.get("cwd", "") or "")

    cfg = _load_config(_repo_root(cwd), session_id)
    if cfg is None:
        return 0

    try:
        brief = fetch_operational_brief(cfg, session_id=session_id)
    except Exception as exc:
        # Qdrant unreachable / auth drift — offline-first, one quiet line.
        _log(f"operational brief unavailable (soft): {exc}", session_id)
        _emit_context(
            "ℹ️  Operational brief channel unavailable (offline or not "
            "configured) — proceed with AGENTS.md + trainee-brief.md alone; "
            "that is normal, not an error.")
        return 0

    if brief is None:
        _log("no active operational brief — offline-first proceed", session_id)
        return 0

    revision = brief.get("revision", 1)
    summary = brief.get("summary") or "repo update instruction"
    files = brief_files(brief)

    # Already on disk from an earlier session? Recurring briefs stay `active`
    # forever, so stay silent rather than re-asking for approval of a change
    # the trainee already approved.
    if files and files_already_applied(_repo_root(cwd), files):
        _log(f"operational brief revision {revision} already applied — quiet",
             session_id)
        return 0

    _log(f"operational brief materialised: {cfg['operational_brief_id']} "
         f"revision {revision}"
         f"{' (hook-apply, %d file(s))' % len(files) if files else ''}",
         session_id)

    if files:
        # T-121 #14: the HOOK writes these files, not the tutor. Alex's job is
        # one plain-language sentence and a yes/no — a beginner is never asked
        # to reason about AGENTS.md/CLAUDE.md they have not been taught yet.
        listing = "\n".join(f"  - {spec['path']}" for spec in files)
        _emit_context(
            f"📋 REPO UPDATE PENDING (revision {revision}, from the training "
            f"team, updated {brief.get('updated_at', 'unknown')}) — {summary}\n\n"
            "DO NOT edit any of these files yourself, and do not show the "
            "trainee their contents or discuss what they are for — that is a "
            "curriculum topic, not a session interruption. The hook applies "
            "them.\n\n"
            f"Files the update will replace:\n{listing}\n\n"
            f"Trainer's note (context for you, not a script to read out):\n"
            f"{brief.get('body', '')}\n\n"
            "Do this, before starting the programme content:\n"
            "  1. Tell the trainee in ONE plain sentence what the update is "
            f"for (\"{summary}\") and ask if they're happy to apply it.\n"
            "  2. If they say yes, run exactly:\n"
            f"       python3 .claude/hooks/trainee-materialise.py --apply {revision}\n"
            "     That writes the files and records their approval in one step.\n"
            "  3. If they say no, apply nothing, tell them that's fine, and "
            "carry on with the session — it will be offered again next time.")
    else:
        _emit_context(
            f"📋 OPERATIONAL BRIEF (revision {revision}, updated "
            f"{brief.get('updated_at', 'unknown')}) — {summary}\n\n"
            f"{brief.get('body', '')}\n\n"
            "This brief carries no files for the hook to apply — it is a prose "
            "instruction. Apply it WITH the trainee's in-session approval "
            "before continuing the programme. Once applied, acknowledge with:\n"
            f"  python3 .claude/hooks/trainee-materialise.py --ack {revision}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # fail-soft: never wall a trainee session (R2-4)
