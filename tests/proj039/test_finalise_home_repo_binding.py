"""T-054 (CC-364) — the finalise binds to the agent's OWN home repo, never the
wandered cwd (the operator-clone hijack class).

Defect (Athena T-026, sid 2b5156d0): the shell ended with cwd in
``.workspace/academy-admin`` (a subrepo the session stood up); the SessionEnd hook
took that as its repo and ``return_to_main`` ran against the subrepo, the result PR
was raised from the operator's live apex clone, and the real home repo stayed on its
session branch with the lock held. Every downstream repo op must instead bind to the
``CLAUDE_PROJECT_DIR``-anchored home repo.

Drives the real ``session-end-finalise.py`` hook in-process against **real** temp git
repos, with only the Qdrant/telemetry-touching steps stubbed (the git-touching steps
— return-to-main, lock release, result authoring — run for real). Asserts, with the
shell cwd deliberately inside ``.workspace/academy-admin``:

  * the HOME repo is returned to a ff'd main and its session branch deleted;
  * the ``.workspace/academy-admin`` subrepo is left **entirely untouched**;
  * the one-session lock is released True (regression: it used to report False);
  * the result-authoring step targets the HOME repo — and NOT the apex
    ``PODZONETEAM_REPO`` clone, even though that env is set (team-lead
    home_repo≠team_repo case: the migrated path wins).
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


def _mk_repo(base: Path, name: str, branch: str) -> Path:
    """A bare origin + a clone with one commit, checked out on ``branch``."""
    origin = base / f"{name}.git"
    clone = base / name
    _run("git", "init", "--bare", "-b", "main", str(origin))
    _run("git", "clone", str(origin), str(clone))
    for k, v in (("user.email", "t@t"), ("user.name", "T"),
                 ("commit.gpgsign", "false")):
        _git(clone, "config", k, v)
    (clone / ".gitignore").write_text(".workspace/\n")
    _git(clone, "add", ".gitignore")
    _git(clone, "commit", "-m", "init")
    _git(clone, "push", "origin", "main")
    if branch != "main":
        _git(clone, "checkout", "-b", branch)
        (clone / "work.txt").write_text("work\n")
        _git(clone, "add", "work.txt")
        _git(clone, "commit", "-m", "work")
        _git(clone, "push", "-u", "origin", branch)
    return clone


SESSION_POINT = {
    "session_id": "dc45f0f5-7123-4f78-aba6-29dcf3c4e55a",
    "agent": "Athena", "work_item": "PROJ-011/T-026",
    "brief_id": "training/2026-07-05-x-athena",
    "brief": {"text": "stand up academy-admin"},
    "response": {"text": "done", "status_transition": "in_progress->complete"},
    "rollup": {},
}


class TestFinaliseHomeRepoBinding(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)

        # HOME repo (a migrated home-* clone) on a session branch.
        self.session_branch = "session/athena-2026-07-05-t026"
        self.home = _mk_repo(base, "home-podzone-athena", self.session_branch)
        # A stood-up subrepo INSIDE the home repo's .workspace/ — where the shell ends.
        ws = self.home / ".workspace"
        ws.mkdir()
        self.subrepo = _mk_repo(ws, "academy-admin", "feature/schema")
        self.sub_branch_before = session_guard.current_branch(self.subrepo)

        # Isolate the lock dir; pre-acquire the lock as the launcher would.
        self._orig_lockdir = session_guard.LOCK_DIR
        session_guard.LOCK_DIR = base / "locks"
        self.sid = SESSION_POINT["session_id"]
        acq = session_guard.SessionLock(str(self.home), self.sid).acquire()
        assert acq["ok"], acq

        # Stub every Qdrant/telemetry-touching step (keep git-touching steps real).
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

        # Capture where the result-authoring + apex paths are pointed.
        self.author_calls: list[str] = []
        self.apex_calls: list[str] = []
        self._patch(session_finalise, "author_home_result",
                    lambda *a, **k: (self.author_calls.append(k.get("repo_dir", "")),
                                     {"ok": True, "disposition": "done", "branch": "b",
                                      "pr_url": "", "reason": ""})[1])
        self._patch(session_finalise, "commit_brief_result",
                    lambda *a, **k: (self.apex_calls.append(k.get("repo_dir", "")),
                                     {"ok": True, "branch": "b", "pr_url": "", "reason": ""})[1])

        self._orig_env = dict(os.environ)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.home)
        os.environ["PODZONE_LOG_DIR"] = str(base / "homelogs")
        # Apex env set on purpose — the migrated path must still win (team-lead case).
        os.environ["PODZONETEAM_REPO"] = str(base / "apex-must-not-be-touched")
        os.environ.pop("TRAINEE_RUNTIME", None)

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
        session_guard.LOCK_DIR = self._orig_lockdir
        os.environ.clear()
        os.environ.update(self._orig_env)
        self._tmp.cleanup()

    def _drive(self, cwd: str) -> int:
        sys.stdin = io.StringIO(json.dumps({
            "session_id": self.sid, "transcript_path": "", "cwd": cwd,
        }))
        return self.hook.main()

    def _assert_clean_home_finalise(self) -> None:
        """The invariant, independent of where the shell ended: the HOME repo is
        returned to a ff'd main, the subrepo is untouched, the result targets HOME
        (never the apex clone), and the lock releases True."""
        # HOME repo returned to main; the session branch reaped (it was pushed).
        self.assertEqual(session_guard.current_branch(self.home), "main")
        branches = _git(self.home, "branch", "--format=%(refname:short)").stdout.split()
        self.assertNotIn(self.session_branch, branches,
                         "home session branch should have been deleted")

        # The .workspace subrepo is UNTOUCHED — still on its branch, clean.
        self.assertEqual(session_guard.current_branch(self.subrepo),
                         self.sub_branch_before)
        self.assertFalse(session_guard.working_tree_dirty(self.subrepo))

        # Result authoring targeted the HOME repo, never the apex clone.
        self.assertEqual(self.author_calls, [str(self.home)])
        self.assertEqual(self.apex_calls, [],
                         "the migrated home path must not touch PODZONETEAM_REPO")

        # Lock released True (regression: used to be False) — the lock file is gone.
        lock_path = session_guard._lock_path(str(self.home))
        self.assertFalse(lock_path.exists(), "session lock should have been released")

    def test_primary_cwd_inside_workspace_subrepo(self) -> None:
        """PRIMARY case (operator design input, 2026-07-05): the LAST act of a
        session is add/commit/push/PR in a task repo, so the shell ending in
        ``.workspace/<subrepo>`` is the DEFAULT end state — not an edge case. The
        finalise must still operate entirely on the home repo via CLAUDE_PROJECT_DIR."""
        self.assertEqual(self._drive(str(self.subrepo)), 0)
        self._assert_clean_home_finalise()

    def test_secondary_cwd_at_home_root(self) -> None:
        """SECONDARY case: the shell happens to end at the home-repo root. Same clean
        finalise — CLAUDE_PROJECT_DIR resolves to the same home repo and nothing about
        the outcome changes."""
        self.assertEqual(self._drive(str(self.home)), 0)
        self._assert_clean_home_finalise()

    def test_response_step_done_with_no_embed_endpoint_anywhere(self) -> None:
        """PROJ-041/T-002: the response step reports `done` on the payload write
        alone — the REAL ``upsert_response`` runs (only the HTTP layer is faked),
        with no embed endpoint configured anywhere, and no vector patch happens.
        Regression target: the former response-vector patch failing on an
        embed-less host used to mark the step `failed` though nothing
        lifecycle-relevant had failed."""
        from lib import finalise_ledger, qdrant_http

        real_upsert_response = next(
            orig for mod, name, orig in self._patches
            if mod is session_substrate and name == "upsert_response"
        )
        calls: list[tuple[str, str]] = []

        def recorder(method, url, *, payload=None, api_key=None, timeout=None):
            calls.append((method.upper(), url))
            return {"result": {"status": "acknowledged"}}

        steps: dict[str, str] = {}
        self._patch(session_substrate, "upsert_response", real_upsert_response)
        self._patch(qdrant_http, "request_json", recorder)
        self._patch(finalise_ledger, "record_step",
                    lambda sid, name, status: steps.__setitem__(name, status))
        os.environ.pop("OLLAMA_HOST", None)
        os.environ.setdefault("PODZONE_QDRANT_APIKEY", "test-key")

        self.assertEqual(self._drive(str(self.home)), 0)
        self.assertEqual(steps.get("response"), "done")
        # payload-only: the set_payload landed, no vector patch anywhere
        self.assertTrue(any(u.endswith("/points/payload") for _, u in calls))
        self.assertFalse(any(u.endswith("/points/vectors") for _, u in calls))
        self._assert_clean_home_finalise()


if __name__ == "__main__":
    unittest.main()
