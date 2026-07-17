"""PROJ-043/T-003 (CC-433) — the serial-mode end-guard returns EVERY touched
clone, not just the home repo.

Defect (Hephaestus t002, sid e29fed8b, 2026-07-17): the session worked
``~/workspace/agent-tooling`` as a serial-mode task repo — branched the primary
clone with a bare ``git checkout -b`` (so NO session lock was taken; the F13
lock-set enumeration could not see it), raised + merged the PR, and exited. The
SessionEnd finalise returned the HOME repo to main correctly but left the
task-repo clone stranded on its merged session branch; Hermes's next ``git
pull`` failed on the deleted remote ref and was recovered by hand. This recurs
on every serial dispatch that branches a task repo.

Lifecycle-faithful per the T-091 lesson (unit-test-green ≠ lifecycle-faithful):
drives the REAL ``session-end-finalise.py`` hook in-process against real temp
git repos — a home repo (with a real identity YAML, so the T-099 agent
resolution the scan scopes on runs for real) plus a task-repo clone left on a
pushed session branch under a scan-visible workspace root. Only the
Qdrant/telemetry-touching steps are stubbed; every git-touching step (return-
to-main, the T-003 scan, lock release, result authoring targeting) runs for
real. Asserts:

  * the HOME repo ends on a ff'd main with its session branch deleted;
  * the TASK-REPO clone (never locked — the t002 shape) also ends on a ff'd
    main with its pushed session branch deleted — the regression line;
  * a foreign agent's clone on ITS session branch is left entirely untouched;
  * zero session locks remain.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import (  # noqa: E402
    session_guard, session_substrate, telemetry_repo, cst_cleanup,
    brief_substrate, session_finalise,
)

HOOK_PATH = REPO_ROOT / "hooks" / "session-end-finalise.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("session_end_finalise", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


def _git(repo, *args):
    return _run("git", "-C", str(repo), *args)


def _mk_repo(base: Path, name: str, branch: str = "") -> Path:
    """A bare origin + a clone with one commit; optionally left on a pushed
    session ``branch`` (the stranded shape)."""
    origin = base / f"{name}-origin.git"
    clone = base / name
    _run("git", "init", "--bare", "-b", "main", str(origin))
    _run("git", "clone", str(origin), str(clone))
    for k, v in (("user.email", "t@t"), ("user.name", "T"),
                 ("commit.gpgsign", "false")):
        _git(clone, "config", k, v)
    (clone / "README.md").write_text(f"{name}\n")
    _git(clone, "add", "README.md")
    _git(clone, "commit", "-m", "init")
    _git(clone, "push", "origin", "main")
    if branch:
        _git(clone, "checkout", "-b", branch)
        (clone / "work.txt").write_text("work\n")
        _git(clone, "add", "work.txt")
        _git(clone, "commit", "-m", "work")
        _git(clone, "push", "-u", "origin", branch)
    return clone


SESSION_POINT = {
    "session_id": "e29fed8b-0000-4000-8000-000000000000",
    "agent": "Hephaestus", "work_item": "PROJ-043/T-002",
    "brief_id": "podzone/2026-07-17-proj043-t002-fusion-sweep",
    "brief": {"text": "fusion sweep"},
    "response": {"text": "done", "status_transition": "in_progress->complete"},
    "rollup": {},
}


class TestEndGuardReturnsTaskRepos(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)

        # HOME repo on a pushed session branch, with a REAL identity YAML —
        # the hook's end-guard scan resolves its agent through T-099 for real.
        self.home_branch = "session/hephaestus-2026-07-17-t002"
        self.home = _mk_repo(base, "home-podzone-hephaestus", self.home_branch)
        ident = self.home / "workspaces" / "identity"
        ident.mkdir(parents=True)
        (ident / "hephaestus.identity.yaml").write_text(
            "agent: Hephaestus\nrole_class: coder\nscope: podzone\n")

        # The scan-visible workspace root: the t002 task-repo clone, branched
        # mid-session with a bare `git checkout -b` — NO lock exists for it —
        # plus a foreign agent's clone that must stay untouched.
        self.ws_root = base / "workspace"
        self.ws_root.mkdir()
        self.task_branch = "hephaestus/2026-07-17-proj043-t002-fusion-sweep"
        self.task = _mk_repo(self.ws_root, "agent-tooling", self.task_branch)
        self.foreign_branch = "athena/2026-07-17-her-work"
        self.foreign = _mk_repo(self.ws_root, "academy-admin", self.foreign_branch)

        self._orig_ws_root = session_guard.WORKSPACE_ROOT
        session_guard.WORKSPACE_ROOT = self.ws_root
        self._orig_lockdir = session_guard.LOCK_DIR
        session_guard.LOCK_DIR = base / "locks"

        # Only the HOME lock exists (the launcher took it); the task repo was
        # branched mid-session and never locked — the exact live-failure shape.
        self.sid = SESSION_POINT["session_id"]
        acq = session_guard.SessionLock(str(self.home), self.sid).acquire()
        assert acq["ok"], acq

        # Stub every Qdrant/telemetry-touching step (git-touching steps real).
        self._patches = []
        self._patch(session_substrate, "upsert_response", lambda *a, **k: None)
        self._patch(session_substrate, "get_session_point", lambda sid: SESSION_POINT)
        self._patch(session_substrate, "compute_rollup",
                    lambda tp: {"tool_usage": {}, "cost_tokens": {}})
        self._patch(session_substrate, "attach_rollup", lambda *a, **k: None)
        self._patch(brief_substrate, "complete_brief", lambda *a, **k: None)
        self._patch(telemetry_repo, "resolve_remote", lambda *a, **k: None)
        self._patch(telemetry_repo, "ensure_repo",
                    lambda *a, **k: {"repo_dir": "", "initialised": False})
        self._patch(telemetry_repo, "commit_and_push",
                    lambda *a, **k: {"committed": False, "pushed": False, "reason": "test"})
        self._patch(cst_cleanup, "delete_raw_tool_events",
                    lambda *a, **k: {"deleted_before_count": 0})
        self._patch(session_finalise, "author_home_result",
                    lambda *a, **k: {"ok": True, "disposition": "done", "branch": "b",
                                     "pr_url": "", "reason": ""})
        self._patch(session_finalise, "commit_brief_result",
                    lambda *a, **k: {"ok": True, "branch": "b", "pr_url": "",
                                     "reason": ""})

        self._orig_env = dict(os.environ)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.home)
        os.environ["PODZONE_LOG_DIR"] = str(base / "homelogs")
        os.environ.pop("PODZONETEAM_REPO", None)
        os.environ.pop("TRAINEE_RUNTIME", None)
        os.environ.pop("PODZONE_INGEST_TRANSCRIPT", None)

        self._orig_stdin = sys.stdin
        self.hook = _load_hook()

    def _patch(self, mod, name, fn) -> None:
        orig = getattr(mod, name)
        self._patches.append((mod, name, orig))
        setattr(mod, name, fn)

    def tearDown(self) -> None:
        for mod, name, orig in reversed(self._patches):
            setattr(mod, name, orig)
        sys.stdin = self._orig_stdin
        session_guard.WORKSPACE_ROOT = self._orig_ws_root
        session_guard.LOCK_DIR = self._orig_lockdir
        os.environ.clear()
        os.environ.update(self._orig_env)
        self._tmp.cleanup()

    def _drive(self) -> int:
        # The shell's LAST act was the task-repo push/PR, so cwd ends INSIDE
        # the task repo — the documented t002 end state.
        sys.stdin = io.StringIO(json.dumps({
            "session_id": self.sid, "transcript_path": "",
            "cwd": str(self.task),
        }))
        return self.hook.main()

    def _branches(self, repo: Path) -> list:
        return _git(repo, "branch", "--format=%(refname:short)").stdout.split()

    def test_finalise_returns_home_and_task_clone_leaves_foreign(self) -> None:
        self.assertEqual(self._drive(), 0)

        # HOME repo: returned to main, session branch reaped (it was pushed).
        self.assertEqual(session_guard.current_branch(self.home), "main")
        self.assertNotIn(self.home_branch, self._branches(self.home))

        # TASK repo — the regression line. Before T-003 this clone stayed
        # stranded on its merged session branch with no lock to find it by.
        self.assertEqual(session_guard.current_branch(self.task), "main",
                         "task-repo clone must be returned to main")
        self.assertNotIn(self.task_branch, self._branches(self.task),
                         "pushed task session branch must be deleted")

        # Foreign agent's clone: entirely untouched.
        self.assertEqual(session_guard.current_branch(self.foreign),
                         self.foreign_branch)
        self.assertIn(self.foreign_branch, self._branches(self.foreign))

        # Zero locks remain — home's released, task never had one to leak.
        self.assertEqual(list(session_guard.LOCK_DIR.glob("*.lock")), [])

    def test_unpushed_task_work_survives_the_finalise(self) -> None:
        """Never discard commits: extra UNPUSHED work on the task branch rides
        through the finalise — the clone returns to main but the branch stays."""
        (self.task / "unpushed.txt").write_text("precious\n")
        _git(self.task, "add", "unpushed.txt")
        _git(self.task, "commit", "-m", "unpushed work")

        self.assertEqual(self._drive(), 0)

        self.assertEqual(session_guard.current_branch(self.task), "main")
        self.assertIn(self.task_branch, self._branches(self.task),
                      "branch with unpushed commits must survive")
        tip = _git(self.task, "rev-parse", self.task_branch).stdout.strip()
        msg = _git(self.task, "log", "-1", "--format=%s", tip).stdout.strip()
        self.assertEqual(msg, "unpushed work")


if __name__ == "__main__":
    unittest.main()
