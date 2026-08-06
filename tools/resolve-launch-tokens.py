#!/usr/bin/env python3
"""resolve-launch-tokens.py — PROJ-039/T-210 fix.

`secretctl run`'s raw CLI has no non-interactive auth path (only
`secretctl mcp-server` reads `SECRETCTL_PASSWORD`), so `launch.sh` cannot
call it directly from a detached/backgrounded script. This tool moves
resolution to the one place secrets ARE reachable non-interactively today:
the Team Lead's own `mcp__secrets__secret_run` MCP tool.

Run this BEFORE any `launch.sh` dispatch (or a batch of them), via the
secrets MCP so the actual token values never appear in any tool output or
transcript — each is read from this process's own env (injected by
`secret_run`) and written straight to the output file, never printed:

    mcp__secrets__secret_run(
        keys=["claude-oath-token-colleym", "claude-oath-token-martinjcolley",
              "claude-oath-token-podzone", "claude-oath-token-norma"],
        command="python3",
        args=["tools/resolve-launch-tokens.py",
              "--template", "tools/launch-tokens.template.json",
              "--out", "~/.claude/launch-tokens.resolved.json"],
    )

`launch.sh` then reads the resolved file **by index**, not by re-deriving a
secretctl key name — a key rotation, whether re-authenticating an expired
token or renaming a subscription slot, only ever concerns operators editing
the template + re-running this resolver, never a `launch.sh` code change.
The resolved file is reusable across many `launch.sh` invocations while the
underlying tokens stay valid — no need to re-resolve per dispatch, only when
a token has actually expired or rotated.

The output file is written `0600` and MUST NOT be committed — it carries
real subscription auth tokens in plaintext (the same secret-handling
posture already accepted for `CLAUDE_CODE_OAUTH_TOKEN` env-var injection
elsewhere in this fleet's launch tooling).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


def secretctl_env_name(secret_key: str) -> str:
    """Mirrors secretctl's own env-naming rule: '/' and '-' -> '_', uppercase."""
    return re.sub(r"[/-]", "_", secret_key).upper()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--template", required=True, help="launch-tokens.template.json path")
    ap.add_argument("--out", required=True, help="resolved output path (0600, never committed)")
    args = ap.parse_args()

    template_path = Path(args.template).expanduser()
    out_path = Path(args.out).expanduser()

    entries = json.loads(template_path.read_text())
    resolved = []
    missing = []
    for entry in entries:
        env_name = secretctl_env_name(entry["secret"])
        value = os.environ.get(env_name)
        if not value:
            missing.append(f"{entry['name']} ({env_name})")
            continue
        resolved.append({"name": entry["name"], "token": value})

    if missing:
        sys.exit(
            "resolve-launch-tokens: missing env value(s) for: " + ", ".join(missing) +
            " — run this via secret_run with all template secrets listed as --keys."
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(resolved))
    out_path.chmod(0o600)
    # Never print token values — only confirm counts and names.
    print(f"resolved {len(resolved)} token(s) -> {out_path} (0600): " +
          ", ".join(e["name"] for e in resolved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
