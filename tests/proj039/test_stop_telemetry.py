"""Tests for lib/stop_telemetry — the T-071 (CC-381) Stop payload assembly.

Fixture-JSONL unit tests for the three extractors (last_assistant_message,
background_tasks, session_crons), the bounded tail-read with widen-on-miss, the
embed-input switch (message text, constant only as empty fallback), the
deterministic turn-linked point id, and best-effort entrypoint behaviour
(hooks/stop-telemetry.py must exit 0 with no API key / no Ollama).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import stop_telemetry  # noqa: E402

HOOK = REPO_ROOT / "hooks" / "stop-telemetry.py"


def assistant(text=None, *, stop_reason=None, sidechain=False, uuid_="",
              tool_uses=None, timestamp="2026-07-08T10:00:00.000Z"):
    """An assistant transcript record. ``text`` may be a str or list of strs
    (multiple text blocks); ``tool_uses`` a list of tool_use block dicts."""
    content = []
    if text is not None:
        texts = [text] if isinstance(text, str) else text
        content += [{"type": "text", "text": t} for t in texts]
    content += tool_uses or []
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "uuid": uuid_ or str(uuid.uuid4()),
        "timestamp": timestamp,
        "message": {"role": "assistant", "content": content,
                    "stop_reason": stop_reason},
    }


def tool_result(tool_use_id, content, *, background_task_id=None):
    rec = {
        "type": "user",
        "isSidechain": False,
        "uuid": str(uuid.uuid4()),
        "timestamp": "2026-07-08T10:00:01.000Z",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id,
             "content": content, "is_error": False},
        ]},
    }
    if background_task_id:
        rec["toolUseResult"] = {"backgroundTaskId": background_task_id}
    return rec


def tool_use(name, input_, id_="toolu_x"):
    return {"type": "tool_use", "id": id_, "name": name, "input": input_}


def write_jsonl(records, path: Path) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


class TestLastAssistantMessage(unittest.TestCase):
    def test_picks_last_end_turn_and_concatenates_blocks(self):
        records = [
            assistant("old turn", stop_reason="end_turn", uuid_="u-old"),
            assistant("mid-turn tool text", stop_reason="tool_use"),
            assistant(["final answer", "second block"],
                      stop_reason="end_turn", uuid_="u-final"),
        ]
        out = stop_telemetry.extract_last_assistant_message(records)
        self.assertEqual(out["text"], "final answer\n\nsecond block")
        self.assertEqual(out["source"], "end_turn")
        self.assertEqual(out["turn_uuid"], "u-final")

    def test_sidechain_records_never_qualify(self):
        records = [
            assistant("main turn", stop_reason="end_turn", uuid_="u-main"),
            assistant("subagent turn", stop_reason="end_turn", sidechain=True),
        ]
        out = stop_telemetry.extract_last_assistant_message(records)
        self.assertEqual(out["turn_uuid"], "u-main")

    def test_fallback_when_no_end_turn(self):
        records = [assistant("only tool-flow text", stop_reason="tool_use")]
        out = stop_telemetry.extract_last_assistant_message(records)
        self.assertEqual(out["text"], "only tool-flow text")
        self.assertEqual(out["source"], "fallback")

    def test_empty_when_nothing_found(self):
        records = [{"type": "user", "message": {"content": []}},
                   assistant(None, stop_reason="end_turn")]  # no text blocks
        out = stop_telemetry.extract_last_assistant_message(records)
        self.assertEqual(out, {"text": "", "source": "", "turn_uuid": ""})

    def test_text_capped_at_16kb(self):
        big = "x" * (stop_telemetry.MESSAGE_CAP_CHARS + 500)
        out = stop_telemetry.extract_last_assistant_message(
            [assistant(big, stop_reason="end_turn")]
        )
        self.assertEqual(len(out["text"]), stop_telemetry.MESSAGE_CAP_CHARS)


class TestTailReadWiden(unittest.TestCase):
    def test_widens_only_on_miss(self):
        """The turn-ending record sits before the tail window: the initial read
        misses, the ladder widens (×8 then whole file) and finds it."""
        target = assistant("the answer", stop_reason="end_turn", uuid_="u-t")
        padding = [
            {"type": "attachment", "pad": "p" * 200} for _ in range(40)
        ]
        with tempfile.TemporaryDirectory() as td:
            path = write_jsonl([target] + padding, Path(td) / "t.jsonl")
            size = path.stat().st_size
            window = 128
            self.assertGreater(size, window * stop_telemetry.WIDEN_FACTOR,
                               "fixture must overflow the widened window too")
            records, msg = stop_telemetry.read_transcript_for_stop(
                str(path), window=window
            )
            self.assertEqual(msg["turn_uuid"], "u-t")
            self.assertEqual(len(records), 41)  # whole file was read

    def test_partial_first_line_dropped_and_garbage_skipped(self):
        target = assistant("tail answer", stop_reason="end_turn", uuid_="u-tail")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t.jsonl"
            path.write_text(
                json.dumps({"type": "attachment", "pad": "p" * 500}) + "\n"
                + "{not-json\n"
                + json.dumps(target) + "\n"
            )
            records, msg = stop_telemetry.read_transcript_for_stop(
                str(path), window=300
            )
            self.assertEqual(msg["turn_uuid"], "u-tail")

    def test_missing_or_empty_path(self):
        records, msg = stop_telemetry.read_transcript_for_stop("")
        self.assertEqual((records, msg["text"]), ([], ""))
        records, msg = stop_telemetry.read_transcript_for_stop("/nonexistent/x.jsonl")
        self.assertEqual((records, msg["text"]), ([], ""))


class TestBackgroundTasks(unittest.TestCase):
    def test_background_bash_pending_with_task_id(self):
        records = [
            assistant(None, tool_uses=[tool_use(
                "Bash", {"command": "sleep 99", "run_in_background": True,
                         "description": "long build"}, id_="toolu_bg")]),
            tool_result("toolu_bg",
                        "Command running in background with ID: bj7qvs89u. "
                        "Output is being written to: /tmp/x.output.",
                        background_task_id="bj7qvs89u"),
        ]
        out = stop_telemetry.extract_background_tasks(records)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["type"], "bash")
        self.assertEqual(out[0]["description"], "long build")
        self.assertEqual(out[0]["task_id"], "bj7qvs89u")

    def test_completion_mention_clears_the_task(self):
        records = [
            assistant(None, tool_uses=[tool_use(
                "Bash", {"command": "make", "run_in_background": True},
                id_="toolu_bg")]),
            tool_result("toolu_bg",
                        "Command running in background with ID: abc123x."),
            {"type": "user", "isSidechain": False,
             "message": {"role": "user", "content": [
                 {"type": "text",
                  "text": "Background task abc123x completed (exit 0)"}]}},
        ]
        self.assertEqual(stop_telemetry.extract_background_tasks(records), [])

    def test_synchronous_bash_ignored(self):
        records = [
            assistant(None, tool_uses=[tool_use(
                "Bash", {"command": "ls"}, id_="toolu_sync")]),
            tool_result("toolu_sync", "file-a\nfile-b"),
        ]
        self.assertEqual(stop_telemetry.extract_background_tasks(records), [])

    def test_agent_spawn_pending_until_final_result(self):
        spawn = assistant(None, tool_uses=[tool_use(
            "Agent", {"description": "review the diff",
                      "prompt": "…"}, id_="toolu_ag")])
        launch = tool_result("toolu_ag", "Async agent launched in background: a1b2")
        final = tool_result("toolu_ag", "Review complete: 2 findings.")
        pending = stop_telemetry.extract_background_tasks([spawn, launch])
        self.assertEqual([(t["type"], t["description"]) for t in pending],
                         [("agent", "review the diff")])
        self.assertEqual(
            stop_telemetry.extract_background_tasks([spawn, launch, final]), []
        )

    def test_foreground_agent_not_tracked(self):
        records = [assistant(None, tool_uses=[tool_use(
            "Agent", {"description": "sync run", "run_in_background": False},
            id_="toolu_fg")])]
        self.assertEqual(stop_telemetry.extract_background_tasks(records), [])

    def test_empty_default(self):
        self.assertEqual(stop_telemetry.extract_background_tasks([]), [])


class TestSessionCrons(unittest.TestCase):
    CRON_ID = "3f1c9a52-0000-4000-8000-1234567890ab"

    def _create_records(self):
        return [
            assistant(None, tool_uses=[tool_use(
                "CronCreate",
                {"schedule": "*/30 * * * *", "prompt": "/babysit-prs"},
                id_="toolu_cc")]),
            tool_result("toolu_cc", f"Created cron {self.CRON_ID} (every 30m)"),
        ]

    def test_croncreate_captured_with_id_from_result(self):
        out = stop_telemetry.extract_session_crons(self._create_records())
        self.assertEqual(out, [{
            "id": self.CRON_ID, "schedule": "*/30 * * * *",
            "prompt": "/babysit-prs",
        }])

    def test_crondelete_removes_matching_entry(self):
        records = self._create_records() + [
            assistant(None, tool_uses=[tool_use(
                "CronDelete", {"id": self.CRON_ID}, id_="toolu_cd")]),
        ]
        self.assertEqual(stop_telemetry.extract_session_crons(records), [])

    def test_empty_default(self):
        self.assertEqual(stop_telemetry.extract_session_crons([]), [])


class TestPayloadAssembly(unittest.TestCase):
    def test_build_payload_shape(self):
        with tempfile.TemporaryDirectory() as td:
            transcript = write_jsonl(
                [assistant("all done", stop_reason="end_turn", uuid_="u-p")],
                Path(td) / "t.jsonl",
            )
            payload = stop_telemetry.build_payload(
                {"session_id": "sess-1", "cwd": td,
                 "transcript_path": str(transcript)},
                now_iso="2026-07-08T12:00:00+00:00",
            )
        self.assertEqual(payload["event_type"], "Stop")
        self.assertEqual(payload["session_id"], "sess-1")
        self.assertEqual(payload["timestamp"], "2026-07-08T12:00:00+00:00")
        self.assertEqual(payload["stop_reason"], "end_turn")
        self.assertEqual(payload["last_assistant_message"], "all done")
        self.assertEqual(payload["message_source"], "end_turn")
        self.assertEqual(payload["turn_uuid"], "u-p")
        self.assertEqual(payload["background_tasks"], [])
        self.assertEqual(payload["session_crons"], [])
        # tmpdir is not a git repo → the probes degrade to "unknown"
        self.assertEqual(payload["repository"]["git_branch"], "unknown")
        self.assertIn(payload["environment"]["platform"], ("darwin", "linux"))

    def test_build_payload_without_transcript(self):
        payload = stop_telemetry.build_payload({"session_id": "sess-2"})
        self.assertEqual(payload["last_assistant_message"], "")
        self.assertEqual(payload["background_tasks"], [])
        self.assertEqual(payload["session_crons"], [])

    def test_embed_input_is_message_with_constant_fallback(self):
        self.assertEqual(
            stop_telemetry.embed_input_for(
                {"last_assistant_message": "the message",
                 "stop_reason": "end_turn"}),
            "the message",
        )
        self.assertEqual(
            stop_telemetry.embed_input_for(
                {"last_assistant_message": "", "stop_reason": "end_turn"}),
            "Stop: end_turn",
        )

    def test_point_id_turn_linked_deterministic_with_uuid4_fallback(self):
        a = stop_telemetry.point_id_for_stop("sess-1", "u-1")
        b = stop_telemetry.point_id_for_stop("sess-1", "u-1")
        c = stop_telemetry.point_id_for_stop("sess-1", "u-2")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertNotEqual(stop_telemetry.point_id_for_stop("sess-1"),
                            stop_telemetry.point_id_for_stop("sess-1"))


class TestEntrypointBestEffort(unittest.TestCase):
    """hooks/stop-telemetry.py must exit 0 in a bare subprocess (no API key)."""

    def _run(self, stdin: str):
        env = dict(os.environ)
        env.pop("PODZONE_QDRANT_APIKEY", None)
        # Point Ollama somewhere unroutable so an embed can't accidentally run.
        env["OLLAMA_HOST"] = "http://127.0.0.1:9"
        return subprocess.run(
            [sys.executable, str(HOOK)], input=stdin, env=env,
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
        )

    def test_empty_stdin_exits_zero(self):
        p = self._run("")
        self.assertEqual(p.returncode, 0, msg=p.stderr)

    def test_valid_input_no_key_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            transcript = write_jsonl(
                [assistant("done", stop_reason="end_turn")],
                Path(td) / "t.jsonl",
            )
            p = self._run(json.dumps({
                "session_id": "sess-e", "cwd": td,
                "transcript_path": str(transcript),
            }))
        self.assertEqual(p.returncode, 0, msg=p.stderr)
        self.assertIn("skipped", p.stderr)


if __name__ == "__main__":
    unittest.main()
