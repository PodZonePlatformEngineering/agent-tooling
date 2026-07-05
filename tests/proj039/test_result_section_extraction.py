"""PROJ-039/T-051 (CC-361) — result section-extraction quality.

Evidence: sid ``0c7908ea`` (home#26). The finalise authored ``_None recorded._``
stubs over a rich final turn because the section extractor:

  1. missed ``## Started / Remaining`` and ``## Questions for Hermes/Martin`` — the
     alias table was exact-match, so a compound header never landed on its canonical
     section;
  2. dropped the ``## Completed`` body as empty — a full-line ``**bold**`` sub-heading
     immediately under it was treated as a section terminator; and
  3. never fired the raw-response fallback — one header (``Completed``) had matched
     (with an empty body), so the ``not extracted`` guard was False.

The fixture below IS the real ``0c7908ea`` response.text (the final ``/exit`` turn,
verbatim). The tests assert the fix delivers the three canonical sections that were
lost, keeps the T-047 behaviours (total-miss raw embed; full response always
preserved), and keeps small legitimately-partial responses stubbing the rest.
"""

from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import session_finalise  # noqa: E402

# The real 0c7908ea final turn (home#26 "## Full response" block), verbatim.
RESPONSE_0C7908EA = textwrap.dedent("""\
    Both repos are clean and committed, both PRs are up. Here's the session summary.

    ## Completed

    **Part 1 — PROJ-039/T-047 (CC-348) finalise result robustness — DONE, verified, pushed**

    **(a) Raw-response fallback.** `generate_session_result` no longer authors five `_None recorded._` stubs over a real response. New helper `_render_result_sections`: when the final turn carried **none** of the canonical headers but a real response exists, it embeds the response verbatim under one `## Session response (raw)` block.
    - **Headless `claude -p` diagnosis:** the response *was* captured; the defect was purely the section-extraction stubbing.

    **(b) `session_ids` `'s'` bug — actual trigger found.** A non-hermetic hook test: `session-materialise`'s `main()` reads `BRIEF_ID` from the ambient env. **Live `'s'` scrubbed** from the T-045 brief.

    **Regression tests green:** free-form → verbatim embed; headless-shaped → non-stub.

    **Self-sync:** resident stack synced to the fix (byte-identity PASS).

    ## PRs raised
    - **agent-tooling#48** — the T-047 fix + tests
    - **home-podzone-hephaestus#25** — resident-stack self-sync

    ## Decisions
    - Left the all-9s test-seed sid (`99999999-…`) in the T-045 point untouched — the brief scoped the scrub to the `'s'` entry only. Flagged below for your call.

    ## Started / Remaining
    **Part 2 — PROJ-011/T-025 (CC-356) trainee repo structure v2 (R-5..R-14) — not started.** It warrants its own focused session against the authority plan; I did not want to rush an operator-reviewed spec as a tail-end addendum. **Brief stays `in_progress`.**

    ## Questions for Hermes/Martin
    1. Scrub the all-9s test-seed sid from the T-045 point too, or is it intentional seed data?
    2. Confirm Part 2 (T-025) should run as the next dedicated session — no blockers, just scope.

    Brief-Status: in_progress
    """)


class TestResultSectionExtraction0c7908ea(unittest.TestCase):
    def setUp(self) -> None:
        self.extracted = session_finalise.extract_sections(RESPONSE_0C7908EA)

    # -- defect 1: compound / fuzzy canonical headers -----------------------

    def test_started_remaining_maps_to_started(self) -> None:
        self.assertIn("Started", self.extracted)
        self.assertIn("Part 2 — PROJ-011/T-025", self.extracted["Started"])

    def test_questions_for_hermes_martin_maps_to_questions(self) -> None:
        self.assertIn("Questions for Martin", self.extracted)
        self.assertIn("intentional seed data", self.extracted["Questions for Martin"])

    # -- defect 2: bold sub-heading no longer voids the Completed body -------

    def test_completed_body_captured_not_empty(self) -> None:
        self.assertIn("Completed", self.extracted)
        body = self.extracted["Completed"]
        self.assertIn("Part 1 — PROJ-039/T-047", body)
        self.assertIn("Raw-response fallback", body)
        self.assertTrue(body.strip(), "Completed body must not be empty")

    def test_decisions_still_captured(self) -> None:
        self.assertIn("all-9s test-seed sid", self.extracted.get("Decisions", ""))

    # -- rendered doc: the lost sections now surface, none wrongly stubbed ---

    def test_rendered_result_surfaces_all_three_recovered_sections(self) -> None:
        point = {
            "session_id": "0c7908ea-f18f-43be-bb58-88464ae698e0",
            "agent": "hephaestus", "work_item": "PROJ-011/T-025",
            "response": {"text": RESPONSE_0C7908EA},
        }
        out = session_finalise.generate_session_result(point, date="2026-07-04")
        # Rich extraction → NOT the raw-embed fallback, and no stubs for the three
        # sections that were wrongly "None recorded" in home#26. (Assert on the
        # fallback's unique explanatory sentence — the response text itself mentions
        # the literal string "## Session response (raw)".)
        self.assertNotIn("embedded verbatim below rather than stubbed", out)
        completed = out.index("## Completed")
        started = out.index("## Started")
        questions = out.index("## Questions for Martin")
        self.assertIn("Part 1 — PROJ-039/T-047", out[completed:started])
        self.assertIn("Part 2 — PROJ-011/T-025", out[started:])
        self.assertIn("intentional seed data", out[questions:])
        # None of the three recovered sections stub.
        self.assertNotIn("## Completed\n\n_None recorded._", out)
        self.assertNotIn("## Started\n\n_None recorded._", out)
        self.assertNotIn("## Questions for Martin\n\n_None recorded._", out)

    def test_full_response_still_embedded_verbatim(self) -> None:
        # T-047 behaviour preserved: the whole response is always in the doc.
        point = {
            "session_id": "0c7908ea", "agent": "hephaestus", "work_item": "W",
            "response": {"text": RESPONSE_0C7908EA},
        }
        out = session_finalise.generate_session_result(point, date="2026-07-04")
        self.assertIn("## Full response", out)
        self.assertIn("Both repos are clean and committed", out)


class TestFuzzyHeaderMatching(unittest.TestCase):
    def test_prefix_variants_map_to_canonical(self) -> None:
        cases = {
            "## Started / Remaining": "Started",
            "## Questions for Hermes/Martin": "Questions for Martin",
            "## Questions for Martin & Hermes": "Questions for Martin",
            "## Completed (this session)": "Completed",
            "## Blockers / Risks": "Blockers",
        }
        for header, canonical in cases.items():
            got = session_finalise.extract_sections(f"{header}\nbody line\n")
            self.assertEqual(got.get(canonical), "body line",
                             f"{header!r} should map to {canonical!r}")

    def test_word_extension_does_not_false_match(self) -> None:
        # "Completedness" must NOT be read as the Completed header.
        got = session_finalise.extract_sections("## Completedness metrics\nx\n")
        self.assertNotIn("Completed", got)


class TestMostlyEmptyRawEmbed(unittest.TestCase):
    def test_mostly_empty_extraction_falls_back_to_raw(self) -> None:
        # A substantial response where a single header matched a tiny body but the
        # bulk of the content lives under a NON-canonical `## Notes` heading (which
        # terminates the section, so that content is otherwise dropped) → raw embed,
        # not a near-stub doc.
        body = "This is a long free-form narrative. " * 20  # ~740 chars
        text = f"## Decisions\n- one small note\n\n## Notes\n{body}"
        point = {"session_id": "s", "agent": "A", "work_item": "W",
                 "response": {"text": text}}
        out = session_finalise.generate_session_result(point, date="2026-07-04")
        self.assertIn("embedded verbatim below rather than stubbed", out)
        self.assertIn("long free-form narrative", out)

    def test_small_partial_response_still_stubs_the_rest(self) -> None:
        # Below the size floor: a small legitimately-partial response keeps the
        # T-047 partial behaviour (stub the absent sections, no raw dump).
        point = {"session_id": "s", "agent": "A", "work_item": "W",
                 "response": {"text": "## Completed\n- did a thing\n"}}
        out = session_finalise.generate_session_result(point, date="2026-07-04")
        self.assertNotIn("## Session response (raw)", out)
        self.assertIn("did a thing", out)

    def test_rich_sectioned_response_not_raw_embedded(self) -> None:
        # A large response that IS well-sectioned must render sections, not raw.
        point = {"session_id": "s", "agent": "A", "work_item": "W",
                 "response": {"text": RESPONSE_0C7908EA}}
        out = session_finalise.generate_session_result(point, date="2026-07-04")
        self.assertNotIn("embedded verbatim below rather than stubbed", out)


if __name__ == "__main__":
    unittest.main()
