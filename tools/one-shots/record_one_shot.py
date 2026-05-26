#!/usr/bin/env python3
"""
record_one_shot.py — record an agent workflow one-shot reflection into Qdrant.

Usage:
  python record_one_shot.py \
    --agent thoth \
    --moment session-start \
    --date 2026-04-26 \
    --task agent-workflow-qdrant-tooling \
    --programme platform-buildout \
    [--team-lead hermes] \
    [--linked-to hermes-2026-04-26-session-start]

  # Batch mode (read from YAML file instead of prompting):
  python record_one_shot.py --from-file team/thoth/outgoing/one-shots/thoth-2026-04-26-session-start.yaml
"""

import argparse
import json
import os
import sys
import uuid
import urllib.request
import urllib.error
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
QDRANT_URL = os.environ.get("AGENTSONLY_QDRANT_URL", "http://qdrant.agenticflows.co.uk:8080")
COLLECTION = "agent-workflow"
EMBED_MODEL = "nomic-embed-text"
TIMEOUT = 120

VALID_MOMENTS = {"session-start", "session-end", "consolidate-tasks", "brief", "post-session-review", "friction"}


def _json_dumps(data: dict) -> bytes:
    import datetime
    def default(o):
        if isinstance(o, (datetime.date, datetime.datetime)):
            return o.isoformat()
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")
    return json.dumps(data, default=default).encode()


def http_put(url, data):
    body = _json_dumps(data)
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="PUT")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def http_post(url, data):
    body = _json_dumps(data)
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def embed(text):
    resp = http_post(f"{OLLAMA_URL}/api/embeddings", {"model": EMBED_MODEL, "prompt": text})
    return resp["embedding"]


def deterministic_id(one_shot_id: str) -> int:
    return uuid.uuid5(uuid.NAMESPACE_DNS, one_shot_id).int >> 64


def read_thinking_text_from_stdin() -> str:
    print("Enter thinking_text (end with Ctrl-D on a blank line, or a line containing only '---'):")
    lines = []
    try:
        while True:
            line = input()
            if line.strip() == "---":
                break
            lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines).strip()


def load_yaml_file(path: Path) -> dict:
    if yaml is None:
        # Minimal YAML loader for the simple flat schema used in one-shots
        data = {}
        key = None
        text_lines = []
        in_multiline = False
        with open(path) as f:
            for raw in f:
                line = raw.rstrip("\n")
                if in_multiline:
                    if line.startswith("  ") or line == "":
                        text_lines.append(line[2:] if line.startswith("  ") else "")
                    else:
                        data[key] = "\n".join(text_lines).strip()
                        in_multiline = False
                        text_lines = []
                if not in_multiline and ":" in line and not line.startswith(" "):
                    k, _, v = line.partition(":")
                    k = k.strip()
                    v = v.strip()
                    if v == "|":
                        key = k
                        in_multiline = True
                        text_lines = []
                    elif v.lower() == "null":
                        data[k] = None
                    else:
                        data[k] = v
            if in_multiline:
                data[key] = "\n".join(text_lines).strip()
        return data
    else:
        with open(path) as f:
            return yaml.safe_load(f)


def build_payload(args_or_data: dict) -> dict:
    return {
        "one_shot_id": args_or_data["one_shot_id"],
        "agent": args_or_data["agent"],
        "team_lead": args_or_data.get("team_lead"),
        "moment": args_or_data["moment"],
        "session_date": args_or_data["session_date"],
        "linked_agent_id": args_or_data.get("linked_agent_id"),
        "linked_brief_id": args_or_data.get("linked_brief_id"),
        "task_slug": args_or_data["task_slug"],
        "programme": args_or_data["programme"],
        "thinking_text": args_or_data["thinking_text"],
    }


def upsert(payload: dict):
    vector = embed(payload["thinking_text"])
    point_id = deterministic_id(payload["one_shot_id"])
    points_payload = {
        "points": [
            {
                "id": point_id,
                "vector": vector,
                "payload": payload,
            }
        ]
    }
    result = http_put(f"{QDRANT_URL}/collections/{COLLECTION}/points", points_payload)
    return point_id, result


def process_one(data: dict):
    payload = build_payload(data)
    if not payload["thinking_text"]:
        print(f"  ⚠️  Skipping {payload['one_shot_id']} — empty thinking_text")
        return
    print(f"  Embedding {payload['one_shot_id']} …", end=" ", flush=True)
    point_id, result = upsert(payload)
    status = result.get("status", "unknown")
    print(f"{status} (point_id={point_id})")
    print(f"  ✅  one_shot_id: {payload['one_shot_id']}")


def main():
    parser = argparse.ArgumentParser(description="Record an agent workflow one-shot into Qdrant.")
    parser.add_argument("--from-file", metavar="PATH", help="Load one-shot from a YAML file instead of prompting")
    parser.add_argument("--agent", help="Agent name (e.g. thoth)")
    parser.add_argument("--moment", choices=list(VALID_MOMENTS), help="Moment type")
    parser.add_argument("--date", dest="session_date", help="Session date YYYY-MM-DD")
    parser.add_argument("--task", dest="task_slug", help="Task slug")
    parser.add_argument("--programme", help="Programme shortform")
    parser.add_argument("--team-lead", dest="team_lead", default=None)
    parser.add_argument("--linked-to", dest="linked_agent_id", default=None)
    parser.add_argument("--batch-dir", metavar="DIR", help="Ingest all *.yaml files in a directory tree")
    args = parser.parse_args()

    if args.batch_dir:
        root = Path(args.batch_dir)
        files = sorted(root.rglob("*.yaml"))
        if not files:
            print(f"No YAML files found under {root}")
            sys.exit(0)
        print(f"Batch ingesting {len(files)} file(s) from {root}")
        for f in files:
            print(f"\n→ {f}")
            try:
                data = load_yaml_file(f)
                process_one(data)
            except Exception as e:
                print(f"  ❌  Error: {e}")
        return

    if args.from_file:
        path = Path(args.from_file)
        if not path.exists():
            print(f"File not found: {path}")
            sys.exit(1)
        data = load_yaml_file(path)
        process_one(data)
        return

    # Interactive mode
    required = ["agent", "moment", "session_date", "task_slug", "programme"]
    missing = [f for f in required if not getattr(args, f, None)]
    if missing:
        parser.error(f"Missing required arguments: {', '.join('--' + f.replace('_', '-') for f in missing)}")

    one_shot_id = f"{args.agent}-{args.session_date}-{args.moment}"
    thinking_text = read_thinking_text_from_stdin()

    data = {
        "one_shot_id": one_shot_id,
        "agent": args.agent,
        "team_lead": args.team_lead,
        "moment": args.moment,
        "session_date": args.session_date,
        "linked_agent_id": args.linked_agent_id,
        "linked_brief_id": None,
        "task_slug": args.task_slug,
        "programme": args.programme,
        "thinking_text": thinking_text,
    }

    process_one(data)


if __name__ == "__main__":
    main()
