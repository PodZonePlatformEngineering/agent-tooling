"""
training_config.py — the committed trainee-repo configuration surface
(PROJ-011/T-030, trainee-repo template v3).

``training-config.yaml`` at the trainee repo root is the SINGLE configuration
surface for the trainee hook set (R2-2/R2-3): the trainee's granular Database
API Key (console-minted, scoped rw to the two training collections — see
docs/training-jwt-runbook.md for why self-signed JWTs are not an option), the
operational ``brief_id``, and the training-collection names. The
operational-brief update channel edits THIS file; nothing else configures a
trainee-role hook — there is no fleet env key (PODZONE_QDRANT_APIKEY) in the
trainee runtime.

Parsed with a deliberate stdlib-only flat ``key: value`` reader — trainee
hosts are bare interpreters (no pyyaml, no requests), and the flat shape is
the whole grammar: the scaffold emits it, the take-on fills it, this module
refuses anything fancier.

Validation IS the R2-3 isolation guarantee: :func:`load` raises unless every
collection name is in ``training_substrate.TRAINEE_COLLECTIONS`` and the
brief id is under ``training/``. A config naming a fleet collection
(``briefs`` / ``session_substrate`` / ``claude_session_telemetry``) refuses
to load, so no trainee-role hook can produce a fleet-collection URL by
construction — the property tests/proj011/test_trainee_routing.py asserts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from lib.training_substrate import TRAINEE_COLLECTIONS

CONFIG_FILENAME = "training-config.yaml"

# Keys the loader requires. qdrant_url is config (not env) so the file really
# is the single surface the operational channel edits.
REQUIRED_KEYS = (
    "qdrant_url",
    "qdrant_api_key",
    "trainee",
    "operational_brief_id",
    "briefs_collection",
    "telemetry_collection",
)

# Scaffold-time placeholder convention: any {{TOKEN}} value means "take-on has
# not filled this yet" — is_configured() turns the hooks into quiet no-ops.
_PLACEHOLDER_MARK = "{{"


class TrainingConfigError(ValueError):
    """A malformed / fleet-reaching training-config.yaml. Loud at load time;
    the HOOKS catch it and degrade soft (log + continue, R2-4)."""


def parse_flat_yaml(text: str) -> dict:
    """The whole supported grammar: ``key: value`` lines, ``#`` comments,
    optional single/double quotes around the value. Nesting is rejected —
    the config surface is deliberately flat."""
    out: dict = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line[0] in " \t":
            raise TrainingConfigError(
                f"{CONFIG_FILENAME}:{lineno}: nested/indented entries are not "
                "supported — the config surface is flat key: value only")
        if ":" not in line:
            raise TrainingConfigError(
                f"{CONFIG_FILENAME}:{lineno}: expected 'key: value', got {line!r}")
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def config_path(repo_root: Optional[str] = None) -> Path:
    root = repo_root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return Path(root) / CONFIG_FILENAME


def validate(cfg: dict) -> dict:
    """Raise :class:`TrainingConfigError` unless ``cfg`` is complete and can
    only ever address the training collections."""
    missing = [k for k in REQUIRED_KEYS if not str(cfg.get(k, "")).strip()]
    if missing:
        raise TrainingConfigError(
            f"{CONFIG_FILENAME} is missing required keys: {', '.join(missing)}")
    for key in ("briefs_collection", "telemetry_collection"):
        name = cfg[key]
        if name not in TRAINEE_COLLECTIONS:
            raise TrainingConfigError(
                f"{CONFIG_FILENAME}: {key}={name!r} is not a training collection "
                f"(allowed: {', '.join(TRAINEE_COLLECTIONS)}) — trainee hooks "
                "never touch fleet collections (R2-3)")
    if not cfg["operational_brief_id"].startswith("training/"):
        raise TrainingConfigError(
            f"{CONFIG_FILENAME}: operational_brief_id must be under 'training/' "
            f"(got {cfg['operational_brief_id']!r})")
    if not cfg["qdrant_url"].lower().startswith(("http://", "https://")):
        raise TrainingConfigError(
            f"{CONFIG_FILENAME}: qdrant_url must be an http(s) URL "
            f"(got {cfg['qdrant_url']!r})")
    return cfg


def load(repo_root: Optional[str] = None) -> dict:
    """Load + validate the repo's training-config.yaml.

    Raises :class:`TrainingConfigError` when the file is absent or invalid —
    hooks catch this and no-op soft. A PLACEHOLDER config (take-on not done)
    loads fine; gate network use on :func:`is_configured`.
    """
    path = config_path(repo_root)
    if not path.is_file():
        raise TrainingConfigError(f"{path} not found — is this a trainee repo?")
    return validate(parse_flat_yaml(path.read_text(encoding="utf-8")))


def is_configured(cfg: dict) -> bool:
    """True once take-on Phase A has filled the real Database API Key (no
    {{PLACEHOLDER}} left in the key or the brief id)."""
    return not any(
        _PLACEHOLDER_MARK in str(cfg.get(k, ""))
        for k in ("qdrant_api_key", "operational_brief_id", "trainee")
    )


def qdrant_kwargs(cfg: dict) -> dict:
    """The routing kwargs every trainee-hook qdrant_http call passes — url +
    key come from the config file, never from the fleet env."""
    return {"qdrant_url": cfg["qdrant_url"].rstrip("/"),
            "api_key": cfg["qdrant_api_key"]}
