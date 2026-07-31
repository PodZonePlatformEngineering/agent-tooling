"""PROJ-011/T-126 — the extraction-gate scanner (lib/extraction_scan.py).

Control of record: podzoneTeam/planning/projects/PROJ-011-academy/
session-to-curriculum-extraction-gate.md.

The tests carry the load-bearing constraints of the design, not just its happy path:

* tier 3 is **silent on Class P at B2** (a control people turn off is worse than none);
* tier 1 does not fire on placeholders or on long numbers that fail the ID checksum
  (the acceptance bar is zero false positives on the existing planning corpus);
* tier 2 is structural — it fires on absence and shape, never on content;
* a declaration may not widen the boundaries its brief authorised.

Every fixture value here is invented. Credentials are shaped like the real thing and
are not real; ID numbers are constructed to satisfy or fail the checksum on purpose.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lib import extraction_scan as ES


CONFIG = ES.DestinationConfig.load()

# 13 digits, valid YYMMDD prefix, Luhn-valid check digit — constructed, not a real ID.
_SA_ID_BASE = "900101500108"


def _luhn_complete(base12: str) -> str:
    for candidate in "0123456789":
        if ES.luhn_ok(base12 + candidate):
            return base12 + candidate
    raise AssertionError("no valid check digit")


SA_ID = _luhn_complete(_SA_ID_BASE)

DECLARATION = """
**Extraction declaration** — gate: `PROJ-011-academy/session-to-curriculum-extraction-gate.md`
- Boundaries crossed: B2 (session → podzoneTeam planning)
- Class A (third-party): none present
- Class P (participants): retained (B2 only)
- Categories 3–5 judgement calls: none
- Declared by: Hephaestus · session `abc12345` · 2026-07-31
"""


def _empty_roster() -> ES.Roster:
    return ES.Roster()


def _roster(names, emails=()) -> ES.Roster:
    return ES.Roster(names=tuple(names), emails=tuple(emails), source="test")


class TestDestinationMapping(unittest.TestCase):
    def test_planning_paths_are_b2(self) -> None:
        self.assertEqual(CONFIG.boundaries_for("planning/projects/PROJ-011/x.md"), ["B2"])

    def test_outgoing_paths_are_b2(self) -> None:
        self.assertEqual(CONFIG.boundaries_for("team/athena/outgoing/proposal.md"), ["B2"])

    def test_curriculum_paths_are_b1(self) -> None:
        self.assertEqual(CONFIG.boundaries_for("modules/02-prompting/lesson.md"), ["B1"])

    def test_ordinary_code_is_not_a_destination(self) -> None:
        self.assertEqual(CONFIG.boundaries_for("lib/extraction_scan.py"), [])
        self.assertEqual(CONFIG.boundaries_for("README.md"), [])

    def test_archive_is_exempt(self) -> None:
        self.assertEqual(CONFIG.boundaries_for("planning/archive/old.md"), [])

    def test_star_does_not_cross_a_separator(self) -> None:
        rx = ES._glob_to_regex("team/*/outgoing/**")
        self.assertTrue(rx.match("team/athena/outgoing/a.md"))
        self.assertFalse(rx.match("team/a/b/outgoing/a.md"))


class TestTier1(unittest.TestCase):
    def _codes(self, line, boundaries=("B2",), roster=None):
        hits = ES.scan_line_tier1(line, roster=roster or _empty_roster(),
                                  boundaries=boundaries)
        return [code for code, _m, _e, _t in hits]

    def test_sa_id_with_valid_checksum_fires(self) -> None:
        self.assertIn("PII_SA_ID", self._codes(f"ID {SA_ID} on file"))

    def test_thirteen_digits_failing_the_checksum_does_not_fire(self) -> None:
        bad = SA_ID[:-1] + str((int(SA_ID[-1]) + 1) % 10)
        self.assertNotIn("PII_SA_ID", self._codes(f"reference {bad}"))

    def test_thirteen_digits_with_impossible_date_does_not_fire(self) -> None:
        candidate = _luhn_complete("999999500108")
        self.assertNotIn("PII_SA_ID", self._codes(f"counter {candidate}"))

    def test_long_number_that_is_not_thirteen_digits_is_ignored(self) -> None:
        self.assertEqual(self._codes("point 1234567890123456789"), [])

    def test_email_fires(self) -> None:
        self.assertIn("PII_EMAIL", self._codes("contact jane.doe@acme-widgets.co.za"))

    def test_example_email_does_not_fire(self) -> None:
        self.assertEqual(self._codes("contact someone@example.com"), [])

    def test_participant_email_is_permitted_at_b2(self) -> None:
        roster = _roster([], ["tutor@team.invalid"])
        self.assertEqual(self._codes("owner tutor@team.invalid", ("B2",), roster), [])

    def test_participant_email_fires_at_b1(self) -> None:
        roster = _roster([], ["tutor@team.invalid"])
        self.assertIn("PARTICIPANT_EMAIL",
                      self._codes("owner tutor@team.invalid", ("B1",), roster))

    def test_sa_phone_fires(self) -> None:
        self.assertIn("PII_PHONE_SA", self._codes("call 082 555 1234"))

    def test_version_string_is_not_a_phone_number(self) -> None:
        self.assertEqual(self._codes("bumped to 1.20.0 today"), [])

    def test_credentials_fire(self) -> None:
        for line, code in (
            ("token=ghp_" + "a" * 30, "CREDENTIAL_GITHUB"),
            ("key AKIA" + "B" * 16, "CREDENTIAL_AWS_KEY_ID"),
            ("-----BEGIN RSA PRIVATE KEY-----", "CREDENTIAL_PRIVATE_KEY"),
            ("dsn postgres://svc:s3cretpw@db.internal:5432/app", "CREDENTIAL_CONNECTION_STRING"),
        ):
            with self.subTest(code=code):
                self.assertIn(code, self._codes(line))

    def test_placeholder_credentials_do_not_fire(self) -> None:
        self.assertEqual(self._codes("dsn postgres://user:<PASSWORD>@host/db"), [])
        self.assertEqual(self._codes("export TOKEN=ghp_your-token-here"), [])

    def test_findings_never_echo_the_value(self) -> None:
        secret = "ghp_" + "z" * 30
        hits = ES.scan_line_tier1(f"token={secret}", roster=_empty_roster(),
                                  boundaries=("B2",))
        self.assertTrue(hits)
        for _code, _msg, excerpt, _tier in hits:
            self.assertNotIn(secret, excerpt)


class TestTier3DestinationAwareness(unittest.TestCase):
    """The constraint the tier lives or dies by."""

    def test_participant_name_silent_at_b2(self) -> None:
        roster = _roster(["Ada Lovelace"])
        hits = ES.scan_line_tier3("Ada Lovelace reviewed the module.",
                                  roster=roster, boundaries=("B2",))
        self.assertEqual(hits, [])

    def test_participant_name_warns_at_b1(self) -> None:
        roster = _roster(["Ada Lovelace"])
        codes = [c for c, _m, _e, _t in ES.scan_line_tier3(
            "Ada Lovelace reviewed the module.", roster=roster, boundaries=("B1",))]
        self.assertEqual(codes, ["PARTICIPANT_NAME"])

    def test_participant_name_check_is_skipped_without_a_roster(self) -> None:
        hits = ES.scan_line_tier3("Ada Lovelace reviewed it.",
                                  roster=_empty_roster(), boundaries=("B1",))
        self.assertEqual(hits, [])

    def test_warnings_are_never_tier_one_or_two(self) -> None:
        roster = _roster(["Ada Lovelace"])
        for _c, _m, _e, tier in ES.scan_line_tier3(
                "Ada Lovelace was paid R12,347.51", roster=roster, boundaries=("B1",)):
            self.assertEqual(tier, ES.TIER_WARN)

    def test_round_amount_does_not_warn(self) -> None:
        codes = [c for c, _m, _e, _t in ES.scan_line_tier3(
            "budget of R50 000 for the pilot", roster=_empty_roster(), boundaries=("B1",))]
        self.assertNotIn("EXACT_AMOUNT", codes)

    def test_non_round_amount_warns(self) -> None:
        codes = [c for c, _m, _e, _t in ES.scan_line_tier3(
            "settled at R12,347.51 exactly", roster=_empty_roster(), boundaries=("B1",))]
        self.assertIn("EXACT_AMOUNT", codes)

    def test_bare_date_does_not_warn(self) -> None:
        codes = [c for c, _m, _e, _t in ES.scan_line_tier3(
            "Delivered on 2026-07-31.", roster=_empty_roster(), boundaries=("B1",))]
        self.assertEqual(codes, [])

    def test_corroborated_date_warns(self) -> None:
        roster = _roster(["Ada Lovelace"])
        codes = [c for c, _m, _e, _t in ES.scan_line_tier3(
            "Ada Lovelace filed it on 2026-07-31.", roster=roster, boundaries=("B1",))]
        self.assertIn("SPECIFIC_DATE", codes)

    def test_transcript_shape_detected(self) -> None:
        lines = ["Alex: what next", "Trainee: try the lab", "Alex: good",
                 "Trainee: done", "prose line"]
        hits = ES.scan_transcript_shape(lines, boundaries=("B1",))
        self.assertEqual([code for _l, code, _m in hits], ["TRANSCRIPT_SHAPE"])

    def test_transcript_shape_ignored_outside_a_destination(self) -> None:
        lines = ["Alex: a", "Trainee: b", "Alex: c", "Trainee: d"]
        self.assertEqual(ES.scan_transcript_shape(lines, boundaries=()), [])

    def test_prose_with_colons_is_not_a_transcript(self) -> None:
        lines = ["Note: this is prose.", "", "Another paragraph entirely."]
        self.assertEqual(ES.scan_transcript_shape(lines, boundaries=("B1",)), [])


class TestDeclarationParsing(unittest.TestCase):
    def test_well_formed_declaration_parses(self) -> None:
        decl = ES.parse_declaration(DECLARATION)
        self.assertTrue(decl.present)
        self.assertEqual(decl.boundaries, ("B2",))
        self.assertEqual(decl.errors, [])

    def test_absent_declaration(self) -> None:
        self.assertFalse(ES.parse_declaration("# A document\n\nprose\n").present)

    def test_missing_field_is_an_error(self) -> None:
        text = DECLARATION.replace("- Class P (participants): retained (B2 only)\n", "")
        errors = ES.parse_declaration(text).errors
        self.assertTrue(any("class_p" in e for e in errors))

    def test_unsigned_declaration_is_an_error(self) -> None:
        text = DECLARATION.replace("- Declared by: Hephaestus · session `abc12345` · 2026-07-31",
                                   "- Declared by: someone")
        self.assertTrue(any("Declared by" in e for e in ES.parse_declaration(text).errors))

    def test_declaration_naming_no_boundary_is_an_error(self) -> None:
        text = DECLARATION.replace("- Boundaries crossed: B2 (session → podzoneTeam planning)",
                                   "- Boundaries crossed: the planning repo")
        self.assertTrue(any("names no boundary" in e for e in ES.parse_declaration(text).errors))

    def test_declaration_missing_gate_reference_is_an_error(self) -> None:
        text = DECLARATION.replace("— gate: `PROJ-011-academy/session-to-curriculum-extraction-gate.md`", "")
        self.assertTrue(any("gate document" in e for e in ES.parse_declaration(text).errors))


class TestTier2Structural(unittest.TestCase):
    def _scan(self, path, text, brief=None):
        return ES.scan_text(path, text, config=CONFIG, roster=_empty_roster(), brief=brief)

    def test_missing_declaration_at_a_destination_fails(self) -> None:
        findings = self._scan("planning/projects/x/triage.md", "# Triage\n\nfindings\n")
        self.assertEqual([f.code for f in findings], ["DECLARATION_MISSING"])
        self.assertEqual(findings[0].tier, ES.TIER_STRUCTURAL)

    def test_declaration_present_passes(self) -> None:
        findings = self._scan("planning/projects/x/triage.md", "# Triage\n" + DECLARATION)
        self.assertEqual(findings, [])

    def test_no_declaration_required_outside_a_destination(self) -> None:
        self.assertEqual(self._scan("lib/thing.py", "x = 1\n"), [])
        self.assertEqual(self._scan("docs/notes.md", "# Notes\n"), [])

    def test_declaration_must_cover_the_destination_boundary(self) -> None:
        findings = self._scan("modules/02/lesson.md", "# Lesson\n" + DECLARATION)
        self.assertIn("DECLARATION_BOUNDARY_MISMATCH", [f.code for f in findings])

    def test_boundary_widening_against_the_brief_fails(self) -> None:
        brief = ES.parse_brief_authorisation(
            "**Extraction-gate:** x\n- Boundaries authorised: none\n")
        findings = self._scan("planning/x/triage.md", "# T\n" + DECLARATION, brief=brief)
        self.assertIn("BOUNDARY_WIDENED", [f.code for f in findings])

    def test_declared_within_authorised_passes(self) -> None:
        brief = ES.parse_brief_authorisation(
            "**Extraction-gate:** x\n- Boundaries authorised: B2\n")
        findings = self._scan("planning/x/triage.md", "# T\n" + DECLARATION, brief=brief)
        self.assertEqual(findings, [])


class TestBriefClause(unittest.TestCase):
    def test_none_parses(self) -> None:
        auth = ES.parse_brief_authorisation(
            "## Extraction authorisation\n\n**Extraction-gate:** `gate.md`\n"
            "- Boundaries authorised: none\n")
        self.assertTrue(auth.present)
        self.assertTrue(auth.explicit_none)
        self.assertEqual(auth.boundaries, ())
        self.assertEqual(auth.errors, [])

    def test_boundaries_parse(self) -> None:
        auth = ES.parse_brief_authorisation(
            "**Extraction-gate:** `gate.md`\n- Boundaries authorised: B1, B2\n")
        self.assertEqual(auth.boundaries, ("B1", "B2"))
        self.assertFalse(auth.permits("B4"))

    def test_absent_clause_is_an_error(self) -> None:
        auth = ES.parse_brief_authorisation("# Brief\n\nDo some work.\n")
        self.assertFalse(auth.present)
        self.assertTrue(auth.errors)

    def test_unparseable_value_is_an_error(self) -> None:
        auth = ES.parse_brief_authorisation(
            "**Extraction-gate:** `gate.md`\n- Boundaries authorised: as needed\n")
        self.assertTrue(any("must read" in e for e in auth.errors))

    def test_shipped_scaffold_template_carries_a_valid_clause(self) -> None:
        template = Path(__file__).resolve().parents[2] / "scaffold" / "brief.template"
        auth = ES.parse_brief_authorisation(template.read_text(encoding="utf-8"))
        self.assertTrue(auth.present)
        self.assertTrue(auth.explicit_none)
        self.assertEqual(auth.errors, [])


class TestRoster(unittest.TestCase):
    def test_unconfigured_by_default(self) -> None:
        self.assertFalse(ES.Roster.load(None).configured)

    def test_loads_from_a_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roster.json"
            path.write_text(json.dumps({"names": ["Ada Lovelace"],
                                        "emails": ["Ada@Example.Invalid"]}), encoding="utf-8")
            roster = ES.Roster.load(str(path))
            self.assertTrue(roster.configured)
            self.assertTrue(roster.is_participant_email("ada@example.invalid"))

    def test_shipped_roster_is_the_example_only(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        self.assertFalse((repo / "data" / "participant-roster.json").exists(),
                         "a real roster must never be committed to this public repo")
        self.assertTrue((repo / "data" / "participant-roster.example.json").exists())


class TestAddedLinesOnly(unittest.TestCase):
    def test_only_added_lines_are_content_scanned(self) -> None:
        text = f"pre-existing: {SA_ID}\nnew line\n"
        findings = ES.scan_text("planning/x/doc.md", text + DECLARATION,
                                config=CONFIG, roster=_empty_roster(),
                                added_lines=[(2, "new line")])
        self.assertEqual(findings, [])

    def test_added_line_content_is_scanned(self) -> None:
        findings = ES.scan_text("planning/x/doc.md", DECLARATION, config=CONFIG,
                                roster=_empty_roster(),
                                added_lines=[(1, f"claimant ID {SA_ID}")])
        self.assertEqual([f.code for f in findings], ["PII_SA_ID"])


if __name__ == "__main__":
    unittest.main()
