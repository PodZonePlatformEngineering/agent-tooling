"""Tests for the PROJ-038/T-002 trajectory-manifest-builder.

Covers:
  - Per-source scans against synthetic team-root + project-dir fixtures
    (AC-MB-004 reject reasons, AC-MB-001 schema via re-load).
  - cwd_slugs resolution: spec.md frontmatter; READMEFIRST.md fallback;
    --cwd-slug override; empty case warns + skips jsonl.
  - --dry-run does not write (AC-MB-003).
  - Re-run determinism with identical inputs (AC-MB-005).
  - Integration: emitted manifest re-loads via lib.decay.manifest.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import manifest_builder  # noqa: E402
from lib.decay.manifest import load_manifest  # noqa: E402

TOOL_PATH = REPO_ROOT / "tools" / "trajectory-manifest-builder.py"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_fixture(tmp: Path, *, with_spec: bool = True,
                  with_readmefirst: bool = False,
                  spec_slugs: list[str] | None = None,
                  readme_slugs: list[str] | None = None) -> tuple[Path, Path]:
    """Return (project_dir, team_root)."""
    team_root = tmp / "podzoneTeam"
    project_dir = team_root / "planning" / "projects" / "PROJ-003-gitopsapi-product"
    project_dir.mkdir(parents=True)

    if with_spec:
        slugs = spec_slugs if spec_slugs is not None else ["gitopsapi"]
        slug_yaml = "[" + ", ".join(slugs) + "]"
        _write(project_dir / "spec.md",
               f"---\nproject: PROJ-003\ncwd_slugs: {slug_yaml}\n---\n\n# spec\n")
    if with_readmefirst:
        slugs = readme_slugs if readme_slugs is not None else ["gitopsapi"]
        slug_yaml = "[" + ", ".join(slugs) + "]"
        _write(project_dir / "READMEFIRST.md",
               f"---\nproject: PROJ-003\ncwd_slugs: {slug_yaml}\n---\n\n# readme\n")

    # Outbox: one matching, one non-matching.
    _write(team_root / "team" / "hephaestus" / "outgoing"
           / "session-2026-05-20-status.md",
           "Worked on PROJ-003 today.\n")
    _write(team_root / "team" / "hermes" / "outgoing"
           / "session-2026-05-21-other.md",
           "Worked on PROJ-007 today.\n")
    # Incoming: one matching brief.
    _write(team_root / "team" / "hephaestus" / "incoming"
           / "2026-05-19-gitopsapi-task.md",
           "Brief: do something for proj-003.\n")
    # Incoming non-matching.
    _write(team_root / "team" / "hephaestus" / "incoming"
           / "2026-05-19-unrelated.md",
           "Brief: do something else.\n")
    return project_dir, team_root


class TestCwdSlugResolution(unittest.TestCase):

    def test_spec_frontmatter_primary(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            project_dir, _ = _make_fixture(
                tmp, with_spec=True, with_readmefirst=True,
                spec_slugs=["from-spec"], readme_slugs=["from-readme"],
            )
            self.assertEqual(
                manifest_builder.resolve_cwd_slugs(project_dir),
                ["from-spec"],
            )
        finally:
            shutil.rmtree(tmp)

    def test_readmefirst_fallback(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            project_dir, _ = _make_fixture(
                tmp, with_spec=False, with_readmefirst=True,
                readme_slugs=["from-readme"],
            )
            self.assertEqual(
                manifest_builder.resolve_cwd_slugs(project_dir),
                ["from-readme"],
            )
        finally:
            shutil.rmtree(tmp)

    def test_empty_when_neither(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            project_dir, _ = _make_fixture(
                tmp, with_spec=False, with_readmefirst=False,
            )
            self.assertEqual(manifest_builder.resolve_cwd_slugs(project_dir), [])
        finally:
            shutil.rmtree(tmp)


class TestOutboxIncomingScans(unittest.TestCase):

    def test_outbox_finds_matching_only(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            _, team_root = _make_fixture(tmp)
            entries = manifest_builder.scan_outbox_artefacts(
                "PROJ-003", team_root,
            )
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["type"], "outbox")
            self.assertEqual(entries[0]["role"], "hephaestus")
            self.assertTrue(entries[0]["path"].endswith(
                "session-2026-05-20-status.md"))
            self.assertTrue(entries[0]["timestamp"].startswith("2026-05-20"))
        finally:
            shutil.rmtree(tmp)

    def test_incoming_finds_lowercase_mention(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            _, team_root = _make_fixture(tmp)
            entries = manifest_builder.scan_incoming_artefacts(
                "PROJ-003", team_root,
            )
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["type"], "brief")
            self.assertTrue(entries[0]["path"].endswith(
                "2026-05-19-gitopsapi-task.md"))
        finally:
            shutil.rmtree(tmp)

    def test_date_window_filter(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            _, team_root = _make_fixture(tmp)
            entries = manifest_builder.scan_outbox_artefacts(
                "PROJ-003", team_root,
                start_iso="2026-05-25T00:00:00+00:00",
            )
            self.assertEqual(entries, [])
        finally:
            shutil.rmtree(tmp)


class TestBuildEndToEnd(unittest.TestCase):

    def test_build_skips_jsonl_when_no_slugs_and_override_absent(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            project_dir, team_root = _make_fixture(
                tmp, with_spec=False, with_readmefirst=False,
            )
            result = manifest_builder.build(
                project_dir=project_dir,
                team_root=team_root,
                include=("jsonl", "outbox", "incoming"),
                verbose=True,
            )
            self.assertEqual(len(result.artefacts), 2)  # 1 outbox + 1 incoming
            self.assertTrue(any("no cwd_slugs" in line
                                for line in result.verbose_log))
        finally:
            shutil.rmtree(tmp)

    def test_build_sorted_chronologically(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            project_dir, team_root = _make_fixture(tmp)
            result = manifest_builder.build(
                project_dir=project_dir,
                team_root=team_root,
                include=("outbox", "incoming"),
            )
            timestamps = [a["timestamp"] for a in result.artefacts]
            self.assertEqual(timestamps, sorted(timestamps))
        finally:
            shutil.rmtree(tmp)

    def test_re_run_byte_identical(self):
        """AC-MB-005 — determinism with identical inputs."""
        tmp = Path(tempfile.mkdtemp())
        try:
            project_dir, team_root = _make_fixture(tmp)
            r1 = manifest_builder.build(
                project_dir=project_dir, team_root=team_root,
                include=("outbox", "incoming"),
            )
            r2 = manifest_builder.build(
                project_dir=project_dir, team_root=team_root,
                include=("outbox", "incoming"),
            )
            self.assertEqual(
                manifest_builder.dump_yaml(r1.manifest),
                manifest_builder.dump_yaml(r2.manifest),
            )
        finally:
            shutil.rmtree(tmp)

    def test_emitted_manifest_validates(self):
        """AC-MB-001 — output reloadable by canonical loader."""
        tmp = Path(tempfile.mkdtemp())
        try:
            project_dir, team_root = _make_fixture(tmp)
            result = manifest_builder.build(
                project_dir=project_dir, team_root=team_root,
                include=("outbox", "incoming"),
            )
            out = tmp / "manifest.yaml"
            out.write_text(manifest_builder.dump_yaml(result.manifest))
            loaded = load_manifest(out)
            self.assertEqual(len(loaded), 2)
        finally:
            shutil.rmtree(tmp)


class TestCli(unittest.TestCase):

    def test_dry_run_does_not_write(self):
        """AC-MB-003."""
        tmp = Path(tempfile.mkdtemp())
        try:
            project_dir, team_root = _make_fixture(tmp)
            iter_dir = project_dir / "iterations" / "iteration-1"
            manifest_path = iter_dir / "trajectory-manifest.yaml"
            self.assertFalse(manifest_path.exists())

            r = subprocess.run(
                [sys.executable, str(TOOL_PATH),
                 "--project-dir", str(project_dir),
                 "--team-root", str(team_root),
                 "--include", "outbox", "--include", "incoming",
                 "--dry-run"],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("artefacts:", r.stdout)
            self.assertFalse(manifest_path.exists())
        finally:
            shutil.rmtree(tmp)

    def test_default_output_path_and_validation(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            project_dir, team_root = _make_fixture(tmp)
            r = subprocess.run(
                [sys.executable, str(TOOL_PATH),
                 "--project-dir", str(project_dir),
                 "--team-root", str(team_root),
                 "--include", "outbox", "--include", "incoming"],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = project_dir / "iterations" / "iteration-1" / "trajectory-manifest.yaml"
            self.assertTrue(out.exists())
            loaded = load_manifest(out)
            self.assertEqual(len(loaded), 2)
        finally:
            shutil.rmtree(tmp)

    def test_verbose_logs_reject_reasons(self):
        """AC-MB-004."""
        tmp = Path(tempfile.mkdtemp())
        try:
            project_dir, team_root = _make_fixture(tmp)
            r = subprocess.run(
                [sys.executable, str(TOOL_PATH),
                 "--project-dir", str(project_dir),
                 "--team-root", str(team_root),
                 "--include", "outbox", "--include", "incoming",
                 "--dry-run", "--verbose"],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("reject", r.stderr)
            self.assertIn("include", r.stderr)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
