"""Test the PROJ-039 additive `tool_usage` field on lib/jsonl_scrape.scrape().

Feeds rollup.tool_usage (R-013/R-014). Per-tool counts come from tool_use blocks
in assistant turns; this is the same single pass that produces cost_tokens, so
DT-006 reconciliation holds.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import jsonl_scrape  # noqa: E402


class TestToolUsage(unittest.TestCase):

    def _scrape(self, lines: list[dict]) -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(x) for x in lines) + "\n")
            path = f.name
        try:
            return jsonl_scrape.scrape(path)
        finally:
            Path(path).unlink()

    def test_counts_tool_uses_by_name(self) -> None:
        p = self._scrape([
            {"type": "assistant", "timestamp": "2026-06-11T10:00:00.000Z",
             "message": {"model": "m", "usage": {"input_tokens": 1},
                         "content": [
                             {"type": "tool_use", "name": "Bash", "input": {}},
                             {"type": "tool_use", "name": "Bash", "input": {}},
                             {"type": "tool_use", "name": "Read", "input": {}},
                             {"type": "text", "text": "ok"},
                         ]}},
            {"type": "user", "timestamp": "2026-06-11T10:00:01.000Z",
             "message": {"content": [
                 {"type": "tool_result", "content": "x"},
                 # a tool_use block on a user entry must NOT be counted
                 {"type": "tool_use", "name": "Ghost", "input": {}},
             ]}},
        ])
        self.assertEqual(p["tool_usage"], {"Bash": 2, "Read": 1})

    def test_empty_when_no_tools(self) -> None:
        p = self._scrape([
            {"type": "assistant", "timestamp": "2026-06-11T10:00:00.000Z",
             "message": {"model": "m", "usage": {"input_tokens": 1},
                         "content": [{"type": "text", "text": "hi"}]}},
        ])
        self.assertEqual(p["tool_usage"], {})

    def test_tool_usage_key_always_present(self) -> None:
        p = self._scrape([])
        self.assertIn("tool_usage", p)
        self.assertEqual(p["tool_usage"], {})


if __name__ == "__main__":
    unittest.main()
