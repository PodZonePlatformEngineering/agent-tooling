"""Tests for lib/sessions_reader.py — PROJ-034/T-011+T-012 shared library.

T1–T6 cover the algorithmic surface: pagination, env/override key resolution,
window filter construction, and 403 error path. Live cloud verification is
captured in the PR description, not here.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# Force-reload to defeat any stale import from other tests in the suite.
spec = importlib.util.spec_from_file_location(
    "lib.sessions_reader", REPO_ROOT / "lib" / "sessions_reader.py"
)
sessions_reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sessions_reader)  # type: ignore[union-attr]


def _mock_response(status_code: int, body: dict | None = None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = body or {}
    if status_code >= 400:
        from requests import HTTPError  # type: ignore

        r.raise_for_status.side_effect = HTTPError(f"HTTP {status_code}")
    else:
        r.raise_for_status.return_value = None
    return r


def _page(points: list[dict], next_offset: str | None = None) -> dict:
    return {
        "result": {
            "points": [{"id": f"id-{i}", "payload": p} for i, p in enumerate(points)],
            "next_page_offset": next_offset,
        }
    }


class TestSessionsReader(unittest.TestCase):

    # T1 — single-page scroll
    def test_single_page_yields_all_points(self):
        body = _page([{"session_id": "a"}, {"session_id": "b"}])
        with patch.dict("os.environ", {"PODZONE_QDRANT_APIKEY": "k"}):
            with patch.object(
                sessions_reader.requests,
                "post",
                return_value=_mock_response(200, body),
            ) as mp:
                out = list(sessions_reader.scroll_all_sessions())
        self.assertEqual([p["session_id"] for p in out], ["a", "b"])
        self.assertEqual(mp.call_count, 1)

    # T2 — multi-page scroll
    def test_multi_page_paginates(self):
        page1 = _page([{"session_id": "a"}], next_offset="cursor-1")
        page2 = _page([{"session_id": "b"}], next_offset="cursor-2")
        page3 = _page([{"session_id": "c"}], next_offset=None)
        with patch.dict("os.environ", {"PODZONE_QDRANT_APIKEY": "k"}):
            with patch.object(
                sessions_reader.requests,
                "post",
                side_effect=[
                    _mock_response(200, page1),
                    _mock_response(200, page2),
                    _mock_response(200, page3),
                ],
            ) as mp:
                out = list(sessions_reader.scroll_all_sessions(batch_size=1))
        self.assertEqual([p["session_id"] for p in out], ["a", "b", "c"])
        self.assertEqual(mp.call_count, 3)
        # Page 2's request body should carry the offset returned by page 1.
        page2_body = mp.call_args_list[1].kwargs["json"]
        self.assertEqual(page2_body["offset"], "cursor-1")

    # T3 — explicit api_key overrides env
    def test_explicit_api_key_overrides_env(self):
        body = _page([{"session_id": "a"}])
        with patch.dict("os.environ", {"PODZONE_QDRANT_APIKEY": "env-key"}):
            with patch.object(
                sessions_reader.requests,
                "post",
                return_value=_mock_response(200, body),
            ) as mp:
                list(sessions_reader.scroll_all_sessions(api_key="override-key"))
        headers = mp.call_args.kwargs["headers"]
        self.assertEqual(headers["api-key"], "override-key")

    # T4 — missing env + no override raises ValueError
    def test_missing_env_no_override_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError) as cm:
                list(sessions_reader.scroll_all_sessions())
        self.assertIn("PODZONE_QDRANT_APIKEY", str(cm.exception))

    # T5 — window query builds correct filter
    def test_window_query_builds_filter(self):
        body = _page([{"session_id": "a"}])
        with patch.dict("os.environ", {"PODZONE_QDRANT_APIKEY": "k"}):
            with patch.object(
                sessions_reader.requests,
                "post",
                return_value=_mock_response(200, body),
            ) as mp:
                out = sessions_reader.query_sessions_in_window(
                    start_iso="2026-05-14T00:00:00+00:00",
                    end_iso="2026-05-21T00:00:00+00:00",
                )
        self.assertEqual(len(out), 1)
        sent = mp.call_args.kwargs["json"]
        self.assertIn("filter", sent)
        must = sent["filter"]["must"]
        self.assertEqual(must[0]["key"], "last_message_ts")
        self.assertEqual(must[0]["range"]["gte"], "2026-05-14T00:00:00+00:00")
        self.assertEqual(must[0]["range"]["lte"], "2026-05-21T00:00:00+00:00")

    # T6 — 403 raises with STATUS.md hint
    def test_403_raises_clear_error(self):
        with patch.dict("os.environ", {"PODZONE_QDRANT_APIKEY": "k"}):
            with patch.object(
                sessions_reader.requests,
                "post",
                return_value=_mock_response(403),
            ):
                with self.assertRaises(sessions_reader.SessionsReaderError) as cm:
                    list(sessions_reader.scroll_all_sessions())
        self.assertIn("STATUS.md", str(cm.exception))
        self.assertIn("403", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
