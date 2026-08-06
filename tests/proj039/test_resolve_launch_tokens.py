"""Tests for tools/resolve-launch-tokens.py (PROJ-039/T-210).

Confirms the resolver reads secretctl-shaped env var names from its own
process env (as `secret_run` would inject them), writes a 0600 file, and
fails loud (nonzero exit, no partial file assumed valid) on a missing value
-- never silently emits a resolved file with a placeholder or empty token.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "resolve-launch-tokens.py"

spec = importlib.util.spec_from_file_location("resolve_launch_tokens", SCRIPT)
resolve_launch_tokens = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolve_launch_tokens)  # type: ignore[union-attr]


class TestSecretctlEnvName(unittest.TestCase):
    def test_hyphenated_key_matches_confirmed_live_transform(self):
        # Live-verified 2026-08-05: claude-oath-token-colleym -> CLAUDE_OATH_TOKEN_COLLEYM
        self.assertEqual(
            resolve_launch_tokens.secretctl_env_name("claude-oath-token-colleym"),
            "CLAUDE_OATH_TOKEN_COLLEYM",
        )

    def test_slash_also_becomes_underscore(self):
        self.assertEqual(resolve_launch_tokens.secretctl_env_name("db/prod"), "DB_PROD")


class TestResolveEndToEnd(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.td = Path(self._tmp.name)

    def _run(self, template, env_extra):
        template_path = self.td / "template.json"
        out_path = self.td / "resolved.json"
        template_path.write_text(json.dumps(template))
        env = {**os.environ, **env_extra}
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--template", str(template_path),
             "--out", str(out_path)],
            env=env, capture_output=True, text=True,
        )
        return result, out_path

    def test_all_present_resolves_and_chmods_0600(self):
        template = [{"name": "colleym", "secret": "claude-oath-token-colleym"}]
        result, out_path = self._run(template, {"CLAUDE_OATH_TOKEN_COLLEYM": "fake-token-value"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("fake-token-value", result.stdout)
        data = json.loads(out_path.read_text())
        self.assertEqual(data, [{"name": "colleym", "token": "fake-token-value"}])
        mode = stat.S_IMODE(out_path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_missing_value_fails_loud_no_partial_file(self):
        template = [{"name": "colleym", "secret": "claude-oath-token-colleym"},
                    {"name": "norma", "secret": "claude-oath-token-norma"}]
        # Only supply one of two -> must fail, not write a partial/placeholder file.
        result, out_path = self._run(template, {"CLAUDE_OATH_TOKEN_COLLEYM": "fake-token-value"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("norma", result.stderr)
        self.assertFalse(out_path.exists())

    def test_never_prints_token_values(self):
        template = [{"name": "colleym", "secret": "claude-oath-token-colleym"}]
        secret_value = "super-secret-do-not-print-me"
        result, _ = self._run(template, {"CLAUDE_OATH_TOKEN_COLLEYM": secret_value})
        self.assertNotIn(secret_value, result.stdout)
        self.assertNotIn(secret_value, result.stderr)


if __name__ == "__main__":
    unittest.main()
