"""Tests for tools/wire-update-tooling.py — the settings.json SessionStart
wiring patcher/verifier (PROJ-039/T-069, T-065 F1).

settings.json is per-repo (env block), so it joins the sync set structurally:
the patcher must insert the updater at its canonical position (first; last for
trainee), normalise shape idempotently, and leave everything else — env, other
hook events — byte-untouched in structure.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "wire_update_tooling", str(REPO_ROOT / "tools" / "wire-update-tooling.py")
)
wire_update_tooling = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire_update_tooling)  # type: ignore

UPDATER_CMD = 'python3 "$CLAUDE_PROJECT_DIR"/.claude/tools/update-tooling.py'

# The pre-T-069 fleet shape (v1.1.1 repos): substrate hooks wired, no updater,
# per-repo env block — what the one-time delivery PRs start from.
UNWIRED = {
    "env": {"PODZONE_TELEMETRY_REMOTE": "https://example.test/t.git"},
    "hooks": {
        "SessionStart": [
            {"matcher": "startup|resume", "hooks": [
                {"type": "command", "command": 'bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/session-start.sh'},
                {"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/session-materialise.py'},
            ]}
        ],
        "Stop": [
            {"matcher": "", "hooks": [
                {"type": "command", "command": 'bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/stop.sh'}]}
        ],
    },
}


def _commands(settings: dict) -> list[str]:
    return [c["command"] for c in settings["hooks"]["SessionStart"][0]["hooks"]]


def _all_entries(settings: dict) -> list[dict]:
    """Every hook command entry across every event — the scope the T-128 coverage
    assertions measure. T-125's test looked at one command; that is the bug."""
    return list(wire_update_tooling._iter_hook_entries(settings))


def _all_commands(settings: dict) -> list[str]:
    return [e["command"] for e in _all_entries(settings)]


@contextlib.contextmanager
def _scaffolded_trainee_repo():
    """A real trainee repo built by scaffold.sh — the bytes that actually ship."""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "home-training-scaffolded"
        subprocess.run(
            ["bash", str(REPO_ROOT / "scaffold.sh"), "podzone", "scaffolded", "trainee",
             "--target-dir", str(target), "--force"],
            cwd=str(REPO_ROOT), check=True, capture_output=True, text=True,
            env={**os.environ, "NO_TELEMETRY_BOOTSTRAP": "1"},
        )
        yield target


def _scaffold_trainee_settings() -> dict:
    with _scaffolded_trainee_repo() as repo:
        return json.loads((repo / ".claude" / "settings.json").read_text())


def _run_command_without_python3(command: str) -> subprocess.CompletedProcess:
    """Run a settings.json hook command string in a shell whose PATH contains no
    python3, against a tree carrying the real shim. This is the T-125 test with
    its scope fixed: it asks what EVERY command does, not what one command does."""
    td = tempfile.mkdtemp()
    project = Path(td) / "home-training-nopy"
    (project / ".claude" / "hooks").mkdir(parents=True)
    (project / ".claude" / "hooks" / "run-hook.sh").write_text(
        (REPO_ROOT / "hooks" / "run-hook.sh").read_text())
    # A PATH carrying bash and NOTHING else — python3 absent, which is the condition
    # under test. bash itself must stay resolvable: the hook commands invoke it by
    # name (`bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/run-hook.sh …`), the same shape
    # scaffold.sh has always used for the shell hooks of every other role.
    bash = shutil.which("bash") or "/bin/bash"
    bin_dir = Path(td) / "bashonly"
    bin_dir.mkdir()
    (bin_dir / "bash").symlink_to(bash)
    assert shutil.which("python3", path=str(bin_dir)) is None
    return subprocess.run(
        [bash, "-c", command], capture_output=True, text=True,
        env={"PATH": str(bin_dir), "CLAUDE_PROJECT_DIR": str(project)},
    )


class TestWire(unittest.TestCase):
    def test_inserts_first_for_non_trainee(self) -> None:
        settings, changed = wire_update_tooling.wire(json.loads(json.dumps(UNWIRED)), "coder")
        self.assertTrue(changed)
        cmds = _commands(settings)
        self.assertEqual(cmds[0], UPDATER_CMD)
        self.assertEqual(len(cmds), 3)
        first = settings["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertEqual(first["timeout"], 300)

    def test_appends_last_for_trainee(self) -> None:
        settings, changed = wire_update_tooling.wire(json.loads(json.dumps(UNWIRED)), "trainee")
        self.assertTrue(changed)
        # The trainee updater entry routes through the shim (T-128) but keeps the
        # same position and the same 300s timeout.
        last = settings["hooks"]["SessionStart"][0]["hooks"][-1]
        self.assertEqual(last["command"], wire_update_tooling.TRAINEE_UPDATER_ENTRY["command"])
        self.assertIn("update-tooling.py", last["command"])
        self.assertEqual(last["timeout"], 300)

    def test_idempotent_and_normalising(self) -> None:
        settings, _ = wire_update_tooling.wire(json.loads(json.dumps(UNWIRED)), "coder")
        again, changed = wire_update_tooling.wire(settings, "coder")
        self.assertFalse(changed)
        # an out-of-position / shape-drifted entry converges to canonical
        drifted = json.loads(json.dumps(UNWIRED))
        drifted["hooks"]["SessionStart"][0]["hooks"].append(
            {"type": "command", "command": UPDATER_CMD})  # wrong slot, no timeout
        fixed, changed = wire_update_tooling.wire(drifted, "coder")
        self.assertTrue(changed)
        self.assertEqual(_commands(fixed)[0], UPDATER_CMD)
        self.assertEqual(_commands(fixed).count(UPDATER_CMD), 1)

    def test_env_and_other_hooks_untouched(self) -> None:
        settings, _ = wire_update_tooling.wire(json.loads(json.dumps(UNWIRED)), "coder")
        self.assertEqual(settings["env"], UNWIRED["env"])
        self.assertEqual(settings["hooks"]["Stop"], UNWIRED["hooks"]["Stop"])


class TestCheck(unittest.TestCase):
    def test_unwired_is_a_defect(self) -> None:
        self.assertIsNotNone(wire_update_tooling.check(json.loads(json.dumps(UNWIRED)), "coder"))

    def test_wired_passes(self) -> None:
        settings, _ = wire_update_tooling.wire(json.loads(json.dumps(UNWIRED)), "coder")
        self.assertIsNone(wire_update_tooling.check(settings, "coder"))

    def test_wrong_position_is_a_defect(self) -> None:
        settings = json.loads(json.dumps(UNWIRED))
        settings["hooks"]["SessionStart"][0]["hooks"].append(
            {"type": "command", "command": UPDATER_CMD, "timeout": 300})
        self.assertIsNotNone(wire_update_tooling.check(settings, "coder"))
        # …but that IS the canonical trainee position. This fixture is a coder-shaped
        # one, so the trainee check still reports a defect — the python3 shim (T-128),
        # NOT the position. Assert on which, or this stops testing position at all.
        defect = wire_update_tooling.check(settings, "trainee")
        self.assertNotIn("expected", defect or "", "position must be accepted for trainee")
        self.assertIn("run-hook.sh shim", defect)


class TestTraineePython3Shim(unittest.TestCase):
    """PROJ-011/T-128 (CC-525) — the python3 guard covers ALL ELEVEN trainee hook
    invocations, via the hooks/run-hook.sh shim, and the message fires ONCE.

    T-125 shipped an inline guard on `trainee-preflight.py` alone. It worked — in
    isolation, which is exactly the scope the T-125 test measured. On the live
    repos it meant one friendly message at session start and then a raw
    `python3: command not found` on every UserPromptSubmit (telemetry) and every
    PreToolUse (read-guard) for the rest of the session.

    The tests below are deliberately COUNT-BASED over the whole settings file
    rather than assertions about the preflight command, so that a twelfth hook
    added without coverage fails the suite instead of silently reopening the hole.
    """

    SHIM = REPO_ROOT / "hooks" / "run-hook.sh"

    #: The pre-T-121 live-trainee shape (verbatim from home-training-martin), with
    #: the full eleven-invocation hook set the six live repos actually carry.
    def _live_trainee_settings(self) -> dict:
        def py(rel: str) -> dict:
            return {"type": "command",
                    "command": f'python3 "$CLAUDE_PROJECT_DIR"/.claude/{rel}'}
        tele = "hooks/trainee-telemetry.py"
        return {
            "env": {"TRAINEE_RUNTIME": "1"},
            "hooks": {
                "SessionStart": [
                    {"matcher": "startup|resume", "hooks": [
                        py("hooks/trainee-preflight.py"),
                        py("hooks/trainee-finalise.py --guard"),
                        py("hooks/trainee-session-branch.py"),
                        {**py("tools/update-tooling.py"), "timeout": 300},
                        py("hooks/trainee-materialise.py"),
                        py(tele),
                    ]}
                ],
                "UserPromptSubmit": [{"matcher": "", "hooks": [py(tele)]}],
                "PreToolUse": [{"matcher": "*", "hooks": [py("hooks/trainee-read-guard.py")]}],
                "PostCompact": [{"matcher": "", "hooks": [py(tele)]}],
                "Stop": [{"matcher": "", "hooks": [py(tele)]}],
                "SessionEnd": [{"matcher": "", "hooks": [
                    {**py("hooks/trainee-finalise.py"), "timeout": 600}]}],
            },
        }

    # ---- coverage: the assertion that stops the class recurring (task 3) ----

    def test_the_live_shape_has_eleven_python3_invocations_and_check_flags_them(self) -> None:
        settings = self._live_trainee_settings()
        bare = [c for c in _all_commands(settings) if c.startswith("python3 ")]
        self.assertEqual(len(bare), 11, "the shape under test IS the 11-command live one")
        defect = wire_update_tooling.check(settings, "trainee")
        self.assertIsNotNone(defect)
        self.assertIn("11 hook command(s)", defect)

    def test_no_unguarded_python3_survives_the_patch(self) -> None:
        """Count-based: ZERO commands may invoke python3 outside the shim."""
        settings = self._live_trainee_settings()
        self.assertTrue(wire_update_tooling.guard_trainee_hooks(settings))
        unshimmed = [c for c in _all_commands(settings)
                     if "python3" in c and not c.startswith(wire_update_tooling.SHIM_PREFIX)]
        self.assertEqual(unshimmed, [], "every python3 invocation must route through the shim")
        self.assertEqual(len(_all_commands(settings)), 11, "nothing added, nothing dropped")
        self.assertIsNone(wire_update_tooling.check(settings, "trainee"))
        self.assertFalse(wire_update_tooling.guard_trainee_hooks(settings), "idempotent")

    def test_scaffolded_trainee_repo_has_zero_unguarded_python3(self) -> None:
        """The same count-based assertion against what scaffold.sh actually WRITES —
        this is the one that fails when hook #12 is added to the scaffold template
        without routing it through the shim."""
        settings = _scaffold_trainee_settings()
        cmds = _all_commands(settings)
        unshimmed = [c for c in cmds
                     if "python3" in c and not c.startswith(wire_update_tooling.SHIM_PREFIX)]
        self.assertEqual(unshimmed, [], f"unshimmed commands in a fresh trainee repo: {unshimmed}")
        self.assertEqual(len(cmds), 11, "the trainee hook set is 11 commands")
        self.assertEqual(len([c for c in cmds
                              if c.startswith(wire_update_tooling.SHIM_PREFIX)]), 11)
        self.assertIsNone(wire_update_tooling.check(settings, "trainee"))

    def test_scaffold_and_patcher_agree_byte_for_byte(self) -> None:
        """Lockstep: patching a fresh trainee repo's settings is a no-op, so the
        two producers of these strings can never drift apart."""
        settings = _scaffold_trainee_settings()
        self.assertFalse(wire_update_tooling.guard_trainee_hooks(settings))

    def test_t125_inline_guard_converges_to_the_shim(self) -> None:
        """The six live repos carry the T-125 inline form after T-127. Re-delivery
        must REPLACE it, not stack a shim on top of it."""
        settings = self._live_trainee_settings()
        pf = settings["hooks"]["SessionStart"][0]["hooks"][0]
        pf["command"] = (
            "command -v python3 >/dev/null 2>&1 && "
            'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/trainee-preflight.py'
            " || echo 'Python 3 is not installed on this machine, so none of this "
            "training repo automation can run.'"
        )
        wire_update_tooling.guard_trainee_hooks(settings)
        got = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertNotIn("command -v python3", got, "the inline guard must be stripped")
        self.assertEqual(
            got,
            wire_update_tooling.SHIM_PREFIX + " --announce hooks/trainee-preflight.py")

    # ---- message frequency: once per session, not once per hook ----

    def test_exactly_one_command_carries_announce(self) -> None:
        for label, settings in (("live-patched", self._live_trainee_settings()),
                                ("scaffolded", _scaffold_trainee_settings())):
            with self.subTest(label):
                if label == "live-patched":
                    wire_update_tooling.guard_trainee_hooks(settings)
                announcers = [c for c in _all_commands(settings) if "--announce" in c]
                self.assertEqual(len(announcers), 1, f"{len(announcers)} announcers in {label}")
                # …and it is the FIRST SessionStart command, which runs once per session.
                self.assertEqual(announcers[0],
                                 settings["hooks"]["SessionStart"][0]["hooks"][0]["command"])

    def test_check_rejects_a_second_announcer(self) -> None:
        settings = _scaffold_trainee_settings()
        settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] += " --announce"
        defect = wire_update_tooling.check(settings, "trainee")
        self.assertIsNotNone(defect)
        self.assertIn("ONCE per session", defect)

    def test_check_rejects_losing_the_announcer_entirely(self) -> None:
        settings = _scaffold_trainee_settings()
        first = settings["hooks"]["SessionStart"][0]["hooks"][0]
        first["command"] = first["command"].replace(" --announce", "")
        defect = wire_update_tooling.check(settings, "trainee")
        self.assertIsNotNone(defect)
        self.assertIn("--announce", defect)

    def test_only_the_announcer_speaks_when_python3_is_absent(self) -> None:
        """The behavioural half of the frequency guarantee: run every one of the
        eleven real commands with an emptied PATH and count what reaches stdout."""
        spoke = []
        for cmd in _all_commands(_scaffold_trainee_settings()):
            r = _run_command_without_python3(cmd)
            self.assertEqual(r.returncode, 0, f"{cmd} -> {r.returncode}: {r.stderr}")
            self.assertEqual(r.stderr, "", f"no-python3 path must be quiet on stderr: {r.stderr}")
            if r.stdout.strip():
                spoke.append(cmd)
        self.assertEqual(len(spoke), 1, f"{len(spoke)} of 11 commands printed; want exactly 1")
        r = _run_command_without_python3(spoke[0])
        self.assertIn("Python 3 is not installed", r.stdout)
        self.assertIn("install Python 3", r.stdout)

    # ---- exit-code / stdio semantics: the thing a wrapper could break ----

    def test_shim_execs_so_exit_codes_are_the_hooks_own(self) -> None:
        """PreToolUse treats exit 2 as DENY and anything else as proceed;
        UserPromptSubmit treats exit 2 as block. `exec` means the shim contributes
        no exit code of its own — verified across the codes that carry meaning."""
        with tempfile.TemporaryDirectory() as td:
            claude = Path(td) / ".claude"
            (claude / "hooks").mkdir(parents=True)
            (claude / "hooks" / "run-hook.sh").write_text(self.SHIM.read_text())
            (claude / "hooks" / "exiter.py").write_text(
                "import sys\n"
                "code = int(sys.argv[1])\n"
                "sys.stdout.write('OUT')\n"
                "sys.stderr.write('ERR')\n"
                "sys.exit(code)\n"
            )
            for code in (0, 1, 2, 7):
                with self.subTest(exit_code=code):
                    r = subprocess.run(
                        ["bash", str(claude / "hooks" / "run-hook.sh"),
                         "hooks/exiter.py", str(code)],
                        capture_output=True, text=True)
                    self.assertEqual(r.returncode, code, "exit code must pass through unchanged")
                    self.assertEqual(r.stdout, "OUT", "stdout must pass through unchanged")
                    self.assertEqual(r.stderr, "ERR", "stderr must pass through unchanged")

    def test_shim_does_not_consume_the_hook_payload_on_stdin(self) -> None:
        """Hooks are fed their event JSON on stdin. A wrapper that read stdin — to
        buffer it, or with a stray `read` — would starve the Python process."""
        with tempfile.TemporaryDirectory() as td:
            claude = Path(td) / ".claude"
            (claude / "hooks").mkdir(parents=True)
            (claude / "hooks" / "run-hook.sh").write_text(self.SHIM.read_text())
            (claude / "hooks" / "echoer.py").write_text(
                "import sys, json\n"
                "sys.stdout.write(json.load(sys.stdin)['tool_name'])\n"
            )
            payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
            r = subprocess.run(
                ["bash", str(claude / "hooks" / "run-hook.sh"), "hooks/echoer.py"],
                input=payload, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "Bash", "the payload must reach the hook intact")

    def test_shim_resolves_claude_dir_from_its_own_location_not_cwd(self) -> None:
        """Cwd-independence (PROJ-039/T-050/T-055). $CLAUDE_PROJECT_DIR locates the
        shim; the shim locates .claude/ from $0. Neither leg may consult $PWD — and
        the shim must not need $CLAUDE_PROJECT_DIR to be set at all once invoked."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "home-training-x"
            (project / ".claude" / "hooks").mkdir(parents=True)
            (project / ".claude" / "tools").mkdir(parents=True)
            (project / ".claude" / "hooks" / "run-hook.sh").write_text(self.SHIM.read_text())
            # A resident under tools/, not hooks/ — update-tooling.py's real shape.
            (project / ".claude" / "tools" / "resident.py").write_text(
                "import os, sys; sys.stdout.write(os.getcwd())\n")
            elsewhere = Path(td) / "elsewhere"
            elsewhere.mkdir()
            cmd = ('bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/run-hook.sh '
                   'tools/resident.py')
            r = subprocess.run(["bash", "-c", cmd], cwd=str(elsewhere),
                               capture_output=True, text=True,
                               env={"PATH": os.environ["PATH"],
                                    "CLAUDE_PROJECT_DIR": str(project)})
            self.assertEqual(r.returncode, 0, r.stderr)
            # It ran (from the unrelated cwd) — proving resolution was not cwd-based.
            self.assertEqual(os.path.realpath(r.stdout), os.path.realpath(str(elsewhere)))

    def test_updater_timeout_is_a_sibling_key_and_survives(self) -> None:
        """The 300s update-tooling timeout and the 600s finalise timeout live
        beside the command, not inside it, so shimming cannot disturb them."""
        settings = self._live_trainee_settings()
        wire_update_tooling.guard_trainee_hooks(settings)
        updater = [e for e in _all_entries(settings) if "update-tooling.py" in e["command"]]
        self.assertEqual([e["timeout"] for e in updater], [300])
        self.assertTrue(updater[0]["command"].startswith(wire_update_tooling.SHIM_PREFIX))
        end = _all_entries(settings)
        finalise = [e for e in end
                    if e["command"].endswith("hooks/trainee-finalise.py")]
        self.assertEqual([e.get("timeout") for e in finalise], [600])
        # And through the full wire() path, which is what sync actually calls.
        scaffolded = _scaffold_trainee_settings()
        _, changed = wire_update_tooling.wire(scaffolded, "trainee")
        self.assertFalse(changed, "a fresh trainee repo is already wired")
        upd = [e for e in _all_entries(scaffolded) if "update-tooling.py" in e["command"]]
        self.assertEqual([e["timeout"] for e in upd], [300])

    def test_non_python3_commands_are_left_alone(self) -> None:
        """The shim's only job is the missing-interpreter path. A shell hook needs
        no guard and must not be rewritten — nor silently no-op'd."""
        settings = self._live_trainee_settings()
        shell_cmd = 'bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/session-start.sh'
        settings["hooks"]["Stop"][0]["hooks"].append(
            {"type": "command", "command": shell_cmd})
        wire_update_tooling.guard_trainee_hooks(settings)
        self.assertIn(shell_cmd, _all_commands(settings))
        self.assertIsNone(wire_update_tooling.shim_command(shell_cmd, announce=False))

    def test_a_hook_added_to_a_new_event_is_swept(self) -> None:
        """Coverage by construction: the patcher is generic over events, so hook
        #12 in an event that does not exist today is still guarded."""
        settings = _scaffold_trainee_settings()
        settings["hooks"]["Notification"] = [{"matcher": "", "hooks": [
            {"type": "command",
             "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/brand-new.py'}]}]
        self.assertIsNotNone(wire_update_tooling.check(settings, "trainee"))
        self.assertTrue(wire_update_tooling.guard_trainee_hooks(settings))
        self.assertIsNone(wire_update_tooling.check(settings, "trainee"))
        self.assertEqual(
            settings["hooks"]["Notification"][0]["hooks"][0]["command"],
            wire_update_tooling.SHIM_PREFIX + " hooks/brand-new.py")

    def test_scaffold_ships_the_shim_file_it_references(self) -> None:
        """settings.json pointing at a run-hook.sh the repo does not carry fails
        EVERY hook with `No such file or directory` — worse than no guard."""
        with _scaffolded_trainee_repo() as repo:
            shim = repo / ".claude" / "hooks" / "run-hook.sh"
            self.assertTrue(shim.is_file(), "scaffold must install hooks/run-hook.sh")
            self.assertEqual(shim.read_text(), self.SHIM.read_text())

    def test_non_trainee_roles_are_untouched(self) -> None:
        """No other role has a workstation that might lack python3; check() must
        not start failing coder/lead repos over an unshimmed command."""
        coder = json.loads(json.dumps(UNWIRED))
        wire_update_tooling.wire(coder, "coder")
        self.assertIsNone(wire_update_tooling.check(coder, "coder"))
        self.assertIn("python3", " ".join(_all_commands(coder)))


class TestCli(unittest.TestCase):
    def test_check_exit_codes_and_patch_roundtrip(self) -> None:
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            path.write_text(json.dumps(UNWIRED))
            tool = str(REPO_ROOT / "tools" / "wire-update-tooling.py")
            base = [sys.executable, tool, "--settings", str(path), "--role", "coder"]
            self.assertEqual(subprocess.run(base + ["--check"], capture_output=True).returncode, 2)
            self.assertEqual(subprocess.run(base, capture_output=True).returncode, 0)
            self.assertEqual(subprocess.run(base + ["--check"], capture_output=True).returncode, 0)
            self.assertEqual(
                subprocess.run([sys.executable, tool, "--settings", str(Path(td) / "missing.json"),
                                "--role", "coder"], capture_output=True).returncode, 1)


if __name__ == "__main__":
    unittest.main()
