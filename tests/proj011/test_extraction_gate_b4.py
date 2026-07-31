"""PROJ-011/T-126 — the B4 decision: the substrate write path is gated inline.

B4 (shared substrate) is the boundary CI cannot reach: an upsert is not a pull
request. It is also the boundary with no remediation story — the PROJ-013 remedy of
minting a clean repo and deleting the old one has no equivalent in a vector store.
So for the one B4 write path this repository owns, ``create-brief.py``, the check
runs at the write itself.

These tests exercise the gate function directly and never touch the network. The
``--dry-run`` flag exists so the CLI can be exercised without a live upsert either:
a brief point is idempotent but not free to undo.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("create_brief", REPO / "tools" / "create-brief.py")
create_brief = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(create_brief)


CLAUSE = "**Extraction-gate:** `gate.md`\n- Boundaries authorised: none\n"


class TestB4Gate(unittest.TestCase):
    def test_clean_body_passes(self) -> None:
        self.assertEqual(create_brief._gate_body("# Brief\n\nBuild a thing.\n" + CLAUSE,
                                                 skip=False), 0)

    def test_credential_in_body_refuses(self) -> None:
        body = "# Brief\n\nexport TOKEN=ghp_" + "a" * 30 + "\n" + CLAUSE
        self.assertEqual(create_brief._gate_body(body, skip=False), 1)

    def test_skip_flag_bypasses(self) -> None:
        body = "# Brief\n\nexport TOKEN=ghp_" + "a" * 30 + "\n" + CLAUSE
        self.assertEqual(create_brief._gate_body(body, skip=True), 0)

    def test_missing_clause_warns_but_does_not_block(self) -> None:
        """Blocking here would strand a Team Lead mid-dispatch on a brief authored
        before the clause existed. Promote once the fleet has converged."""
        self.assertEqual(create_brief._gate_body("# Brief\n\nplain work\n", skip=False), 0)

    def test_gate_runs_at_b4_so_participant_names_are_not_exempt(self) -> None:
        """B2's Class P permission does not extend to the substrate (gate §2.1)."""
        body = "# Brief\n\ncontact someone.real@acme-widgets.co.za\n" + CLAUSE
        self.assertEqual(create_brief._gate_body(body, skip=False), 1)


if __name__ == "__main__":
    unittest.main()
