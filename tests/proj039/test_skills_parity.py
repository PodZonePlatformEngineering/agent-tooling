#!/usr/bin/env python3
"""
test_skills_parity.py — PROJ-039/T-034 (CC-333).

Guards the skills byte-identity invariant: the agent-tooling `skills/` source is
canonical, and every team-repo mirror must hold a byte-identical copy of every
skill the source defines. Skills had no enforced parity (unlike hooks/lib/
primitives, covered by sync-agent-tooling.sh) and drifted across the four copies
three times (T-028 reconcile -> re-drift at T-031). This is the skills analogue of
test_home_runtime_lib_closure.py.

Two layers, mirroring the closure test:

  1. Hermetic layer (always runs): build a temp source + mirrors, prove the parity
     checker PASSES when identical, FAILS on an injected drift, and HONOURS the
     allowlist. No real repos needed -- this is the "fails on injected drift" proof
     the brief requires, runnable in any CI.

  2. Real-repo layer (skipped if siblings absent): assert the live mirrors
     (podzoneAgentTeam / trainingTeam / roadmapTeam) are byte-identical to the
     agent-tooling source for every source skill. This is the pre-`consolidate-tasks`
     drift guard; it skips cleanly where the sibling repos are not checked out.

The checker logic here is the Python twin of sync-skills.sh's invariant block;
both are kept in lock-step (same pattern as the bash/python pair for the lib closure).
"""

import filecmp
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "skills"
ALLOWLIST = REPO_ROOT / "skills-sync-allowlist"

# Mirror skills dirs, relative to agent-tooling's parent. Kept in sync with the
# default MIRRORS in sync-skills.sh.
MIRROR_RELPATHS = (
    "podzoneAgentTeam/.claude/skills",
    "trainingTeam/.claude/skills",
    "roadmapTeam/.claude/skills",
)

IGNORE = [".DS_Store", "__pycache__"]


def read_allowlist(path: Path) -> set[str]:
    """`mirror-basename:skill` entries; comments/blanks stripped."""
    out: set[str] = set()
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            out.add(entry)
    return out


def _dirs_identical(a: Path, b: Path) -> bool:
    """True iff directory trees `a` and `b` are byte-identical (ignoring pyc/cruft)."""
    cmp = filecmp.dircmp(str(a), str(b), ignore=IGNORE)
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return False
    # filecmp compares shallow by default; force byte comparison of common files.
    match, mismatch, errors = filecmp.cmpfiles(
        str(a), str(b), cmp.common_files, shallow=False
    )
    if mismatch or errors:
        return False
    for sub in cmp.common_dirs:
        if not _dirs_identical(a / sub, b / sub):
            return False
    return True


def source_skills(source: Path) -> list[str]:
    return sorted(p.name for p in source.iterdir() if p.is_dir())


def find_drift(source: Path, mirror: Path, allowed: set[str]) -> list[str]:
    """Return a list of drift descriptions for one mirror (empty == parity holds).

    The mirror may carry extra skills not in the source -- those are out of scope.
    Allowlisted `<mirror-key>:<skill>` pairs are exempt.
    """
    key = mirror_key(mirror)
    drift: list[str] = []
    for skill in source_skills(source):
        if f"{key}:{skill}" in allowed:
            continue
        dst = mirror / skill
        if not dst.is_dir():
            drift.append(f"{key}/{skill}: missing in mirror")
            continue
        if not _dirs_identical(source / skill, dst):
            drift.append(f"{key}/{skill}: not byte-identical to source")
    return drift


def mirror_key(mirror: Path) -> str:
    """Basename of the repo dir for a `<repo>/.claude/skills` (or `<repo>/skills`) path."""
    p = mirror
    if p.name == "skills":
        p = p.parent
    if p.name == ".claude":
        p = p.parent
    return p.name


class TestSkillsParityHermetic(unittest.TestCase):
    """Self-contained: no real repos. Proves the checker pass/fail/allowlist logic."""

    def _build(self, root: Path):
        source = root / "agent-tooling" / "skills"
        source.mkdir(parents=True)
        for skill, body in (("alpha", "A\n"), ("beta", "B\n")):
            (source / skill).mkdir()
            (source / skill / "SKILL.md").write_text(body)
        mirror = root / "trainingTeam" / ".claude" / "skills"
        mirror.mkdir(parents=True)
        for skill in ("alpha", "beta"):
            (mirror / skill).mkdir()
            (mirror / skill / "SKILL.md").write_text((source / skill / "SKILL.md").read_text())
        # A legit extra skill in the mirror, not in source -> must be ignored.
        (mirror / "usage-report").mkdir()
        (mirror / "usage-report" / "SKILL.md").write_text("local only\n")
        return source, mirror

    def test_pass_when_identical(self):
        with tempfile.TemporaryDirectory() as td:
            source, mirror = self._build(Path(td))
            self.assertEqual(find_drift(source, mirror, set()), [],
                             "identical trees must show no drift")

    def test_extra_mirror_skill_is_not_drift(self):
        with tempfile.TemporaryDirectory() as td:
            source, mirror = self._build(Path(td))
            # usage-report exists only in the mirror; parity is source-scoped.
            self.assertEqual(find_drift(source, mirror, set()), [])

    def test_fails_on_injected_content_drift(self):
        with tempfile.TemporaryDirectory() as td:
            source, mirror = self._build(Path(td))
            (mirror / "alpha" / "SKILL.md").write_text("A drifted\n")
            drift = find_drift(source, mirror, set())
            self.assertTrue(any("alpha" in d for d in drift),
                            f"injected drift not caught: {drift}")

    def test_fails_on_missing_skill(self):
        with tempfile.TemporaryDirectory() as td:
            source, mirror = self._build(Path(td))
            import shutil
            shutil.rmtree(mirror / "beta")
            drift = find_drift(source, mirror, set())
            self.assertTrue(any("beta" in d and "missing" in d for d in drift), drift)

    def test_fails_on_injected_extra_file_in_skill(self):
        with tempfile.TemporaryDirectory() as td:
            source, mirror = self._build(Path(td))
            (mirror / "alpha" / "EXTRA.md").write_text("sneaky\n")
            drift = find_drift(source, mirror, set())
            self.assertTrue(any("alpha" in d for d in drift), drift)

    def test_allowlist_exempts_drift(self):
        with tempfile.TemporaryDirectory() as td:
            source, mirror = self._build(Path(td))
            (mirror / "alpha" / "SKILL.md").write_text("A drifted\n")
            allowed = {f"{mirror_key(mirror)}:alpha"}
            self.assertEqual(find_drift(source, mirror, allowed), [],
                             "allowlisted skill must not be reported as drift")


class TestSkillsParityRealRepos(unittest.TestCase):
    """Live parity guard over the sibling team repos (skips if not checked out)."""

    def test_source_exists(self):
        self.assertTrue(SOURCE.is_dir(), f"source skills dir missing: {SOURCE}")
        self.assertTrue(source_skills(SOURCE), "source has no skills")

    def test_mirrors_byte_identical_to_source(self):
        allowed = read_allowlist(ALLOWLIST)
        parent = REPO_ROOT.parent
        present = [parent / rel for rel in MIRROR_RELPATHS
                   if (parent / rel).is_dir()]
        if not present:
            self.skipTest(
                "no sibling team-repo mirrors checked out alongside agent-tooling"
            )
        all_drift: list[str] = []
        for mirror in present:
            all_drift += find_drift(SOURCE, mirror, allowed)
        self.assertEqual(
            all_drift, [],
            "\nskill drift vs canonical agent-tooling/skills source:\n  "
            + "\n  ".join(all_drift)
            + "\n-> run: bash sync-skills.sh   (or add a skills-sync-allowlist entry)",
        )


if __name__ == "__main__":
    unittest.main()
