"""PROJ-011/T-025 (CC-356) — trainee repo structure v2.

Unit coverage for the pieces that are not exercised by the scaffold shell test
(`tests/scaffold/test-scaffold-trainee.sh`):

  * R-14 finalise log-copy helper — the session transcript is copied into the repo's
    ``logs/`` (so it rides the R-3 session PR) instead of being pushed to a fleet
    telemetry remote.
  * R-11 artifact-hygiene detector — catches the baseline leaks (template-named
    workspace/identity, FILL-IN placeholders, README/AGENTS provenance, root session
    work) and passes a clean tree.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

AT = Path(__file__).resolve().parents[2]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


FIN = _load(AT / "hooks" / "session-end-finalise.py", "sef")
QA = _load(AT / "tools" / "qa-snapshot.py", "qa_snapshot")


class TestR14LogCopy(unittest.TestCase):
    SID = "abcd1234-0000-0000-0000-000000000000"

    def test_copies_transcript_into_logs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript = root / "session.jsonl"
            transcript.write_text('{"type":"user"}\n', encoding="utf-8")
            repo = root / "repo"
            repo.mkdir()
            wrote = FIN._copy_session_log_for_trainee(str(transcript), str(repo), self.SID)
            self.assertTrue(wrote)
            out = repo / "logs" / "session-abcd1234.jsonl"
            self.assertTrue(out.is_file())
            self.assertEqual(out.read_text(encoding="utf-8"), '{"type":"user"}\n')

    def test_missing_transcript_is_soft_noop(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            self.assertFalse(
                FIN._copy_session_log_for_trainee("/no/such/file.jsonl", str(repo), self.SID))
            self.assertFalse((repo / "logs").exists())


class TestR11Hygiene(unittest.TestCase):
    def _clean_repo(self, root: Path):
        (root / "README.md").write_text("# repo\n\nOperating manual.\n", encoding="utf-8")
        (root / "AGENTS.md").write_text("# Trainee\n\nContext.\n", encoding="utf-8")
        (root / "docs").mkdir()
        # A doc that legitimately *is* a template must NOT be flagged.
        (root / "docs" / "trainee-profile-template.md").write_text("profile\n", encoding="utf-8")
        (root / "Trainee").mkdir()
        (root / "Trainee" / "outputDocs").mkdir()
        (root / "Trainee" / "outputDocs" / "summary.md").write_text("s\n", encoding="utf-8")

    def test_clean_tree_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._clean_repo(root)
            self.assertEqual(QA.hygiene_violations(str(root)), [])

    def test_template_named_workspace_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._clean_repo(root)
            (root / "home-training-template.code-workspace").write_text("{}", encoding="utf-8")
            v = QA.hygiene_violations(str(root))
            self.assertTrue(any("template-named" in x for x in v), v)

    def test_martin_template_identity_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._clean_repo(root)
            idd = root / "workspaces" / "identity"
            idd.mkdir(parents=True)
            (idd / "martin-template-coder.identity.yaml").write_text("agent: x\n", encoding="utf-8")
            v = QA.hygiene_violations(str(root))
            self.assertTrue(any("template-named" in x for x in v), v)

    def test_fill_in_placeholder_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._clean_repo(root)
            (root / ".claude").mkdir()
            (root / ".claude" / "instructions.md").write_text("Role: FILL IN this\n", encoding="utf-8")
            v = QA.hygiene_violations(str(root))
            self.assertTrue(any("FILL-IN" in x for x in v), v)

    def test_readme_provenance_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._clean_repo(root)
            (root / "README.md").write_text("# repo\n\nScaffolded per PROJ-039 T-044.\n",
                                            encoding="utf-8")
            v = QA.hygiene_violations(str(root))
            self.assertTrue(any("provenance" in x for x in v), v)

    def test_root_session_work_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._clean_repo(root)
            (root / "answer.md").write_text("leaked session output\n", encoding="utf-8")
            v = QA.hygiene_violations(str(root))
            self.assertTrue(any("repo root" in x for x in v), v)


if __name__ == "__main__":
    unittest.main()
