#!/usr/bin/env python3
"""
proj039-live-checks.py — credentialled live checks for the PROJ-039 MVP gate.

Three checks, runnable singly or together (default: all):

  dt001   semantic-recall harness (AC-002 / MVP-11): seed the 10 labelled
          fixture briefs as point_type=brief test points, embed each query via
          Ollama, search the `brief` named vector, score top-3 recall. Pass:
          ≥ 8/10 AND grep-baseline = 0. All test points cleaned up after.
  ot006   upsert-discipline crux (SD-3-001 / F-2-008): create a session point
          with a `brief` vector, patch the `response` vector via upsert_response,
          GET with vectors, assert the `brief` vector is still present and
          bit-identical. Prints the before/after evidence for the outbox.
  dt015   index-before-ingest probe (F-2-004): GET the collection info and
          assert `point_type` appears in `payload_schema`.

Needs PODZONE_QDRANT_APIKEY (cloud Qdrant) and, for dt001/ot006, a reachable
Ollama (OLLAMA_HOST, default localhost:11434). Run under
`mcp__secrets__secret_run -k podzone_qdrant_apikey` / `secretctl run`.

This is a tool, not a hook: failures are loud (non-zero exit), per the
qdrant_http philosophy — a green no-op would defeat the point of a re-run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib import qdrant_http, recall_harness, session_substrate  # noqa: E402

COLLECTION = session_substrate.COLLECTION


def _vector_evidence(vec: list[float]) -> str:
    """Compact, comparable evidence line for a 768-dim vector."""
    digest = hashlib.sha256(
        json.dumps(vec, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    head = ", ".join(f"{v:.6f}" for v in vec[:4])
    return f"dim={len(vec)} sha256[:16]={digest} head=[{head}, …]"


def _named_vector(result: dict, name: str) -> list[float] | None:
    vec = (result or {}).get("vector") or {}
    value = vec.get(name)
    return value if isinstance(value, list) and value else None


def check_dt001() -> bool:
    print("== DT-001 — semantic recall (AC-002 / MVP-11) ==")
    pairs = recall_harness.load_pairs()

    baseline = recall_harness.grep_baseline(pairs)
    nonzero = {s: n for s, n in baseline.items() if n != 0}
    print(f"grep-baseline: {sum(baseline.values())} hits across "
          f"{len(pairs)} queries (require 0)")
    if nonzero:
        print(f"FAIL: lexical hits {nonzero}")
        return False

    print("embedding 10 briefs via Ollama …")
    vectors = {p["slug"]: session_substrate.embed_text(p["brief"]) for p in pairs}
    points = recall_harness.seed_points(pairs, vectors)
    ids = [pt["id"] for pt in points]
    ok = False
    try:
        qdrant_http.upsert_points(points, collection=COLLECTION)
        # upsert defaults to wait=false; poll until the last point is readable.
        deadline = time.time() + 15
        while time.time() < deadline:
            if qdrant_http.get_point(ids[-1], collection=COLLECTION) is not None:
                break
            time.sleep(0.5)
        else:
            print("FAIL: seeded points not readable within 15 s")
            return False
        print(f"seeded {len(points)} point_type=brief fixture points")

        # Filter by point_type=brief (indexed) to constrain search to fixture
        # corpus; test_marker is not indexed so must not be used in a filter.
        brief_filter = {
            "must": [{"key": "point_type", "match": {"value": "brief"}}]
        }
        results_by_slug: dict[str, list[str]] = {}
        for p in pairs:
            qvec = session_substrate.embed_text(p["query"])
            hits = qdrant_http.search(
                qvec, collection=COLLECTION, using="brief",
                limit=recall_harness.TOP_K, query_filter=brief_filter,
            )
            results_by_slug[p["slug"]] = [
                h.get("payload", {}).get("slug", "?") for h in hits
            ]

        score = recall_harness.top3_recall(results_by_slug)
        for slug, ranked in results_by_slug.items():
            mark = "✓" if score["per_query"][slug] else "✗"
            print(f"  {mark} {slug:<20} top-3: {ranked}")
        print(f"recall: {score['hits']}/{score['total']} top-3 "
              f"(threshold ≥ {recall_harness.PASS_THRESHOLD}) "
              f"grep-baseline=0 → {'PASS' if score['passed'] else 'FAIL'}")
        ok = score["passed"]
    finally:
        # Delete by the deterministic IDs we just seeded — no scroll needed.
        qdrant_http.delete_points(ids, collection=COLLECTION)
        print(f"cleanup: deleted {len(ids)} fixture points")
    return ok


def check_ot006() -> bool:
    print("== OT-006 — brief vector survives the response patch "
          "(SD-3-001 / F-2-008) ==")
    session_id = f"ot006-live-{uuid.uuid4()}"
    pid = session_substrate.point_id_for(session_id)
    ok = False
    try:
        session_substrate.create_session_point(
            session_id=session_id, agent="proj039-live-check",
            work_item="PROJ-039/T-009/OT-006",
            brief_text="Live OT-006 probe: verify the brief named vector "
                       "survives a response-vector patch via update-vectors.",
        )
        before = qdrant_http.get_point(pid, collection=COLLECTION, with_vector=True)
        brief_before = _named_vector(before, "brief")
        if brief_before is None:
            print("FAIL: brief vector absent after creation")
            return False
        print(f"before patch: brief  {_vector_evidence(brief_before)}")

        session_substrate.upsert_response(
            session_id,
            text="Live OT-006 probe response: this text is embedded and "
                 "patched onto the response named vector.",
            status_transition="in_progress->complete",
        )
        after = qdrant_http.get_point(pid, collection=COLLECTION, with_vector=True)
        brief_after = _named_vector(after, "brief")
        response_after = _named_vector(after, "response")
        if brief_after is None:
            print("FAIL: brief vector ABSENT after response patch — F-2-008 broken")
            return False
        print(f"after  patch: brief  {_vector_evidence(brief_after)}")
        print(f"after  patch: response {_vector_evidence(response_after or [])}")

        unchanged = brief_before == brief_after
        print(f"brief vector present and unchanged: {unchanged}; "
              f"response vector present: {response_after is not None} "
              f"→ {'PASS' if unchanged and response_after else 'FAIL'}")
        ok = unchanged and response_after is not None
    finally:
        qdrant_http.delete_points([pid], collection=COLLECTION)
        print(f"cleanup: deleted test point {pid}")
    return ok


def check_dt015() -> bool:
    print("== DT-015 — point_type index live in payload_schema (F-2-004) ==")
    info = qdrant_http.request_json(
        "GET", f"{qdrant_http.CLOUD_QDRANT_URL}/collections/{COLLECTION}"
    )
    schema = info.get("result", {}).get("payload_schema", {})
    print(f"payload_schema fields: {sorted(schema.keys())}")
    entry = schema.get("point_type")
    ok = entry is not None and entry.get("data_type") == "keyword"
    print(f"point_type indexed as keyword: {ok} → {'PASS' if ok else 'FAIL'}")
    return ok


CHECKS = {"dt001": check_dt001, "ot006": check_ot006, "dt015": check_dt015}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checks", nargs="*", choices=[*CHECKS, []],
                        help="subset to run (default: all)")
    args = parser.parse_args()
    selected = args.checks or list(CHECKS)

    results = {}
    for name in selected:
        results[name] = CHECKS[name]()
        print()
    print("== summary ==")
    for name, ok in results.items():
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
