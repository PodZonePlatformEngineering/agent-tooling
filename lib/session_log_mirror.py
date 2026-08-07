"""
session_log_mirror.py — copy the live session transcript into the trainee's
own ``logs/`` directory (PROJ-011/T-122 Build A, design doc §1).

Factored out of ``trainee-finalise.py``'s ``_copy_session_log`` so the same
mirror can run twice: on every ``Stop`` (interim safety net, from
``trainee-telemetry.py``) and, unchanged, as the final authoritative
overwrite at ``SessionEnd`` (from ``trainee-finalise.py``). A hang or crash
between two ``Stop``s therefore leaves the repo's own transcript copy
reflecting the last completed turn instead of stale-since-close.

Deliberately local-file-only, no Qdrant write (design doc §1, "explicitly out
of scope"). Degrades soft like every other trainee hook: any failure (missing
transcript, unwritable ``logs/``) returns ``False`` and never raises.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone


def mirror_session_log(transcript_path: str, repo_dir: str, session_id: str) -> bool:
    """Copy ``transcript_path`` into ``{repo_dir}/logs/session-{sid8}.jsonl``
    and stamp ``{repo_dir}/logs/session-{sid8}.heartbeat`` with the current
    UTC time. Best-effort — returns True only on a successful copy."""
    if not (transcript_path and repo_dir and os.path.isfile(transcript_path)):
        return False
    sid8 = (session_id or "session")[:8]
    logs_dir = os.path.join(repo_dir, "logs")
    try:
        os.makedirs(logs_dir, exist_ok=True)
        shutil.copyfile(transcript_path,
                        os.path.join(logs_dir, f"session-{sid8}.jsonl"))
        with open(os.path.join(logs_dir, f"session-{sid8}.heartbeat"), "w",
                  encoding="utf-8") as fh:
            fh.write(datetime.now(timezone.utc).isoformat() + "\n")
        return True
    except Exception:
        return False
