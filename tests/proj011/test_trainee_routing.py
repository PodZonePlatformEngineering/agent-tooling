"""PROJ-011/T-030 — trainee hook routing + offline degradation (R2-3/R2-4/R2-5).

The two properties the brief demands proven:

  * **Training collections only** — capture every Qdrant URL the trainee hooks
    produce under a valid config and assert each addresses a training_*
    collection; no trainee-role config can even load with a fleet collection
    name (test_training_config.py), and this covers the request layer on top.
  * **Offline degradation** — with Qdrant unreachable (connection refused) or
    the config unfilled, every hook exits 0, emits nothing fatal, and the
    materialise offers the offline-first guidance instead of an error.
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
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "hooks"
sys.path.insert(0, str(REPO_ROOT))

from lib import qdrant_http  # noqa: E402

SID = "3f1c9a52-1111-2222-3333-444455556666"

CONFIG = """\
qdrant_url: https://training.example.cloud:6333
qdrant_api_key: "scoped-key"
trainee: sam
operational_brief_id: training/sam/operational
briefs_collection: training_briefs
telemetry_collection: training_session_telemetry
"""

FLEET_COLLECTIONS = ("briefs", "session_substrate", "claude_session_telemetry")


def _load_hook(name: str):
    """Import a hook module by file path (hyphenated names)."""
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), HOOKS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _UrlCapture:
    """Patch qdrant_http.request_json, record every URL, return canned bodies."""

    def __init__(self, get_result=None):
        self.urls: list[str] = []
        self._get_result = get_result

    def __call__(self, method, url, *, payload=None, api_key=None, timeout=None):
        self.urls.append(url)
        if method.upper() == "GET":
            return {"result": {"id": "x", "payload": self._get_result}} \
                if self._get_result is not None else {"result": None}
        return {"result": {"status": "acknowledged"}}


class TestTrainingCollectionRouting(unittest.TestCase):
    """Every URL a trainee hook produces addresses a training_* collection."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "training-config.yaml").write_text(CONFIG, encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)

    def _assert_training_only(self, urls):
        self.assertTrue(urls, "expected at least one Qdrant request")
        for url in urls:
            self.assertIn("/collections/training_", url,
                          f"non-training collection URL produced: {url}")
            for fleet in FLEET_COLLECTIONS:
                self.assertNotIn(f"/collections/{fleet}/", url + "/",
                                 f"fleet collection URL produced: {url}")

    def test_materialise_urls_training_only(self):
        mat = _load_hook("trainee-materialise")
        from lib import training_config
        cfg = training_config.load(str(self.repo))
        brief = {"status": "active", "revision": 3, "body": "do the thing",
                 "session_ids": [], "summary": "update"}
        cap = _UrlCapture(get_result=brief)
        with mock.patch.object(qdrant_http, "request_json", cap):
            got = mat.fetch_operational_brief(cfg, session_id=SID)
        self.assertEqual(got["revision"], 3)
        # GET the brief point + set_payload for session_ids[] append.
        self.assertGreaterEqual(len(cap.urls), 2)
        self._assert_training_only(cap.urls)

    def test_materialise_ack_urls_training_only(self):
        mat = _load_hook("trainee-materialise")
        from lib import training_config
        cfg = training_config.load(str(self.repo))
        cap = _UrlCapture()
        with mock.patch.object(qdrant_http, "request_json", cap):
            mat.write_ack(cfg, session_id=SID, revision=4)
        self._assert_training_only(cap.urls)

    def test_telemetry_urls_training_only_all_events(self):
        tel = _load_hook("trainee-telemetry")
        cap = _UrlCapture()
        events = [
            {"hook_event_name": "SessionStart", "session_id": SID,
             "cwd": str(self.repo), "source": "startup"},
            {"hook_event_name": "UserPromptSubmit", "session_id": SID,
             "cwd": str(self.repo), "prompt": "Hi Alex, this is Sam"},
            {"hook_event_name": "PostCompact", "session_id": SID,
             "cwd": str(self.repo), "trigger": "auto"},
            {"hook_event_name": "Stop", "session_id": SID,
             "cwd": str(self.repo), "transcript_path": ""},
        ]
        with mock.patch.object(qdrant_http, "request_json", cap), \
                mock.patch.dict(os.environ,
                                {"CLAUDE_PROJECT_DIR": str(self.repo)}):
            for ev in events:
                self.assertTrue(tel.write_event(ev),
                                f"write skipped for {ev['hook_event_name']}")
        self.assertEqual(len(cap.urls), len(events))
        self._assert_training_only(cap.urls)

    def test_telemetry_point_ids_deterministic(self):
        tel = _load_hook("trainee-telemetry")
        ev = {"hook_event_name": "UserPromptSubmit", "session_id": SID,
              "cwd": str(self.repo), "prompt": "same prompt"}
        (_p1, id1), (_p2, id2) = tel.build_event(ev), tel.build_event(ev)
        self.assertEqual(id1, id2, "re-fired hook must converge on one point")
        start_id = tel.build_event(
            {"hook_event_name": "SessionStart", "session_id": SID})[1]
        self.assertNotEqual(id1, start_id)

    def test_finalise_close_telemetry_training_only(self):
        fin = _load_hook("trainee-finalise")
        cap = _UrlCapture()
        with mock.patch.object(qdrant_http, "request_json", cap):
            ok = fin._write_close_telemetry(str(self.repo), SID, "other")
        self.assertTrue(ok)
        self._assert_training_only(cap.urls)

    def test_no_fleet_env_key_used(self):
        """The routing kwargs come from the config file — a fleet env key must
        never be consulted (R2-2: config is the single surface)."""
        tel = _load_hook("trainee-telemetry")
        seen_keys: list = []

        def capture(method, url, *, payload=None, api_key=None, timeout=None):
            seen_keys.append(api_key)
            return {"result": {}}

        env = {"CLAUDE_PROJECT_DIR": str(self.repo),
               "PODZONE_QDRANT_APIKEY": "FLEET-KEY-MUST-NOT-APPEAR"}
        with mock.patch.object(qdrant_http, "request_json", capture), \
                mock.patch.dict(os.environ, env):
            tel.write_event({"hook_event_name": "SessionStart", "session_id": SID})
        self.assertEqual(seen_keys, ["scoped-key"])


class TestHookAppliedBriefFiles(unittest.TestCase):
    """PROJ-011/T-121 #14 — the HOOK writes the brief's files, not the tutor.

    The properties that matter: SessionStart asks for approval and writes
    nothing; ``--apply`` writes the files and acks in one step; an
    already-applied brief never re-asks; nothing escapes the repo.
    """

    FILES = [{"path": "AGENTS.md", "content": "# Alex, v2\n"},
             {"path": "docs/new-note.md", "content": "note\n"}]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "training-config.yaml").write_text(CONFIG, encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)
        self.mat = _load_hook("trainee-materialise")

    def _brief(self, *, revision=3, files=None):
        brief = {"status": "active", "revision": revision,
                 "summary": "refresh Alex's briefing",
                 "body": "Replace the briefing files.", "session_ids": []}
        if files is not None:
            brief["files"] = files
        return brief

    def _run_sessionstart(self, brief):
        cap = _UrlCapture(get_result=brief)
        stdin = json.dumps({"session_id": SID, "cwd": str(self.repo)})
        buf = io.StringIO()
        with mock.patch.object(qdrant_http, "request_json", cap), \
                mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
                mock.patch.object(sys, "stdout", buf), \
                mock.patch.dict(os.environ,
                                {"CLAUDE_PROJECT_DIR": str(self.repo)}):
            rc = self.mat.main()
        self.assertEqual(rc, 0)
        out = buf.getvalue().strip()
        if not out:
            return ""
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]

    def _run_apply(self, revision, brief):
        cap = _UrlCapture(get_result=brief)
        env = {"CLAUDE_PROJECT_DIR": str(self.repo), "CLAUDE_SESSION_ID": SID}
        with mock.patch.object(qdrant_http, "request_json", cap), \
                mock.patch.dict(os.environ, env):
            rc = self.mat._apply_main(str(revision))
        return rc, cap.urls

    def test_sessionstart_asks_for_approval_and_writes_nothing(self):
        ctx = self._run_sessionstart(self._brief(files=self.FILES))
        self.assertFalse((self.repo / "AGENTS.md").exists(),
                         "SessionStart must not apply anything before approval")
        self.assertIn("--apply 3", ctx)
        self.assertIn("DO NOT edit", ctx)
        for spec in self.FILES:
            self.assertIn(spec["path"], ctx)
        # The tutor is never handed the file bodies to retype.
        self.assertNotIn("# Alex, v2", ctx)

    def test_apply_writes_files_and_acks(self):
        rc, urls = self._run_apply(3, self._brief(files=self.FILES))
        self.assertEqual(rc, 0)
        self.assertEqual((self.repo / "AGENTS.md").read_text(encoding="utf-8"),
                         "# Alex, v2\n")
        self.assertEqual(
            (self.repo / "docs" / "new-note.md").read_text(encoding="utf-8"),
            "note\n")
        self.assertTrue(any("/points?" in u or "/points" in u for u in urls))
        for url in urls:
            self.assertIn("/collections/training_", url)

    def test_apply_is_idempotent(self):
        brief = self._brief(files=self.FILES)
        self.assertEqual(self._run_apply(3, brief)[0], 0)
        self.assertEqual(self._run_apply(3, brief)[0], 0)
        self.assertEqual((self.repo / "AGENTS.md").read_text(encoding="utf-8"),
                         "# Alex, v2\n")

    def test_applied_brief_never_re_asks(self):
        brief = self._brief(files=self.FILES)
        self.assertEqual(self._run_apply(3, brief)[0], 0)
        self.assertEqual(self._run_sessionstart(brief), "",
                         "an already-applied brief must stay silent")

    def test_apply_refuses_revision_mismatch(self):
        rc, _urls = self._run_apply(2, self._brief(revision=3, files=self.FILES))
        self.assertEqual(rc, 1)
        self.assertFalse((self.repo / "AGENTS.md").exists(),
                         "a stale approval must change nothing")

    def test_prose_only_brief_keeps_the_ack_path(self):
        ctx = self._run_sessionstart(self._brief())
        self.assertIn("--ack 3", ctx)
        self.assertNotIn("--apply", ctx)
        self.assertEqual(self._run_apply(3, self._brief())[0], 1)

    def test_malformed_files_degrade_to_prose(self):
        ctx = self._run_sessionstart(
            self._brief(files=[{"path": "../escape.md", "content": "x"}]))
        self.assertIn("--ack 3", ctx)
        self.assertFalse((self.repo.parent / "escape.md").exists())

    def test_resolve_target_refuses_repo_escape(self):
        self.assertEqual(self.mat.resolve_target(str(self.repo), "docs/a.md"),
                         (self.repo / "docs" / "a.md").resolve())
        link = self.repo / "away"
        link.symlink_to(Path(self.tmp.name).parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.mat.resolve_target(str(self.repo), "away/escaped.md")


class TestOfflineDegradation(unittest.TestCase):
    """R2-4: hooks no-op cleanly with Qdrant unreachable / config unfilled."""

    def _run_hook(self, name: str, stdin: dict, repo: Path, args=()) -> subprocess.CompletedProcess:
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(repo))
        env.pop("PODZONE_QDRANT_APIKEY", None)
        return subprocess.run(
            [sys.executable, str(HOOKS / f"{name}.py"), *args],
            input=json.dumps(stdin), capture_output=True, text=True,
            env=env, timeout=60)

    def _repo(self, tmp: str, config: str | None) -> Path:
        repo = Path(tmp)
        if config is not None:
            (repo / "training-config.yaml").write_text(config, encoding="utf-8")
        (repo / "logs").mkdir(exist_ok=True)
        return repo

    def test_hooks_exit_zero_qdrant_unreachable(self):
        # 127.0.0.1:9 refuses instantly — a clean "offline" without timeouts.
        offline = CONFIG.replace("https://training.example.cloud:6333",
                                 "http://127.0.0.1:9")
        stdin = {"session_id": SID, "hook_event_name": "SessionStart"}
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, offline)
            stdin["cwd"] = str(repo)
            for name in ("trainee-materialise", "trainee-telemetry"):
                cp = self._run_hook(name, stdin, repo)
                self.assertEqual(cp.returncode, 0,
                                 f"{name} must exit 0 offline: {cp.stderr}")

    def test_materialise_offline_context_is_soft(self):
        offline = CONFIG.replace("https://training.example.cloud:6333",
                                 "http://127.0.0.1:9")
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, offline)
            cp = self._run_hook("trainee-materialise",
                                {"session_id": SID, "cwd": str(repo)}, repo)
        self.assertEqual(cp.returncode, 0)
        out = json.loads(cp.stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("normal, not an error", ctx)

    def test_hooks_exit_zero_config_missing_and_unfilled(self):
        placeholder = CONFIG.replace('"scoped-key"', '"{{TRAINING_DB_API_KEY}}"')
        for config in (None, placeholder):
            with tempfile.TemporaryDirectory() as tmp:
                repo = self._repo(tmp, config)
                stdin = {"session_id": SID, "cwd": str(repo),
                         "hook_event_name": "Stop"}
                for name in ("trainee-materialise", "trainee-telemetry"):
                    cp = self._run_hook(name, stdin, repo)
                    self.assertEqual(cp.returncode, 0,
                                     f"{name} (config={bool(config)}): {cp.stderr}")
                    self.assertEqual(cp.stdout.strip(), "",
                                     f"{name} must stay quiet when unconfigured")

    def test_finalise_guard_exits_zero_with_no_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, CONFIG)
            cp = self._run_hook("trainee-finalise", {}, repo, args=("--guard",))
        self.assertEqual(cp.returncode, 0, cp.stderr)


if __name__ == "__main__":
    unittest.main()
