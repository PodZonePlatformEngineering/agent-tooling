"""PROJ-011/T-030 — training-config.yaml loader (lib/training_config.py).

The committed config file is the single trainee configuration surface (R2-2);
its validation is the R2-3 isolation guarantee: a config naming a fleet
collection must refuse to load, so no trainee-role hook can produce a
fleet-collection URL by construction.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lib import training_config as TC


VALID = """\
# training-config.yaml — the single trainee configuration surface
qdrant_url: https://example.cloud.qdrant.io:6333
qdrant_api_key: "real-scoped-key"
trainee: sam
operational_brief_id: training/sam/operational
briefs_collection: training_briefs
telemetry_collection: training_session_telemetry
"""


def _write(tmp: Path, text: str) -> Path:
    path = tmp / TC.CONFIG_FILENAME
    path.write_text(text, encoding="utf-8")
    return path


class TestParseFlatYaml(unittest.TestCase):
    def test_parses_comments_quotes_and_blanks(self) -> None:
        cfg = TC.parse_flat_yaml(VALID)
        self.assertEqual(cfg["qdrant_api_key"], "real-scoped-key")   # quotes stripped
        self.assertEqual(cfg["trainee"], "sam")
        self.assertEqual(len(cfg), 6)                                # comment line ignored

    def test_inline_comment_stripped(self) -> None:
        cfg = TC.parse_flat_yaml("trainee: sam  # the handle\n")
        self.assertEqual(cfg["trainee"], "sam")

    def test_nested_yaml_rejected(self) -> None:
        with self.assertRaises(TC.TrainingConfigError):
            TC.parse_flat_yaml("outer:\n  inner: 1\n")

    def test_non_kv_line_rejected(self) -> None:
        with self.assertRaises(TC.TrainingConfigError):
            TC.parse_flat_yaml("just a stray line\n")


class TestValidate(unittest.TestCase):
    def _cfg(self, **overrides) -> dict:
        cfg = TC.parse_flat_yaml(VALID)
        cfg.update(overrides)
        return cfg

    def test_valid_config_passes(self) -> None:
        self.assertEqual(TC.validate(self._cfg())["trainee"], "sam")

    def test_missing_key_rejected(self) -> None:
        with self.assertRaises(TC.TrainingConfigError):
            TC.validate(self._cfg(qdrant_url=""))

    def test_fleet_collections_rejected(self) -> None:
        # R2-3 by construction: every fleet collection name refuses to load.
        for fleet in ("briefs", "session_substrate", "claude_session_telemetry",
                      "prompt_logs", "task_events"):
            for key in ("briefs_collection", "telemetry_collection"):
                with self.assertRaises(TC.TrainingConfigError, msg=f"{key}={fleet}"):
                    TC.validate(self._cfg(**{key: fleet}))

    def test_registry_collection_rejected_for_trainee(self) -> None:
        # training_token_registry is training-team-owned — out of trainee reach.
        with self.assertRaises(TC.TrainingConfigError):
            TC.validate(self._cfg(briefs_collection="training_token_registry"))

    def test_non_training_brief_id_rejected(self) -> None:
        with self.assertRaises(TC.TrainingConfigError):
            TC.validate(self._cfg(operational_brief_id="podzone/2026-07-12-x"))

    def test_non_http_url_rejected(self) -> None:
        with self.assertRaises(TC.TrainingConfigError):
            TC.validate(self._cfg(qdrant_url="qdrant.example.com:6333"))


class TestLoadAndConfigured(unittest.TestCase):
    def test_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp), VALID)
            cfg = TC.load(tmp)
        self.assertTrue(TC.is_configured(cfg))
        self.assertEqual(TC.qdrant_kwargs(cfg),
                         {"qdrant_url": "https://example.cloud.qdrant.io:6333",
                          "api_key": "real-scoped-key"})

    def test_missing_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TC.TrainingConfigError):
                TC.load(tmp)

    def test_placeholder_config_loads_but_unconfigured(self) -> None:
        # The scaffold-emitted template must VALIDATE (shape is right) while
        # is_configured() stays False until take-on fills the key.
        text = VALID.replace('"real-scoped-key"', '"{{TRAINING_DB_API_KEY}}"') \
                    .replace("sam", "{{TRAINEE_HANDLE}}")
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp), text)
            cfg = TC.load(tmp)
        self.assertFalse(TC.is_configured(cfg))

    def test_trailing_slash_url_normalised(self) -> None:
        cfg = TC.validate(TC.parse_flat_yaml(
            VALID.replace(":6333", ":6333/")))
        self.assertEqual(TC.qdrant_kwargs(cfg)["qdrant_url"],
                         "https://example.cloud.qdrant.io:6333")


if __name__ == "__main__":
    unittest.main()
