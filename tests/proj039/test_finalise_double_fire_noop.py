"""Tests for the T-100 double-fire no-op guard (CC-418).

The live gap (T-066 shakedown close-out, 2026-07-16): a manual `/session-end`
finalise (the sidebar `/exit` wrapper) followed by the harness SessionEnd fire at
window close re-delivers the same session end. Before T-100 there was no
short-circuit — only step-level idempotency.

The guard's discriminator is **transcript growth since completion**, not bare
`complete: true` — an F14 re-armed continuation resumes a finalised sid, does new
work (transcript grows), and its exit MUST re-run the finalise to bank that work.
Only a byte-identical transcript reads as a duplicate delivery.

Red-on-main proof: `finalise_ledger.unchanged_since_complete` does not exist
before this change (AttributeError), and `finalise_session` re-runs `begin()`
unconditionally (entry flips back to complete=False, attempts increments).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location(
    "session_end_finalise", str(REPO_ROOT / "hooks" / "session-end-finalise.py")
)
sef = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sef)  # type: ignore

from lib import finalise_ledger  # noqa: E402

SID = "aa100aa1-0000-0000-0000-000000000000"


class _LedgerFixture(unittest.TestCase):
    """Redirected ledger (PODZONE_LOG_DIR) + a real transcript file."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self._tmp.name) / "logs"
        self.transcript = Path(self._tmp.name) / "transcript.jsonl"
        self.transcript.write_text('{"type":"assistant"}\n', encoding="utf-8")
        self._env = os.environ.get("PODZONE_LOG_DIR")
        # finalise_session exports PODZONE_SESSION_ID process-wide (T-048 sid-keyed
        # logs) — snapshot it too, or this module sid-keys every later test's logs.
        self._sid_env = os.environ.get("PODZONE_SESSION_ID")
        os.environ["PODZONE_LOG_DIR"] = str(self.log_dir)

    def tearDown(self) -> None:
        if self._env is None:
            os.environ.pop("PODZONE_LOG_DIR", None)
        else:
            os.environ["PODZONE_LOG_DIR"] = self._env
        if self._sid_env is None:
            os.environ.pop("PODZONE_SESSION_ID", None)
        else:
            os.environ["PODZONE_SESSION_ID"] = self._sid_env
        self._tmp.cleanup()


class TestUnchangedSinceComplete(_LedgerFixture):
    def test_complete_with_unchanged_transcript_is_noop_condition(self) -> None:
        finalise_ledger.begin(SID, str(self.transcript), "/tmp/repo")
        finalise_ledger.complete(SID)
        self.assertTrue(finalise_ledger.unchanged_since_complete(SID))

    def test_grown_transcript_must_rerun(self) -> None:
        finalise_ledger.begin(SID, str(self.transcript), "/tmp/repo")
        finalise_ledger.complete(SID)
        with open(self.transcript, "a", encoding="utf-8") as fh:
            fh.write('{"type":"user"}\n')  # the F14 re-armed continuation
        self.assertFalse(finalise_ledger.unchanged_since_complete(SID))

    def test_incomplete_entry_is_never_noop(self) -> None:
        finalise_ledger.begin(SID, str(self.transcript), "/tmp/repo")
        self.assertFalse(finalise_ledger.unchanged_since_complete(SID))

    def test_pre_t100_entry_without_snapshot_fails_open(self) -> None:
        # A ledger written by an older complete() has no transcript_bytes —
        # must re-run (fail open), never skip on ambiguity.
        finalise_ledger.begin(SID, str(self.transcript), "/tmp/repo")
        ledger = finalise_ledger.load()
        ledger[SID]["complete"] = True
        ledger[SID].pop("transcript_bytes", None)
        finalise_ledger._save(ledger)
        self.assertFalse(finalise_ledger.unchanged_since_complete(SID))

    def test_missing_transcript_fails_open(self) -> None:
        finalise_ledger.begin(SID, str(self.transcript), "/tmp/repo")
        finalise_ledger.complete(SID)
        self.transcript.unlink()
        self.assertFalse(finalise_ledger.unchanged_since_complete(SID))


class TestFinaliseSessionShortCircuit(_LedgerFixture):
    def test_duplicate_fire_short_circuits_before_begin(self) -> None:
        finalise_ledger.begin(SID, str(self.transcript), "/tmp/repo")
        finalise_ledger.complete(SID)
        attempts_before = finalise_ledger.attempts(SID)

        rc = sef.finalise_session(SID, str(self.transcript), "/tmp/repo")

        self.assertEqual(rc, 0)
        entry = finalise_ledger.load()[SID]
        # begin() never ran: entry still complete, attempts unchanged.
        self.assertTrue(entry.get("complete"))
        self.assertEqual(finalise_ledger.attempts(SID), attempts_before)


if __name__ == "__main__":
    unittest.main()
