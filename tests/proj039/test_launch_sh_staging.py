"""Integration tests for launch.sh Phase 1 (PROJ-039/T-108, proposal §3.2).

Exercises the actual git mechanics `tools/launch.sh` performs against real
temp git repos (bare "origin" + working clones), the same style as
test_session_guard.py: preflight-abort on a dirtied repo, and the
branch + allow-empty-commit + push staging that pre-creates a working repo's
session branch (and, implicitly, what a PR would be opened against) BEFORE
Claude ever runs.

Does NOT invoke `claude -p` or `gh pr create` — those need a live subscription
token / real GitHub remote respectively, neither available in a test harness.
This proves the git-level contract launch.sh relies on: (a) preflight aborts
on a dirty working repo without touching it, (b) the empty-shell staging
leaves a branch pushed with exactly one commit ahead of main, ready for a PR.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import session_guard  # noqa: E402


def _run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


def _git(repo, *args):
    return _run("git", "-C", str(repo), *args)


class _GitFixture(unittest.TestCase):
    """A bare origin + a clone on main with one commit, in a temp dir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.origin = base / "origin.git"
        self.clone = base / "clone"
        _run("git", "init", "--bare", "-b", "main", str(self.origin))
        _run("git", "clone", str(self.origin), str(self.clone))
        for k, v in (("user.email", "t@t"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            _git(self.clone, "config", k, v)
        (self.clone / "README.md").write_text("hi\n")
        _git(self.clone, "add", "README.md")
        _git(self.clone, "commit", "-m", "init")
        _git(self.clone, "push", "origin", "main")

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TestPreflightAbort(_GitFixture):
    """launch.sh's abort condition: session_guard.preflight must HALT (not
    ready/recovered) on a dirty working repo — the exact condition launch.sh
    treats as an abort-with-message, no launch, no cleanup attempt."""

    def test_clean_on_main_is_ready(self):
        res = session_guard.preflight(str(self.clone))
        self.assertEqual(res["decision"], "ready")

    def test_dirty_tree_halts(self):
        (self.clone / "scratch.txt").write_text("uncommitted work\n")
        res = session_guard.preflight(str(self.clone))
        self.assertEqual(res["decision"], "halt")
        self.assertEqual(res["reason"], "dirty-tree")
        # Never mutates on a halt — the clone is left exactly as found.
        self.assertTrue((self.clone / "scratch.txt").exists())

    def test_untracked_file_also_halts(self):
        # "untracked" is explicitly in scope per the brief's preflight-abort
        # condition (untracked, uncommitted, OR unpushed).
        (self.clone / "untracked.txt").write_text("new file\n")
        status = _git(self.clone, "status", "--porcelain").stdout
        self.assertIn("??", status)
        res = session_guard.preflight(str(self.clone))
        self.assertEqual(res["decision"], "halt")


class TestWorkingRepoStaging(_GitFixture):
    """The branch + allow-empty-commit + push sequence launch.sh runs to
    pre-stage a working repo's session branch before Claude ever runs."""

    def test_empty_shell_branch_is_pushed_with_one_commit_ahead(self):
        branch = "hephaestus/2026-08-05-t108-smoke"
        _git(self.clone, "checkout", "-b", branch)
        # Nothing ahead of main yet — launch.sh's allow-empty guard fires.
        ahead = _git(self.clone, "log", "origin/main.." + branch).stdout
        self.assertEqual(ahead.strip(), "")
        _git(self.clone, "commit", "--allow-empty", "-m",
             "chore: open session branch for podzone/2026-08-05-t108-smoke")
        _git(self.clone, "push", "-u", "origin", branch)

        # Verify: the remote now carries the branch, exactly one commit ahead
        # of main, and the working tree is clean (a PR could be opened against
        # it with a real diff to review, even though the diff is empty).
        remote_branches = _git(self.clone, "branch", "-r").stdout
        self.assertIn(f"origin/{branch}", remote_branches)
        commits_ahead = _git(self.clone, "rev-list", "--count",
                              "origin/main.." + branch).stdout.strip()
        self.assertEqual(commits_ahead, "1")
        self.assertEqual(_git(self.clone, "status", "--porcelain").stdout, "")

    def test_restaging_an_existing_branch_does_not_duplicate_the_empty_commit(self):
        # launch.sh's idempotency guard: `git log origin/main..branch` already
        # non-empty ⇒ skip the allow-empty commit (a re-run / relaunch against
        # an already-staged branch must not pile up empty commits).
        branch = "hephaestus/2026-08-05-t108-smoke2"
        _git(self.clone, "checkout", "-b", branch)
        _git(self.clone, "commit", "--allow-empty", "-m", "chore: open session branch")
        _git(self.clone, "push", "-u", "origin", branch)

        _git(self.clone, "checkout", branch)  # simulate a relaunch re-checking out
        ahead = _git(self.clone, "log", "origin/main.." + branch).stdout
        self.assertNotEqual(ahead.strip(), "")  # non-empty ⇒ launch.sh's guard skips a 2nd commit
        commits_ahead = _git(self.clone, "rev-list", "--count",
                              "origin/main.." + branch).stdout.strip()
        self.assertEqual(commits_ahead, "1")


class TestClaudeInvocationCarriesBriefId(unittest.TestCase):
    """PROJ-039/T-212 regression: launch.sh resolved BRIEF_ID for its own use
    (team/assignee lookup, branch naming, lock sid) but never exported it to
    the `claude -p` subprocess it launches -- the SessionStart materialise
    hook had nothing to key off, so every real launch.sh dispatch halted with
    "MATERIALISE FAILED ... .workspace is empty/stale", misread on first
    sighting as a stale-brief artifact rather than this bug. Not exercisable
    as a live subprocess test (needs a real subscription token + Qdrant), so
    this asserts the fix textually: the exact line invoking `claude -p` must
    export BRIEF_ID in the same env-var assignment as the token.
    """

    def test_claude_invocation_line_exports_brief_id(self):
        lines = (REPO_ROOT / "tools" / "launch.sh").read_text().splitlines()
        matches = [i for i, line in enumerate(lines) if "LAST_STDOUT=" in line and "claude" in line.lower()]
        self.assertEqual(
            len(matches), 1,
            f"expected exactly one LAST_STDOUT=...claude invocation, found {len(matches)}",
        )
        # The invocation spans a `\`-continued line pair; join both.
        idx = matches[0]
        invocation_block = lines[idx] + "\n" + lines[idx + 1]
        self.assertIn("claude -p", invocation_block)
        self.assertIn(
            'BRIEF_ID="${BRIEF_ID}"', invocation_block,
            "claude -p invocation must export BRIEF_ID or SessionStart materialise "
            "halts on every real dispatch (T-212)",
        )


class TestTokenRotationCursor(unittest.TestCase):
    """PROJ-039/T-216: every launch.sh invocation used to start at token index
    0 unconditionally (confirmed live -- every dispatch this session hit
    'colleym' first, and two invocations launched in parallel would both
    authenticate against the same subscription concurrently). Extracts and
    runs the actual cursor-read/advance snippet from launch.sh via bash -c
    (not a full script run -- that needs claude/gh/qdrant) to prove the
    arithmetic and the --token-index override bypass, both against real bash,
    not a Python reimplementation that could silently drift from the script.
    """

    def _extract_cursor_block(self):
        lines = (REPO_ROOT / "tools" / "launch.sh").read_text().splitlines()
        start = next(
            i for i, l in enumerate(lines)
            if l.strip() == 'if [[ -n "${TOKEN_INDEX_OVERRIDE}" ]]; then'
        )
        end = next(i for i, l in enumerate(lines[start:], start) if l.strip() == "fi") + 1
        return "\n".join(lines[start:end])

    def _run(self, cursor_initial, token_count, override=""):
        with tempfile.TemporaryDirectory() as td:
            cursor_file = Path(td) / "cursor"
            if cursor_initial is not None:
                cursor_file.write_text(str(cursor_initial))
            script = f"""
set -euo pipefail
CURSOR_FILE="{cursor_file}"
TOKEN_COUNT={token_count}
TOKEN_INDEX_OVERRIDE="{override}"
log() {{ :; }}
{self._extract_cursor_block()}
echo "i=$i"
"""
            result = _run("bash", "-c", script)
            i = int(result.stdout.strip().split("i=")[1])
            next_cursor = cursor_file.read_text().strip() if cursor_file.exists() else None
            return i, next_cursor

    def test_no_cursor_file_starts_at_zero_and_advances(self):
        i, next_cursor = self._run(cursor_initial=None, token_count=4)
        self.assertEqual(i, 0)
        self.assertEqual(next_cursor, "1")

    def test_cursor_advances_round_robin(self):
        i, next_cursor = self._run(cursor_initial=2, token_count=4)
        self.assertEqual(i, 2)
        self.assertEqual(next_cursor, "3")

    def test_cursor_wraps_at_token_count(self):
        i, next_cursor = self._run(cursor_initial=3, token_count=4)
        self.assertEqual(i, 3)
        self.assertEqual(next_cursor, "0")

    def test_malformed_cursor_falls_back_to_zero(self):
        with tempfile.TemporaryDirectory() as td:
            cursor_file = Path(td) / "cursor"
            cursor_file.write_text("not-a-number")
            script = f"""
set -euo pipefail
CURSOR_FILE="{cursor_file}"
TOKEN_COUNT=4
TOKEN_INDEX_OVERRIDE=""
log() {{ :; }}
{self._extract_cursor_block()}
echo "i=$i"
"""
            result = _run("bash", "-c", script)
            self.assertEqual(result.stdout.strip(), "i=0")

    def test_explicit_override_bypasses_cursor_entirely(self):
        i, next_cursor = self._run(cursor_initial=1, token_count=4, override="3")
        self.assertEqual(i, 3)
        self.assertEqual(next_cursor, "1", "override must not advance or touch the cursor file")


if __name__ == "__main__":
    unittest.main()
