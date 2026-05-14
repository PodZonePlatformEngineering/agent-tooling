#!/usr/bin/env python3
"""CLI driver for create-gmail-draft.sh. Wraps gmail_client.create_draft()."""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import gmail_client

p = argparse.ArgumentParser()
p.add_argument("--to", required=True)
p.add_argument("--subject", required=True)
p.add_argument("--body", required=True)
p.add_argument("--attachment", action="append", default=[])
args = p.parse_args()

attachments = [(pathlib.Path(a), pathlib.Path(a).name) for a in args.attachment]
draft_id = gmail_client.create_draft(args.to, args.subject, args.body, attachments or None)
print(f"draft_id={draft_id}")
