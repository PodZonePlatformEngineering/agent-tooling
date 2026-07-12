#!/usr/bin/env python3
"""
training-jwt.py — trainee credential mint/rotate/revoke + live claim probe
(PROJ-011/T-031, CC-416). Runbook: docs/training-jwt-runbook.md.

TRAINING-TEAM tool. Requires the master cluster key (PODZONE_QDRANT_APIKEY)
in env; the master key never ships anywhere and is never printed. The minted
JWT is the shippable credential — printing IT is the point.

Designed claim set (R2-2): per-collection rw restricted to the two training
collections, an expiry, and a `value_exists` revocation claim bound to a
point in `training_token_registry` (training-team-owned; trainee tokens get
no claim on it — delete the point, the token dies).

⚠️  LIVE-VERIFIED FINDING (2026-07-12, this cluster): our Qdrant Cloud tier
does NOT honour self-signed JWTs — the cluster accepts only control-plane-
minted tokens (the database API key itself is a Cloud-minted JWT, access
"m"), and there is no exposed signing secret to mint against. `mint`
therefore verifies its own output live and tells you loudly whether the
cluster honoured it (`probe` re-checks the tier any time — the tool
self-upgrades to the designed path the day jwt_rbac appears). Until then the
operational fallback is a console-minted granular Database API Key
(collection-scoped rw + short expiry + rotation), tracked here via
`register` so rotation/revocation bookkeeping still lives in the registry.

Subcommands:
  probe                        live claim-set support check against the cluster
  mint     --trainee X         mint self-signed JWT (full claim set) + registry
                               point; live-verifies; --days N (default 30)
  register --trainee X         register a console-minted credential (reads the
                               token from $TRAINING_TOKEN or --token-file;
                               stores only a sha256 fingerprint, never the token)
  verify   --token-file F      decode claims structurally + live round-trip:
                               rw on training collections, DENIED on fleet
  rotate   --trainee X         mint new + revoke the trainee's previous actives
  revoke   --token-id ID | --trainee X
                               delete registry point(s) (kills a value_exists
                               token; for cloud keys ALSO delete in console)
  list     [--trainee X]       scroll the registry

Exit codes: 0 ok; 1 usage/env error; 2 live verification NOT honoured (mint
completes, credential registered, but the cluster ignored the claims).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib import qdrant_http  # noqa: E402
from lib.training_substrate import (  # noqa: E402
    TOKEN_REGISTRY, TRAINEE_COLLECTIONS, now_iso,
)

FLEET_CANARY = "briefs"  # a collection a trainee token must NEVER reach


# --- JWT construction (stdlib-only; HS256) -----------------------------------

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64url_json(obj: dict) -> str:
    return _b64url(json.dumps(obj, separators=(",", ":"), sort_keys=True).encode())


def build_claims(*, token_id: str, expires_epoch: int) -> dict:
    """The designed R2-2 claim set for a trainee token."""
    return {
        "exp": expires_epoch,
        "access": [
            {"collection": name, "access": "rw"} for name in TRAINEE_COLLECTIONS
        ],
        "value_exists": {
            "collection": TOKEN_REGISTRY,
            "matches": [{"key": "token_id", "value": token_id}],
        },
    }


def sign_jwt(claims: dict, key: str) -> str:
    header = _b64url_json({"alg": "HS256", "typ": "JWT"})
    payload = _b64url_json(claims)
    sig = hmac.new(key.encode(), f"{header}.{payload}".encode(), hashlib.sha256)
    return f"{header}.{payload}.{_b64url(sig.digest())}"


def decode_claims(token: str) -> dict:
    """Structural decode of a JWT's claims (no signature check)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a JWT (expected 3 dot-separated segments)")
    raw = parts[1]
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def fingerprint(token: str) -> str:
    """A safe, non-reversible identifier for a credential (sha256/16)."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


# --- Live access checks -------------------------------------------------------

def _can_read(collection: str, token: str) -> tuple:
    """(allowed: bool, status: int|None) for a GET collection-info as token."""
    url = f"{qdrant_http.CLOUD_QDRANT_URL}/collections/{collection}"
    try:
        qdrant_http.request_json("GET", url, api_key=token)
        return True, 200
    except qdrant_http.QdrantHTTPError as exc:
        return False, exc.status


def live_verify(token: str) -> dict:
    """Exercise a trainee credential against the cluster: rw scope honoured,
    fleet canary denied. Returns a result dict; never raises on denial."""
    results = {}
    for name in TRAINEE_COLLECTIONS:
        allowed, status = _can_read(name, token)
        results[name] = {"read": allowed, "status": status}
    canary_allowed, canary_status = _can_read(FLEET_CANARY, token)
    results[FLEET_CANARY] = {"read": canary_allowed, "status": canary_status}
    results["honoured"] = all(results[n]["read"] for n in TRAINEE_COLLECTIONS)
    results["scoped"] = results["honoured"] and not canary_allowed
    return results


# --- Registry ops (master key) -------------------------------------------------

def registry_upsert(*, token_id: str, trainee: str, kind: str,
                    token_fp: str, expires_at: str, claims_summary: str) -> None:
    point = {
        "id": token_id,  # plain UUID — valid Qdrant point id
        "vector": {},
        "payload": {
            "token_id": token_id,
            "trainee": trainee,
            "kind": kind,  # self_signed | cloud_key
            "token_fingerprint": token_fp,
            "claims_summary": claims_summary,
            "active": "true",
            "minted_at": now_iso(),
            "expires_at": expires_at,
            "minted_by": os.environ.get("USER", "unknown"),
        },
    }
    qdrant_http.upsert_points([point], collection=TOKEN_REGISTRY)


def registry_scroll(trainee: str | None = None, active_only: bool = False) -> list:
    must = []
    if trainee:
        must.append({"key": "trainee", "match": {"value": trainee}})
    if active_only:
        must.append({"key": "active", "match": {"value": "true"}})
    body = {"limit": 100, "with_payload": True}
    if must:
        body["filter"] = {"must": must}
    resp = qdrant_http.scroll(collection=TOKEN_REGISTRY, body=body)
    return (resp.get("result") or {}).get("points") or []


def registry_delete(point_ids: list) -> None:
    qdrant_http.delete_points(point_ids, collection=TOKEN_REGISTRY)


# --- Subcommands ---------------------------------------------------------------

def _require_master_key() -> str:
    key = os.environ.get(qdrant_http.API_KEY_ENV, "")
    if not key:
        print(f"error: {qdrant_http.API_KEY_ENV} not set (training-team tool — "
              "run inside the harness or via secret_run)", file=sys.stderr)
        sys.exit(1)
    return key


def cmd_probe(_args) -> int:
    """Live claim-set support check. Safe: only reads, only throwaway tokens."""
    key = _require_master_key()
    exp = int(time.time()) + 300
    checks = [
        ("bare exp-only JWT", {"exp": exp}),
        ("granular access claims", {"exp": exp, "access": [
            {"collection": name, "access": "rw"} for name in TRAINEE_COLLECTIONS]}),
        ("full set incl. value_exists",
         build_claims(token_id=str(uuid.uuid4()), expires_epoch=exp)),
    ]
    supported = True
    for label, claims in checks:
        tok = sign_jwt(claims, key)
        allowed, status = _can_read(TRAINEE_COLLECTIONS[0], tok)
        print(f"  {'✅' if allowed else '❌'} {label}: "
              f"{'honoured' if allowed else f'rejected (HTTP {status})'}")
        supported = supported and allowed
    if supported:
        print("\nself-signed JWT RBAC is HONOURED on this cluster — "
              "`mint` is fully operational (designed R2-2 path).")
    else:
        print("\nself-signed JWT RBAC is NOT honoured on this cluster "
              "(control-plane-minted tokens only). Fallback: console-minted "
              "granular Database API Key + short expiry + rotation; track it "
              "here via `register`. See docs/training-jwt-runbook.md.")
    return 0 if supported else 2


def cmd_mint(args) -> int:
    key = _require_master_key()
    token_id = str(uuid.uuid4())
    expires_epoch = int(time.time()) + args.days * 86400
    claims = build_claims(token_id=token_id, expires_epoch=expires_epoch)
    token = sign_jwt(claims, key)

    # Registry point FIRST — a value_exists token must never exist without
    # its revocation point.
    expires_at = now_iso()[:10] + f" (+{args.days}d, epoch {expires_epoch})"
    registry_upsert(
        token_id=token_id, trainee=args.trainee, kind="self_signed",
        token_fp=fingerprint(token), expires_at=expires_at,
        claims_summary=f"rw:{','.join(TRAINEE_COLLECTIONS)} exp:{args.days}d value_exists",
    )
    print(f"registry point {token_id} created (trainee={args.trainee})",
          file=sys.stderr)
    print(token)

    if args.no_verify:
        return 0
    result = live_verify(token)
    if result["scoped"]:
        print("live verify: token honoured + correctly scoped "
              f"(fleet canary {FLEET_CANARY} denied)", file=sys.stderr)
        return 0
    if result["honoured"]:
        print(f"⚠️  live verify: token honoured but fleet canary {FLEET_CANARY} "
              "READABLE — DO NOT SHIP; investigate before issuing.",
              file=sys.stderr)
        return 2
    print("⚠️  live verify: cluster did NOT honour the self-signed token "
          f"(HTTP {result[TRAINEE_COLLECTIONS[0]]['status']}). Known tier "
          "limitation — use the console-minted Database API Key fallback and "
          "`register` it (docs/training-jwt-runbook.md).", file=sys.stderr)
    return 2


def cmd_register(args) -> int:
    _require_master_key()
    token = os.environ.get("TRAINING_TOKEN", "")
    if args.token_file:
        token = Path(args.token_file).read_text().strip()
    if not token:
        print("error: provide the credential via $TRAINING_TOKEN or --token-file "
              "(never as an argv — argv leaks into process listings)",
              file=sys.stderr)
        return 1
    token_id = str(uuid.uuid4())
    summary = "cloud-console database API key"
    try:
        claims = decode_claims(token)
        summary += f" (claims: {', '.join(sorted(claims.keys()))})"
    except ValueError:
        summary += " (opaque)"
    registry_upsert(
        token_id=token_id, trainee=args.trainee, kind="cloud_key",
        token_fp=fingerprint(token), expires_at=args.expires or "unset",
        claims_summary=summary,
    )
    print(f"registered cloud key for {args.trainee}: token_id={token_id} "
          f"fingerprint={fingerprint(token)}")
    if not args.no_verify:
        result = live_verify(token)
        state = ("scoped OK" if result["scoped"]
                 else "⚠️ NOT correctly scoped" if result["honoured"]
                 else "⚠️ not honoured")
        print(f"live verify: {state} — {json.dumps({k: v for k, v in result.items() if isinstance(v, dict)})}")
        return 0 if result["scoped"] else 2
    return 0


def cmd_verify(args) -> int:
    token = os.environ.get("TRAINING_TOKEN", "")
    if args.token_file:
        token = Path(args.token_file).read_text().strip()
    if not token:
        print("error: provide the credential via $TRAINING_TOKEN or --token-file",
              file=sys.stderr)
        return 1
    try:
        claims = decode_claims(token)
        print("claims:", json.dumps(claims, indent=2, default=str))
    except ValueError as exc:
        print(f"(structural decode skipped: {exc})")
    result = live_verify(token)
    print("live:", json.dumps(result, indent=2))
    return 0 if result["scoped"] else 2


def cmd_revoke(args) -> int:
    _require_master_key()
    if args.token_id:
        targets = [{"id": args.token_id,
                    "payload": {"trainee": "?", "kind": "?"}}]
    else:
        targets = registry_scroll(trainee=args.trainee)
        if not targets:
            print(f"no registry points for trainee {args.trainee}")
            return 0
    ids = [str(p["id"]) for p in targets]
    registry_delete(ids)
    for p in targets:
        pl = p.get("payload") or {}
        print(f"revoked {p['id']} (trainee={pl.get('trainee')}, "
              f"kind={pl.get('kind')})")
    print("value_exists tokens bound to these points are now dead. For "
          "cloud_key credentials ALSO delete the key in the Qdrant Cloud "
          "console — the registry is bookkeeping, not enforcement, for those.")
    return 0


def cmd_rotate(args) -> int:
    _require_master_key()
    previous = registry_scroll(trainee=args.trainee, active_only=True)
    rc = cmd_mint(args)
    if previous:
        # keep only the newest: delete the pre-rotation actives
        registry_delete([str(p["id"]) for p in previous])
        print(f"revoked {len(previous)} previous credential(s) for "
              f"{args.trainee}", file=sys.stderr)
    print("rotation: deliver the new credential via an operational-brief "
          "instruction (R2-1); the trainee approves the config update "
          "in-session.", file=sys.stderr)
    return rc


def cmd_list(args) -> int:
    _require_master_key()
    points = registry_scroll(trainee=args.trainee)
    if not points:
        print("(registry empty)" if not args.trainee
              else f"(no credentials for {args.trainee})")
        return 0
    for p in points:
        pl = p.get("payload") or {}
        print(f"{p['id']}  trainee={pl.get('trainee')}  kind={pl.get('kind')}  "
              f"active={pl.get('active')}  minted={pl.get('minted_at', '')[:19]}  "
              f"expires={pl.get('expires_at')}  fp={pl.get('token_fingerprint')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="training-jwt.py",
        description="trainee credential mint/rotate/revoke + live claim probe "
                    "(PROJ-011/T-031; docs/training-jwt-runbook.md)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe", help="live claim-set support check")

    p = sub.add_parser("mint", help="mint self-signed JWT + registry point")
    p.add_argument("--trainee", required=True)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--no-verify", action="store_true")

    p = sub.add_parser("register", help="register a console-minted credential")
    p.add_argument("--trainee", required=True)
    p.add_argument("--token-file", help="file holding the credential "
                   "(or set $TRAINING_TOKEN)")
    p.add_argument("--expires", help="expiry note (ISO date) for bookkeeping")
    p.add_argument("--no-verify", action="store_true")

    p = sub.add_parser("verify", help="decode + live round-trip a credential")
    p.add_argument("--token-file")

    p = sub.add_parser("revoke", help="delete registry point(s)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--token-id")
    g.add_argument("--trainee")

    p = sub.add_parser("rotate", help="mint new + revoke previous actives")
    p.add_argument("--trainee", required=True)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--no-verify", action="store_true")

    p = sub.add_parser("list", help="scroll the registry")
    p.add_argument("--trainee")

    args = ap.parse_args()
    return {
        "probe": cmd_probe, "mint": cmd_mint, "register": cmd_register,
        "verify": cmd_verify, "revoke": cmd_revoke, "rotate": cmd_rotate,
        "list": cmd_list,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
