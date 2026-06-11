"""Smoke tests for the PROJ-039 hook entrypoints + brief-authoring CLI.

The hook entrypoints are best-effort: they must exit 0 even with no API key /
no Qdrant reachable (a Stop or SessionEnd hook must never break the session).
The brief-authoring tool, by contrast, must fail LOUDLY (non-zero) when it can't
create the point — a silently-failed dispatch would strand the agent.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "hooks"
TOOLS = REPO_ROOT / "tools"


def _run(args, stdin="", env_extra=None, timeout=30):
    env = dict(os.environ)
    env.pop("PODZONE_QDRANT_APIKEY", None)  # simulate a bare subprocess
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(REPO_ROOT), input=stdin, env=env,
        capture_output=True, text=True, timeout=timeout,
    )


class TestAppendSessionStop(unittest.TestCase):
    def test_exits_zero_without_key(self) -> None:
        # best-effort: no key, no Qdrant → must not break the Stop hook
        p = _run([str(HOOKS / "append-session-stop.py"),
                  "sess-x", "2026-06-11T10:00:00+00:00", "end_turn", ""])
        self.assertEqual(p.returncode, 0, msg=p.stderr)

    def test_exits_zero_with_too_few_args(self) -> None:
        p = _run([str(HOOKS / "append-session-stop.py"), "only-one"])
        self.assertEqual(p.returncode, 0, msg=p.stderr)


class TestSessionEndFinalise(unittest.TestCase):
    def test_empty_stdin_exits_zero(self) -> None:
        p = _run([str(HOOKS / "session-end-finalise.py")], stdin="{}")
        self.assertEqual(p.returncode, 0, msg=p.stderr)

    def test_no_session_id_exits_zero(self) -> None:
        p = _run([str(HOOKS / "session-end-finalise.py")],
                 stdin='{"cwd": "/tmp"}')
        self.assertEqual(p.returncode, 0, msg=p.stderr)

    def test_session_id_no_key_still_exits_zero(self) -> None:
        # response upsert (embed + qdrant) will fail without key/Ollama, but the
        # hook swallows it and exits 0.
        p = _run([str(HOOKS / "session-end-finalise.py")],
                 stdin='{"session_id": "sess-y", "transcript_path": ""}')
        self.assertEqual(p.returncode, 0, msg=p.stderr)


class TestCreateSessionPointCLI(unittest.TestCase):
    def test_empty_brief_rejected(self) -> None:
        p = _run([str(TOOLS / "create-session-point.py"),
                  "--session-id", "s", "--agent", "a",
                  "--work-item", "PROJ-039/T-006", "--brief", "   "])
        self.assertEqual(p.returncode, 2, msg=p.stderr)

    def test_missing_brief_arg_rejected(self) -> None:
        p = _run([str(TOOLS / "create-session-point.py"),
                  "--session-id", "s", "--agent", "a",
                  "--work-item", "PROJ-039/T-006"])
        self.assertNotEqual(p.returncode, 0)

    def test_fails_loudly_without_key(self) -> None:
        # A real dispatch with no key must NOT silently succeed.
        p = _run([str(TOOLS / "create-session-point.py"),
                  "--session-id", "s", "--agent", "a",
                  "--work-item", "PROJ-039/T-006", "--brief", "do the thing"])
        self.assertNotEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()
