#!/usr/bin/env python3
"""
test_skill_docs_trunk_aware.py — PROJ-039/T-097.

Docs-consistency grep, not a runtime test: `launch-session/SKILL.md` and
`consolidate-tasks/SKILL.md` are prose, so there is no fixture to execute. This
guards two textual invariants the T-097 brief called out directly, so a future
edit can't silently regress them:

  1. No dead branch-routing instruction survives for brief delivery — the pre-
     2026-07-10 "Team Lead pushes a `team-lead` branch + PR to the home repo" brief
     ceremony was retired fleet-wide by the change-visibility policy
     (OPERATING-MANUAL §2b); the phrase must not reappear as a live instruction.
  2. `lifecycle_mode` is mentioned in both `/launch-session`'s Step 3 fork and
     `/consolidate-tasks`'s Step 0c fork, so a fresh team-lead session finds the
     trunk-vs-branch choreography in both halves of the lifecycle it touches.
"""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCH_SKILL = REPO_ROOT / "skills" / "launch-session" / "SKILL.md"
CONSOLIDATE_SKILL = REPO_ROOT / "skills" / "consolidate-tasks" / "SKILL.md"


class TestNoDeadBranchRoutingInstruction(unittest.TestCase):
    """The retired brief-routing ceremony must not survive as a live instruction."""

    def test_launch_session_has_no_team_lead_branch_routing(self):
        text = LAUNCH_SKILL.read_text(encoding="utf-8")
        self.assertNotIn(
            "routes the brief by committing it to a **`team-lead`** branch",
            text,
            "dead team-lead-branch brief-routing instruction survives in "
            "launch-session/SKILL.md — retired by the 2026-07-10 change-visibility "
            "policy (OPERATING-MANUAL §2b)",
        )
        self.assertIn(
            "OPERATING-MANUAL §2b",
            text,
            "launch-session/SKILL.md should cross-reference the change-visibility "
            "policy where it describes brief routing",
        )


class TestBriefFirstLockUsesBriefId(unittest.TestCase):
    """PROJ-039/T-119 — the launcher's Step 3 lock loop must key a brief-first
    launch's ``$SID`` on the ``brief_id`` string, not a generated uuid. A uuid
    here can never match T-054's release-side ``owned_sids=[brief_id]`` (a
    permanent mismatch), so finalise's release always reports False and the
    lock is left wedged — hit ten times on 2026-07-30/31. Doc-consistency
    grep, matching the shape of :class:`TestNoDeadBranchRoutingInstruction`
    above: the prose is the artifact here, there's no fixture to execute."""

    def test_step3_names_brief_id_as_the_lock_sid(self):
        text = LAUNCH_SKILL.read_text(encoding="utf-8")
        self.assertIn(
            'brief-first path: "{brief_id}"',
            text,
            "launch-session/SKILL.md Step 3 no longer tells the brief-first "
            "path to lock with the brief_id string — PROJ-039/T-119 regression",
        )

    def test_variant_table_calls_out_step3_sid_delta(self):
        text = LAUNCH_SKILL.read_text(encoding="utf-8")
        self.assertIn(
            "PROJ-039/T-119",
            text,
            "brief-first variant table should cross-reference T-119 where it "
            "documents the Step 3 lock-sid delta",
        )
        self.assertIn("`$SID` = the `brief_id` string", text)


class TestLifecycleModeForkInBothSkills(unittest.TestCase):
    """`lifecycle_mode` must be mentioned in both Step 3 (launch) and Step 0c (consolidate)."""

    def test_launch_session_step3_mentions_lifecycle_mode(self):
        text = LAUNCH_SKILL.read_text(encoding="utf-8")
        self.assertIn("lifecycle_mode", text)
        self.assertIn("Lifecycle-mode fork", text)

    def test_consolidate_tasks_step0c_mentions_lifecycle_mode(self):
        text = CONSOLIDATE_SKILL.read_text(encoding="utf-8")
        self.assertIn("lifecycle_mode", text)
        self.assertIn("Lifecycle-mode fork", text)

    def test_both_skills_reference_the_same_reader(self):
        for path in (LAUNCH_SKILL, CONSOLIDATE_SKILL):
            text = path.read_text(encoding="utf-8")
            self.assertIn(
                "lib/lifecycle_mode.py",
                text,
                f"{path} forks on lifecycle_mode but doesn't name the canonical reader",
            )


if __name__ == "__main__":
    unittest.main()
