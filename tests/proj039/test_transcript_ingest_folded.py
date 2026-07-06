"""T-053 (CC-363) — the archivist's transcript ingest is FOLDED INTO the finalise
as its last step, so the archivist runs a SINGLE SessionEnd hook.

Defect: Thoth's headless one-shots (T-022 sid 9035370d, T-023 sid 38ae63e3) both
died `SessionEnd hook … failed: Hook cancelled` on the finalise, each cutting off
after brief_status/response/rollup and BEFORE telemetry/result-PR/return-to-main.
Hephaestus (a single SessionEnd hook) is 4/4 clean. Diagnosis: the archivist wires
**two** SessionEnd hooks — the heavy finalise (git + telemetry push) plus
`ingest-transcript.py` (N sequential Ollama embeds + a Telegram notify). The combined
teardown overran the CLI's SessionEnd budget and the finalise was cancelled mid-run,
losing the load-bearing result PR + return-to-main.

Fix (proven here): a single SessionEnd hook. `ingest-transcript.py` is folded into
the finalise as the LAST step (after return-to-main), gated by `PODZONE_INGEST_TRANSCRIPT=1`
(archivist settings), ledger-tracked. This test proves:

  * **scaffold** — the archivist SessionEnd is a single hook + carries the env flag;
    no other role runs ingest.
  * **ordering** — the ingest runs AFTER return-to-main (the home repo is already on a
    ff'd main by the time ingest fires), so a teardown cancel during the slow ingest
    can only truncate the ingest tail — every load-bearing step has already committed.
  * **gating** — with the env unset, no ingest runs (pure no-op for non-archivists).
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

SCAFFOLD = REPO_ROOT / "scaffold.sh"
HOOK_PATH = REPO_ROOT / "hooks" / "session-end-finalise.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("session_end_finalise_t053", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


def _git(repo, *args):
    return _run("git", "-C", str(repo), *args)


class TestArchivistSingleHook(unittest.TestCase):
    def test_scaffold_archivist_single_sessionend_hook_with_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "thoth"
            subprocess.run(["bash", str(SCAFFOLD), "podzone", "thoth", "archivist",
                            "--target-dir", str(dest)],
                           capture_output=True, text=True, check=True)
            settings = json.loads((dest / ".claude" / "settings.json").read_text())
            se = settings["hooks"]["SessionEnd"]
            hooks = [h for entry in se for h in entry["hooks"]]
            self.assertEqual(len(hooks), 1, "archivist must run a SINGLE SessionEnd hook")
            self.assertTrue(hooks[0]["command"].endswith("session-end-finalise.py"))
            self.assertEqual(settings["env"].get("PODZONE_INGEST_TRANSCRIPT"), "1")
            # Both ingest files stay resident: the finalise invokes the .sh wrapper
            # (secret injection) which runs the .py.
            self.assertTrue((dest / ".claude" / "hooks" / "ingest-transcript.sh").exists())
            self.assertTrue((dest / ".claude" / "hooks" / "ingest-transcript.py").exists())

    def test_non_archivist_has_no_ingest_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "hephaestus"
            subprocess.run(["bash", str(SCAFFOLD), "podzone", "hephaestus", "coder",
                            "--target-dir", str(dest)],
                           capture_output=True, text=True, check=True)
            settings = json.loads((dest / ".claude" / "settings.json").read_text())
            self.assertNotIn("PODZONE_INGEST_TRANSCRIPT", settings["env"])


SESSION_POINT = {
    "session_id": "9035370d-aaaa-bbbb-cccc-thoth-t022",
    "agent": "Thoth", "work_item": "PROJ-034/T-022",
    "brief": {"text": "archive"}, "response": {"text": "done"}, "rollup": {},
}


class TestIngestFoldedAndOrderedLast(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        # A migrated archivist home repo on a session branch.
        self.branch = "session/thoth-2026-07-05-t022"
        self.home = base / "home-podzone-thoth"
        origin = base / "origin.git"
        _run("git", "init", "--bare", "-b", "main", str(origin))
        _run("git", "clone", str(origin), str(self.home))
        for k, v in (("user.email", "t@t"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            _git(self.home, "config", k, v)
        (self.home / "README.md").write_text("hi\n")
        _git(self.home, "add", "README.md")
        _git(self.home, "commit", "-m", "init")
        _git(self.home, "push", "origin", "main")
        _git(self.home, "checkout", "-b", self.branch)
        (self.home / "w.txt").write_text("work\n")
        _git(self.home, "add", "w.txt")
        _git(self.home, "commit", "-m", "work")
        _git(self.home, "push", "-u", "origin", self.branch)

        self._orig_lockdir = session_guard.LOCK_DIR
        session_guard.LOCK_DIR = base / "locks"

        # Stub the Qdrant/telemetry steps; keep git-touching steps real.
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
                    lambda *a, **k: {"committed": False, "pushed": False, "reason": "t"})
        self._patch(session_finalise, "author_home_result",
                    lambda *a, **k: {"ok": True, "disposition": "done", "branch": "b",
                                     "pr_url": "", "reason": ""})

        # Intercept ONLY the ingest subprocess; delegate everything else (git) to real.
        self.ingest_observations: list[str] = []
        self._orig_run = subprocess.run

        def _fake_run(cmd, *a, **k):
            if isinstance(cmd, (list, tuple)) and any(
                    str(c).endswith("ingest-transcript.sh") for c in cmd):
                # At ingest time, the home repo must ALREADY be back on a ff'd main —
                # i.e. return_to_main ran before this step (the ordering guarantee).
                self.ingest_observations.append(session_guard.current_branch(self.home))
                class _R:  # noqa: N801
                    returncode = 0
                    stdout = "[ingest] ingested 3/3 turns"
                    stderr = ""
                return _R()
            return self._orig_run(cmd, *a, **k)

        subprocess.run = _fake_run
        self._patches.append((subprocess, "run", self._orig_run))

        self._orig_env = dict(os.environ)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.home)
        os.environ["PODZONE_LOG_DIR"] = str(base / "homelogs")
        os.environ.pop("TRAINEE_RUNTIME", None)
        os.environ.pop("PODZONEAGENTTEAM_REPO", None)

        self._orig_stdin = sys.stdin
        self.hook = _load_hook()
        from lib import finalise_ledger
        self.ledger = finalise_ledger

    def _patch(self, mod, name, fn) -> None:
        self._patches.append((mod, name, getattr(mod, name)))
        setattr(mod, name, fn)

    def tearDown(self) -> None:
        for mod, name, orig in reversed(self._patches):
            setattr(mod, name, orig)
        sys.stdin = self._orig_stdin
        session_guard.LOCK_DIR = self._orig_lockdir
        os.environ.clear()
        os.environ.update(self._orig_env)
        self._tmp.cleanup()

    def _drive(self) -> int:
        sys.stdin = io.StringIO(json.dumps({
            "session_id": SESSION_POINT["session_id"],
            "transcript_path": "", "cwd": str(self.home),
        }))
        return self.hook.main()

    def test_ingest_runs_folded_after_return_to_main(self) -> None:
        os.environ["PODZONE_INGEST_TRANSCRIPT"] = "1"
        rc = self._drive()
        self.assertEqual(rc, 0)
        # Ingest fired exactly once, AND saw the home repo already returned to main.
        self.assertEqual(self.ingest_observations, ["main"],
                         "ingest must run AFTER return_to_main (home already on main)")
        steps = self.ledger.steps(SESSION_POINT["session_id"])
        self.assertEqual(steps.get("transcript_ingest"), "done")
        self.assertIn(steps.get("return_to_main"),
                      ("returned-branch-deleted", "returned-branch-kept-unpushed",
                       "already-main"))
        self.assertEqual(session_guard.current_branch(self.home), "main")

    def test_ingest_skipped_when_env_unset(self) -> None:
        os.environ.pop("PODZONE_INGEST_TRANSCRIPT", None)
        rc = self._drive()
        self.assertEqual(rc, 0)
        self.assertEqual(self.ingest_observations, [],
                         "no ingest may run without PODZONE_INGEST_TRANSCRIPT")
        steps = self.ledger.steps(SESSION_POINT["session_id"])
        self.assertNotEqual(steps.get("transcript_ingest"), "done")


if __name__ == "__main__":
    unittest.main()
