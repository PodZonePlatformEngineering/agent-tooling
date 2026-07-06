"""runtime_log.py — best-effort append-only runtime logging for resident
home-repo Python libs and hooks (PROJ-039/T-029, CC-328).

Writes timestamped, caller-identified lines to ``<project>/logs/libraries.log``.
A log-write failure NEVER propagates — observability must not break a hook or a
primitive (the same best-effort contract the SessionEnd finalise lives by).

Stdlib-only by design: home-repo ``python3`` has no ``requests`` (see memory
``feedback-substrate-hooks-requests-free``); this module imports nothing outside
the standard library and nothing from ``lib/`` so it stays a leaf of the
home-runtime-lib closure.

Log line format (one per call)::

    2026-06-27T17:48:03Z [INFO] tooling=v1.0.0 session_finalise session=<sid>: rollup attached (tools=7)

The ``tooling=v{X.Y.Z}`` stamp (PROJ-039/T-055) is a lazy, best-effort read of the
shipped ``.claude/tooling-manifest.json`` — ``tooling=unknown`` when the manifest is
absent or corrupt; the log line is still written either way (never-raise, T-029).

The log directory is resolved best-effort in priority order:
  1. ``$PODZONE_LOG_DIR``           — explicit override (tests / odd layouts)
  2. ``$CLAUDE_PROJECT_DIR``/logs   — the home-repo root Claude Code exports to hooks
  3. ``<cwd>``/logs                 — fallback (hooks run with cwd = the worktree)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Unkeyed fallback name — used only when no session id can be resolved (T-048: the
# committed session logs are sid-keyed, ``libraries-{sid8}.log``, so they ride the
# session-result PR as per-session diagnostics rather than one ever-growing file).
LIBRARIES_LOG = "libraries.log"


def resolve_log_dir() -> Path:
    """Best-effort resolution of the runtime ``logs/`` directory (not created here)."""
    override = os.environ.get("PODZONE_LOG_DIR")
    if override:
        return Path(override)
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if project:
        return Path(project) / "logs"
    # Last resort only (both envs unset). Strip any `.workspace/` tail so a shell
    # that ended inside a stood-up subrepo still logs to the OWNING home repo's
    # logs/ (T-054 — inlined here because this module stays a lib-free leaf).
    cwd = str(Path.cwd())
    marker = os.sep + ".workspace"
    i = cwd.find(marker + os.sep)
    if i != -1:
        cwd = cwd[:i]
    elif cwd.endswith(marker):
        cwd = cwd[: -len(marker)]
    return Path(cwd) / "logs"


def _sid8(session_id: str | None) -> str:
    """The 8-char sid tag for a sid-keyed log filename, or '' if none resolvable.

    Priority: the explicit ``session_id`` arg, else ``$PODZONE_SESSION_ID`` — the
    per-session env every substrate shell hook exports after parsing its stdin json
    (and the SessionEnd finalise sets in-process), T-048. Non-hex/short values are
    used verbatim-truncated so synthetic test sids still key a distinct file."""
    sid = (session_id or os.environ.get("PODZONE_SESSION_ID") or "").strip()
    return sid[:8]


def libraries_log_name(session_id: str | None = None) -> str:
    """The libraries log filename for a session: ``libraries-{sid8}.log`` when a sid
    is resolvable, else the unkeyed ``libraries.log`` (T-048)."""
    tag = _sid8(session_id)
    return f"libraries-{tag}.log" if tag else LIBRARIES_LOG


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# The manifest key the tooling stamp reads (T-055).
_TOOLING_MANIFEST_NAME = "tooling-manifest.json"
_UNKNOWN_VERSION = "unknown"


def tooling_version() -> str:
    """Lazy, never-raise read of the shipped tooling version, as ``v{X.Y.Z}`` (T-055).

    Reads ``{project}/.claude/tooling-manifest.json``'s ``version`` field, where
    ``{project}`` is ``resolve_log_dir()``'s parent (same env-priority resolution:
    ``$PODZONE_LOG_DIR``/.. > ``$CLAUDE_PROJECT_DIR`` > cwd), and returns it prefixed
    with ``v``. Returns :data:`_UNKNOWN_VERSION` (no ``v`` prefix) when the manifest
    is absent, unreadable, or missing the key — a missing/corrupt manifest must never
    break a log call. Re-read on every call (not cached): the file is small, and a
    self-update (T-056) rewrites it before task work, so a cache would risk serving a
    stale version within a long-running process.
    """
    try:
        manifest_path = resolve_log_dir().parent / ".claude" / _TOOLING_MANIFEST_NAME
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = data.get("version")
        if not version:
            return _UNKNOWN_VERSION
        return f"v{version}"
    except Exception:
        return _UNKNOWN_VERSION


def log_library(component: str, message: str, *, session_id: str | None = None,
                level: str = "INFO") -> None:
    """Append one best-effort line to ``logs/libraries-{sid8}.log`` (T-048).

    Never raises: any failure (unresolvable dir, unwritable file, encoding) is
    swallowed so the calling lib/hook is unaffected — the never-raise contract every
    runtime-lib call-site relies on. ``component`` identifies the caller (e.g.
    ``"session_finalise"``); ``session_id`` keys both the filename and the line. Every
    line carries a ``tooling=v{X.Y.Z}`` stamp (T-055; ``tooling=unknown`` if the
    manifest can't be read).
    """
    try:
        log_dir = resolve_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        sid = f" session={session_id}" if session_id else ""
        line = (f"{_utc_stamp()} [{level}] tooling={tooling_version()} "
                f"{component}{sid}: {message}\n")
        with open(log_dir / libraries_log_name(session_id), "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        # Best-effort: observability must never break the caller (T-029 acceptance).
        pass
