#!/usr/bin/env python3
"""check-cf-pages-required-vars.py — PROJ-011/ACP-403 follow-up (2026-08-20).

Read-only drift check: verifies each academy-* Cloudflare Pages project has
every backend env var its Functions actually require, at the production
deployment config. Built after this required var went missing TWICE in one
session on newly-created projects (STACK_PROJECT_ID, then
NEON_DATABASE_URL + PODZONE_QDRANT_APIKEY) — both times discovered only via
a live user-facing bug report, not proactively. `withClient()`/`qdrantCall()`
in academy-web/academy-frontend now fail fast on a missing var instead of
hanging (same follow-up), but this check is the "catch it before a human
hits it" half — run it from `/consolidate-tasks` or standalone whenever a
new academy-* CF Pages project is created.

This is a CHECK only — it never writes. Fixing a reported gap is a manual
`curl -X PATCH` against the Cloudflare Pages API (see this session's
transcript / academy-gui#19 for the pattern) or a future `--fix` mode if
this proves worth automating further.

Auth: needs a Cloudflare API token with Pages:Read + account id, via
CLOUDFLARE_API_TOKEN/CLOUDFLARE_ACCOUNT_ID, or (this fleet's vault key
names) CLOUDFLARE_PODZONE_TOKEN/CLOUDFLARE_PODZONE_ACCOUNT — e.g.:

    mcp__secrets__secret_run -k cloudflare-podzone-token -k cloudflare-podzone-account -- \\
        python3 tools/check-cf-pages-required-vars.py

Exit code 0 = everything present, 1 = at least one project is missing a
required var (report printed either way).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# Every project's Functions read env.NEON_DATABASE_URL / env.PODZONE_QDRANT_APIKEY
# (functions/_lib/db.ts, functions/_lib/qdrant.ts, both academy-web and
# academy-frontend) plus env.STACK_PROJECT_ID (functions/_lib/jwt.ts) for the
# assessment backend specifically. Update this manifest if a project's
# Functions gain/drop a required var.
REQUIRED_VARS: dict[str, list[str]] = {
    "academy-web-vibe": ["NEON_DATABASE_URL", "PODZONE_QDRANT_APIKEY", "STACK_PROJECT_ID"],
    "academy-web-podzone": ["NEON_DATABASE_URL", "PODZONE_QDRANT_APIKEY", "STACK_PROJECT_ID"],
    "academy-frontend": ["NEON_DATABASE_URL", "PODZONE_QDRANT_APIKEY", "STACK_PROJECT_ID"],
    "academy-frontend-vibe": ["NEON_DATABASE_URL", "PODZONE_QDRANT_APIKEY", "STACK_PROJECT_ID"],
    "academy-frontend-qa": ["NEON_DATABASE_URL", "PODZONE_QDRANT_APIKEY", "STACK_PROJECT_ID"],
    "academy-api": ["NEON_DATABASE_URL", "PODZONE_QDRANT_APIKEY", "STACK_PROJECT_ID"],
    "academy-api-qa": ["NEON_DATABASE_URL", "PODZONE_QDRANT_APIKEY", "STACK_PROJECT_ID"],
}


def _api_creds() -> tuple[str, str]:
    token = os.environ.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CLOUDFLARE_PODZONE_TOKEN")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or os.environ.get("CLOUDFLARE_PODZONE_ACCOUNT")
    if not token or not account:
        sys.exit(
            "need CLOUDFLARE_API_TOKEN/CLOUDFLARE_ACCOUNT_ID (or this fleet's "
            "cloudflare-podzone-token/cloudflare-podzone-account vault keys) in env"
        )
    return token, account


def _get_env_vars(token: str, account: str, project: str) -> dict:
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects/{project}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    if not data.get("success"):
        return {"_error": str(data.get("errors"))}
    return data["result"]["deployment_configs"]["production"].get("env_vars") or {}


def main() -> int:
    token, account = _api_creds()
    any_missing = False
    for project, required in REQUIRED_VARS.items():
        env_vars = _get_env_vars(token, account, project)
        if "_error" in env_vars:
            print(f"{project}: ERROR — {env_vars['_error']}")
            any_missing = True
            continue
        missing = [k for k in required if k not in env_vars]
        if missing:
            any_missing = True
            print(f"{project}: MISSING {', '.join(missing)}")
        else:
            print(f"{project}: OK ({', '.join(required)} all present)")
    return 1 if any_missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
