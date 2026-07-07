"""Tests for lib/jsonl_scrape.scrape()."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import jsonl_scrape  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "sample-session.jsonl"


class TestScrape(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = jsonl_scrape.scrape(FIXTURE)

    # T1: returns required keys
    def test_schema_shape(self) -> None:
        required = {
            "session_id", "workspace", "agent", "cwd",
            "first_message_ts", "last_message_ts", "duration_seconds",
            "model_usage", "total_tokens", "message_counts",
            "work_items", "projects",
            "jsonl_path", "jsonl_mtime",
        }
        self.assertTrue(required.issubset(self.payload.keys()))
        self.assertEqual(self.payload["session_id"], "sample-session")

    # T2: model_usage sums correct
    def test_model_usage_sums(self) -> None:
        mu = self.payload["model_usage"]
        self.assertIn("claude-opus-4-7", mu)
        self.assertIn("claude-sonnet-4-6", mu)
        opus = mu["claude-opus-4-7"]
        self.assertEqual(opus["input_tokens"], 270)
        self.assertEqual(opus["output_tokens"], 135)
        self.assertEqual(opus["cache_creation_input_tokens"], 200)
        self.assertEqual(opus["cache_read_input_tokens"], 800)
        self.assertEqual(opus["cache_creation_5m_input_tokens"], 200)
        self.assertEqual(opus["web_search_requests"], 1)
        self.assertEqual(opus["web_fetch_requests"], 2)
        self.assertEqual(opus["iterations"], 3)
        sonnet = mu["claude-sonnet-4-6"]
        self.assertEqual(sonnet["input_tokens"], 80)
        self.assertEqual(sonnet["iterations"], 1)
        totals = self.payload["total_tokens"]
        self.assertEqual(totals["input_tokens"], 350)
        self.assertEqual(totals["output_tokens"], 175)

    # T3: tool_use entries with usage are counted (D1)
    def test_d1_tool_use_counted(self) -> None:
        # Two of the three opus entries have stop_reason=tool_use. Their input
        # contribution (100 + 120) must appear in totals. If D1 were violated
        # we'd only see the single end_turn entry (50).
        self.assertGreaterEqual(
            self.payload["model_usage"]["claude-opus-4-7"]["input_tokens"], 220
        )

    # T4: message_counts reflects all entry types
    def test_message_counts(self) -> None:
        mc = self.payload["message_counts"]
        self.assertEqual(mc["user"], 2)
        self.assertEqual(mc["assistant"], 5)
        self.assertEqual(mc["attachment"], 1)
        self.assertEqual(mc["system"], 1)
        self.assertEqual(mc["file-history-snapshot"], 1)
        self.assertEqual(mc["queue-operation"], 1)
        self.assertEqual(mc["last-prompt"], 1)

    # T5: malformed line skipped, warning surfaces
    def test_malformed_warning(self) -> None:
        self.assertIn("_warnings", self.payload)
        self.assertEqual(self.payload["_warnings"]["malformed_lines"], 1)

    # T6: empty file → no raise, zero values
    def test_empty_file(self) -> None:
        with tempfile.NamedTemporaryFile(
            suffix=".jsonl", mode="w", delete=False
        ) as f:
            empty_path = f.name
        try:
            p = jsonl_scrape.scrape(empty_path)
            self.assertEqual(p["model_usage"], {})
            self.assertEqual(p["total_tokens"]["input_tokens"], 0)
            self.assertEqual(p["duration_seconds"], 0)
            self.assertIsNone(p["first_message_ts"])
        finally:
            Path(empty_path).unlink()

    # T7: duration_seconds matches fixture spread (00 → 11 = 11s)
    def test_duration(self) -> None:
        self.assertEqual(self.payload["duration_seconds"], 11)
        self.assertEqual(
            self.payload["first_message_ts"], "2026-05-20T10:00:00.000Z"
        )
        self.assertEqual(
            self.payload["last_message_ts"], "2026-05-20T10:00:11.000Z"
        )

    # T8: cwd derivation when no entry has .cwd
    def test_cwd_derived_from_jsonl_parent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td) / "-Users-martincolley-workspace-podzoneTeam"
            parent.mkdir()
            f = parent / "abc.jsonl"
            f.write_text(
                json.dumps({
                    "type": "assistant",
                    "timestamp": "2026-05-20T10:00:00.000Z",
                    "message": {
                        "model": "claude-opus-4-7",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                }) + "\n"
            )
            p = jsonl_scrape.scrape(f)
            self.assertEqual(p["cwd"], "/Users/martincolley/workspace/podzoneTeam")
            self.assertEqual(p["workspace"], "podzoneTeam")

    def test_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            jsonl_scrape.scrape("/nonexistent/path/no.jsonl")

    # T-010: work_items + projects extracted from corpus and cwd slug.
    def test_work_items_extracted_from_corpus_and_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td) / "-Users-x-sessions-hephaestus-2026-05-21-proj034-t010-agent-tooling"
            parent.mkdir()
            f = parent / "deadbeef.jsonl"
            lines = [
                json.dumps({
                    "type": "user",
                    "timestamp": "2026-05-21T10:00:00.000Z",
                    "cwd": "/Users/x/sessions/hephaestus-2026-05-21-proj034-t010/agent-tooling",
                    "message": {"role": "user", "content": "implement PROJ-034/T-010"},
                }),
                json.dumps({
                    "type": "assistant",
                    "timestamp": "2026-05-21T10:00:01.000Z",
                    "message": {
                        "model": "claude-opus-4-7",
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Reading PROJ-033 context too"},
                            {"type": "tool_use", "input": {"command": "grep PROJ-029/T-7"}},
                        ],
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                }),
            ]
            f.write_text("\n".join(lines) + "\n")
            p = jsonl_scrape.scrape(f)
            self.assertIn("PROJ-034/T-010", p["work_items"])
            self.assertIn("PROJ-029/T-007", p["work_items"])
            self.assertIn("PROJ-033", p["projects"])
            self.assertIn("PROJ-034", p["projects"])  # from work item + cwd slug
            self.assertIn("PROJ-029", p["projects"])  # from work item


if __name__ == "__main__":
    unittest.main()
