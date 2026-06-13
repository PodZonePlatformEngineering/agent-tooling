"""
recall_harness.py — DT-001 semantic-recall harness logic (PROJ-039 AC-002 / MVP-11).

The DT-001 contract (DTD § 4.2): on a corpus of N=10 manually-labelled brief
pairs, a semantic query over the `brief` named vector retrieves the correct
counterpart in the top-3 for ≥ 8 of 10, where a grep on the query text alone
returns no result — so the win is provably semantic, not lexical.

This module holds the deterministic halves (fixture loading, the grep
baseline, the top-3 recall computation) so they are unit-testable offline;
the live half (seed → embed → search → clean up against cloud
`session_substrate` + Ollama) lives in ``tools/proj039-live-checks.py`` and
composes these same functions, so the numbers the live run records are
produced by tested code.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests" / "proj039" / "fixtures" / "recall_pairs.json"
)

PASS_THRESHOLD = 8  # ≥ 8/10 top-3 (DT-001)
TOP_K = 3
TEST_MARKER = "proj039-dt001-fixture"


def load_pairs(path: str | Path = FIXTURE_PATH) -> list[dict]:
    """Load the labelled corpus: ``[{slug, brief, query}, …]``."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data["pairs"]


def grep_baseline(pairs: list[dict]) -> dict[str, int]:
    """Per-query hit count of the raw query phrase over the whole brief corpus.

    Case-insensitive fixed-string containment — the literal `grep -iF` a
    sceptic would run. DT-001 requires every count to be 0: if a query phrase
    appeared verbatim in any brief, top-3 retrieval could be lexical luck
    rather than a semantic win.
    """
    corpus = [p["brief"].lower() for p in pairs]
    return {
        p["slug"]: sum(1 for brief in corpus if p["query"].lower() in brief)
        for p in pairs
    }


def point_id_for_pair(slug: str) -> str:
    """Deterministic point ID for a seeded fixture brief (stable cleanup target)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{TEST_MARKER}-{slug}"))


def seed_points(pairs: list[dict], vectors: dict[str, list[float]]) -> list[dict]:
    """Build the ``point_type=brief`` fixture points for upsert.

    ``vectors`` maps slug → embedded brief text. Every point carries
    ``test_marker`` so a filtered scroll can find strays if a run dies
    mid-way and the deterministic-ID delete misses.
    """
    return [
        {
            "id": point_id_for_pair(p["slug"]),
            "vector": {"brief": vectors[p["slug"]]},
            "payload": {
                "point_type": "brief",
                "slug": p["slug"],
                "brief": {"text": p["brief"]},
                "test_marker": TEST_MARKER,
            },
        }
        for p in pairs
    ]


def top3_recall(results_by_slug: dict[str, list[str]], *, k: int = TOP_K) -> dict:
    """Score the run: ``results_by_slug`` maps query slug → ranked result slugs.

    Returns ``{hits, total, per_query: {slug: bool}, passed}`` where a hit is
    the target slug appearing in the first ``k`` results for its own query.
    """
    per_query = {
        slug: slug in ranked[:k] for slug, ranked in results_by_slug.items()
    }
    hits = sum(per_query.values())
    total = len(per_query)
    return {
        "hits": hits,
        "total": total,
        "per_query": per_query,
        "passed": hits >= PASS_THRESHOLD and total == 10,
    }
