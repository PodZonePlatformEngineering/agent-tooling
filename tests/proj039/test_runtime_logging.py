#!/usr/bin/env python3
"""
test_runtime_logging.py — PROJ-039/T-029 (logging) + T-030 (finalise ledger).

Covers:
  * runtime_log.log_library — writes timestamped, caller-identified lines to
    logs/libraries.log; resolves the dir from PODZONE_LOG_DIR / CLAUDE_PROJECT_DIR;
    NEVER raises on an unwritable target (best-effort contract).
  * primitives/log_primitive.sh — sourced + standalone; survives `set -e`; writes
    logs/primitives.log; swallows a failing target.
  * finalise_ledger — begin/record_step/complete/is_complete/unfinalised, and the
    re-run idempotency the SessionStart guard relies on (brief_pr skip).
  * session-end-finalise.py --guard — no-op on an empty ledger; detects + clears a
    partial entry (re-run marks it complete).
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

LOG_PRIMITIVE = REPO_ROOT / "primitives" / "log_primitive.sh"
FINALISE_HOOK = REPO_ROOT / "hooks" / "session-end-finalise.py"


class TestRuntimeLog(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("PODZONE_LOG_DIR", "CLAUDE_PROJECT_DIR")}
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.logdir = Path(self._td.name) / "logs"
        os.environ["PODZONE_LOG_DIR"] = str(self.logdir)
        os.environ.pop("CLAUDE_PROJECT_DIR", None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._td.cleanup()

    def test_writes_caller_identified_line(self):
        from lib import runtime_log
        runtime_log.log_library("session_finalise", "rollup attached", session_id="sid-1")
        # T-048: an explicit session_id sid-keys the filename (libraries-{sid8}.log).
        text = (self.logdir / "libraries-sid-1.log").read_text()
        self.assertIn("session_finalise", text)
        self.assertIn("session=sid-1", text)
        self.assertIn("rollup attached", text)
        self.assertIn("[INFO]", text)
        # ISO-ish UTC stamp prefix
        self.assertRegex(text, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z ")

    def test_level_and_no_session(self):
        from lib import runtime_log
        runtime_log.log_library("telemetry_repo", "push failed", level="WARN")
        text = (self.logdir / "libraries.log").read_text()
        self.assertIn("[WARN] tooling=unknown telemetry_repo: push failed", text)
        self.assertNotIn("session=", text)

    def test_tooling_stamp_reads_manifest_version(self):
        # T-055: logs/'s parent .claude/tooling-manifest.json version stamps every line.
        from lib import runtime_log
        project = self.logdir.parent
        (project / ".claude").mkdir(parents=True, exist_ok=True)
        (project / ".claude" / "tooling-manifest.json").write_text(
            json.dumps({"version": "1.2.3"}), encoding="utf-8")
        runtime_log.log_library("session_finalise", "hello")
        text = (self.logdir / "libraries.log").read_text()
        self.assertIn("[INFO] tooling=v1.2.3 session_finalise: hello", text)

    def test_tooling_stamp_unknown_when_manifest_missing(self):
        from lib import runtime_log
        self.assertEqual(runtime_log.tooling_version(), "unknown")

    def test_tooling_stamp_unknown_when_manifest_corrupt(self):
        from lib import runtime_log
        project = self.logdir.parent
        (project / ".claude").mkdir(parents=True, exist_ok=True)
        (project / ".claude" / "tooling-manifest.json").write_text(
            "{not valid json", encoding="utf-8")
        self.assertEqual(runtime_log.tooling_version(), "unknown")

    def test_unwritable_dir_never_raises(self):
        from lib import runtime_log
        os.environ["PODZONE_LOG_DIR"] = "/proc/cannot/write/here/logs"
        # Must not raise — best-effort.
        runtime_log.log_library("x", "y")

    def test_resolve_prefers_project_dir(self):
        from lib import runtime_log
        os.environ.pop("PODZONE_LOG_DIR", None)
        os.environ["CLAUDE_PROJECT_DIR"] = "/home/agent/repo"
        self.assertEqual(runtime_log.resolve_log_dir(), Path("/home/agent/repo/logs"))


class TestLogPrimitiveShell(unittest.TestCase):
    def _run(self, script_body, env_extra):
        import tempfile
        env = dict(os.environ)
        env.update(env_extra)
        return subprocess.run(["bash", "-c", script_body], capture_output=True,
                              text=True, env=env)

    def test_sourced_under_set_e(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            logdir = Path(td) / "logs"
            body = (
                f"set -euo pipefail; source '{LOG_PRIMITIVE}'; "
                f"log_primitive embed-text.sh 'embedded 41 chars'; echo done"
            )
            r = self._run(body, {"PODZONE_LOG_DIR": str(logdir)})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("done", r.stdout)
            log = (logdir / "primitives.log").read_text()
            self.assertIn("embed-text.sh: embedded 41 chars", log)
            self.assertIn("[INFO]", log)

    def test_standalone_invocation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            logdir = Path(td) / "logs"
            r = self._run(f"bash '{LOG_PRIMITIVE}' getSecret.sh 'lookup foo'",
                          {"PODZONE_LOG_DIR": str(logdir)})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("getSecret.sh: lookup foo", (logdir / "primitives.log").read_text())

    def test_unwritable_target_does_not_abort_caller(self):
        # set -e + unwritable log dir: the caller must still reach `echo ok`.
        body = (
            f"set -euo pipefail; source '{LOG_PRIMITIVE}'; "
            f"log_primitive x y; echo ok"
        )
        r = self._run(body, {"PODZONE_LOG_DIR": "/proc/nope/logs"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok", r.stdout)


class TestFinaliseLedger(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("PODZONE_LOG_DIR")
        os.environ["PODZONE_LOG_DIR"] = str(Path(self._td.name) / "logs")
        # Fresh import state isn't needed; module reads env each call.

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("PODZONE_LOG_DIR", None)
        else:
            os.environ["PODZONE_LOG_DIR"] = self._saved
        self._td.cleanup()

    def test_begin_record_complete_cycle(self):
        from lib import finalise_ledger
        sid = "sess-abc"
        finalise_ledger.begin(sid, "/tmp/t.jsonl")
        self.assertFalse(finalise_ledger.is_complete(sid))
        self.assertEqual([s for s, _ in finalise_ledger.unfinalised()], [sid])

        finalise_ledger.record_step(sid, "response", "done")
        finalise_ledger.record_step(sid, "brief_pr", "done")
        self.assertEqual(finalise_ledger.step_status(sid, "response"), "done")
        self.assertEqual(finalise_ledger.step_status(sid, "brief_pr"), "done")

        finalise_ledger.complete(sid)
        self.assertTrue(finalise_ledger.is_complete(sid))
        self.assertEqual(finalise_ledger.unfinalised(), [])

    def test_transcript_path_preserved_on_rerun(self):
        from lib import finalise_ledger
        sid = "sess-keep"
        finalise_ledger.begin(sid, "/path/one.jsonl")
        finalise_ledger.begin(sid, "")  # re-run with no path must not erase it
        entry = dict(finalise_ledger.load())[sid]
        self.assertEqual(entry["transcript_path"], "/path/one.jsonl")

    def test_partial_detected_until_complete(self):
        from lib import finalise_ledger
        finalise_ledger.begin("p1", "")
        finalise_ledger.record_step("p1", "response", "done")
        # telemetry_push failed → still partial
        finalise_ledger.record_step("p1", "telemetry_push", "failed")
        self.assertIn("p1", [s for s, _ in finalise_ledger.unfinalised()])


def _load_hook_module():
    """Import session-end-finalise.py (hyphenated) as a module object."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_seh", FINALISE_HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestLastAssistantText(unittest.TestCase):
    """T-047: the response scrape must find the session's OWN final turn — never a
    subagent's — in the headless `claude -p` transcript shape."""

    def _write(self, entries):
        import tempfile
        fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        for e in entries:
            fh.write(json.dumps(e) + "\n")
        fh.close()
        return fh.name

    def _asst(self, text, sidechain=False):
        return {"type": "assistant", "isSidechain": sidechain,
                "message": {"content": [{"type": "text", "text": text}]}}

    def test_skips_trailing_subagent_turn(self):
        mod = _load_hook_module()
        path = self._write([
            self._asst("the session's real final summary"),
            self._asst("a subagent's closing turn", sidechain=True),
        ])
        self.assertEqual(mod._last_assistant_text(path),
                         "the session's real final summary")

    def test_plain_transcript_unaffected(self):
        mod = _load_hook_module()
        path = self._write([
            self._asst("earlier turn"),
            self._asst("final main-thread turn"),
        ])
        self.assertEqual(mod._last_assistant_text(path), "final main-thread turn")


class TestFinaliseSessionMocked(unittest.TestCase):
    """Drive finalise_session with the real lib functions patched to exercise the
    ledger state machine, the retry budget, and brief_pr idempotency — no network.

    Patches attributes on the already-imported lib modules (robust against the
    package-attribute binding that defeats sys.modules swaps)."""

    def setUp(self):
        import tempfile
        from unittest import mock
        self._td = tempfile.TemporaryDirectory()
        self._saved = {k: os.environ.get(k) for k in
                       ("PODZONE_LOG_DIR", "PODZONEAGENTTEAM_REPO", "PODZONE_SESSION_ID")}
        os.environ["PODZONE_LOG_DIR"] = str(Path(self._td.name) / "logs")
        os.environ.pop("PODZONEAGENTTEAM_REPO", None)  # skip steps 6/7 (agent repo)
        self.mock = mock
        # Ensure the lib submodules are imported so we can patch their attrs.
        import lib.session_substrate  # noqa: F401
        import lib.telemetry_repo     # noqa: F401
        import lib.cst_cleanup        # noqa: F401

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._td.cleanup()

    def _patches(self, *, fail):
        mock = self.mock

        def maybe_raise(*a, **k):
            if fail:
                raise RuntimeError("simulated step failure")

        def rollup(*a, **k):
            if fail:
                raise RuntimeError("simulated rollup failure")
            return {"tool_usage": {"Bash": 3}, "cost_tokens": {"opus": {}}}

        def push(*a, **k):
            if fail:
                raise RuntimeError("simulated push failure")
            return {"committed": True, "pushed": True}

        return [
            mock.patch("lib.session_substrate.upsert_response", side_effect=maybe_raise),
            mock.patch("lib.session_substrate.compute_rollup", side_effect=rollup),
            mock.patch("lib.session_substrate.attach_rollup", side_effect=maybe_raise),
            mock.patch("lib.telemetry_repo.commit_and_push", side_effect=push),
            mock.patch("lib.cst_cleanup.delete_raw_tool_events",
                       return_value={"deleted_before_count": 5}),
        ]

    def _run(self, sid, transcript, *, fail):
        import contextlib
        mod = _load_hook_module()
        with contextlib.ExitStack() as stack:
            for p in self._patches(fail=fail):
                stack.enter_context(p)
            mod.finalise_session(sid, transcript)

    def test_success_marks_complete(self):
        from lib import finalise_ledger
        self._run("ok-sess", "/tmp/x.jsonl", fail=False)
        self.assertTrue(finalise_ledger.is_complete("ok-sess"))
        self.assertEqual(finalise_ledger.step_status("ok-sess", "response"), "done")
        self.assertEqual(finalise_ledger.step_status("ok-sess", "rollup"), "done")
        self.assertEqual(finalise_ledger.step_status("ok-sess", "telemetry_push"), "done")
        self.assertEqual(finalise_ledger.step_status("ok-sess", "cst_prune"), "done")

    def test_reaching_end_marks_complete_even_with_step_failures(self):
        # A pass that reaches the end is `complete` regardless of step failures —
        # `complete` means "not truncated", not "all steps succeeded". Per-step
        # failures are recorded for diagnostics (here telemetry/cst fail).
        from lib import finalise_ledger
        self._run("fail-sess", "", fail=True)
        self.assertTrue(finalise_ledger.is_complete("fail-sess"))
        self.assertEqual(finalise_ledger.step_status("fail-sess", "telemetry_push"), "failed")
        # cst_prune is gated on the push landing → skipped, not failed.
        self.assertEqual(finalise_ledger.step_status("fail-sess", "cst_prune"), "skipped")

    def test_truncated_session_recovered_by_guard(self):
        # Simulate a TRUNCATED finalise: begin() ran but the hook was killed before
        # complete(). The session is a detectable partial; the SessionStart guard
        # re-runs it to completion.
        import contextlib
        from lib import finalise_ledger
        finalise_ledger.begin("trunc-sess", "/tmp/x.jsonl")
        finalise_ledger.record_step("trunc-sess", "response", "done")
        # (killed here — no complete())
        self.assertFalse(finalise_ledger.is_complete("trunc-sess"))
        self.assertIn("trunc-sess", [s for s, _ in finalise_ledger.unfinalised()])

        mod = _load_hook_module()
        with contextlib.ExitStack() as stack:
            for p in self._patches(fail=False):
                stack.enter_context(p)
            mod.run_guard()
        self.assertTrue(finalise_ledger.is_complete("trunc-sess"))
        self.assertEqual(finalise_ledger.unfinalised(), [])

    def test_brief_pr_not_repeated_on_rerun(self):
        # brief_pr is the one non-idempotent step. Once recorded done, a re-run must
        # skip it (no duplicate PR). PODZONEAGENTTEAM_REPO is unset so the live path
        # is skipped anyway; assert the ledger guard short-circuits a prior `done`.
        import contextlib
        from lib import finalise_ledger
        finalise_ledger.begin("pr-sess", "")
        finalise_ledger.record_step("pr-sess", "brief_pr", "done")
        mod = _load_hook_module()
        with contextlib.ExitStack() as stack:
            for p in self._patches(fail=False):
                stack.enter_context(p)
            mod.finalise_session("pr-sess", "")
        # Still recorded done — never downgraded / re-attempted.
        self.assertEqual(finalise_ledger.step_status("pr-sess", "brief_pr"), "done")


class TestGuardSmoke(unittest.TestCase):
    def test_guard_noop_on_empty_ledger(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ)
            env["PODZONE_LOG_DIR"] = str(Path(td) / "logs")
            env["CLAUDE_PROJECT_DIR"] = td
            r = subprocess.run(
                [sys.executable, str(FINALISE_HOOK), "--guard"],
                capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            # No partials → no "detected UNFINALISED" line.
            self.assertNotIn("detected UNFINALISED", r.stderr)


if __name__ == "__main__":
    unittest.main()
