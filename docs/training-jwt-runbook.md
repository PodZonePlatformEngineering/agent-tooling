# Trainee credential runbook — mint / rotate / revoke

**Status:** v1 (PROJ-011/T-031, CC-416).
**Tool:** `tools/training-jwt.py` (training-team only — needs the master
cluster key `PODZONE_QDRANT_APIKEY`; the master key never ships anywhere).
**Registry:** `training_token_registry` on cloud Qdrant
(`docs/training-collections-schema.md`).
**Design record:** trainee-repo-takeon-flow.md R2-2.

---

## ⚠️ Live-verified tier finding (2026-07-12) — read this first

The designed R2-2 credential is a **self-signed JWT** (HMAC-signed against
the cluster API key) carrying per-collection rw claims for the two training
collections, an expiry, and a `value_exists` revocation claim. **Our Qdrant
Cloud cluster does not honour self-signed JWTs.** Verified live against the
cluster on 2026-07-12 (`tools/training-jwt.py probe` re-runs the check):

| claim set tried | result |
|---|---|
| bare `exp`-only JWT | HTTP 403 (identical to a bogus key) |
| granular `access` per-collection claims | HTTP 403 |
| full set incl. `value_exists` | HTTP 403 |

Root cause: on this tier the cluster accepts only **control-plane-minted
tokens**. Our own "API key" is itself a Cloud-minted JWT (HS256, claims
`access: "m"` + `subject`) signed by a secret the Cloud control plane holds
and does not expose — so there is nothing for us to sign against;
`value_exists` and self-signed granular claims are unreachable regardless of
claim syntax. No Cloud Management API key is provisioned in our environment
(and the Cloud database-API-key surface does not expose `value_exists`
anyway).

### What IS supported on our tier, and the recommended fallback

**Qdrant Cloud "Database API Keys" with granular access** — minted in the
Cloud console (or the Cloud Management API, if we later provision a
management key). These ARE collection-scopeable JWTs, signed by the control
plane. Recommended per-trainee credential until/unless `jwt_rbac` becomes
available:

1. **Scope:** rw on `training_briefs` + `training_session_telemetry` only —
   the R2-2 isolation property is fully preserved.
2. **Short expiry + rotation** (the brief's designated fallback): mint with
   the shortest workable expiry (suggest ≤ 90 days for the programme,
   shorter if console-supported), rotate on a calendar cadence via the
   operational brief.
3. **Revocation:** delete the database API key in the Cloud console —
   control-plane revocation, immediate. (Operationally equivalent to the
   `value_exists` point-delete, just console-side.)
4. **Bookkeeping:** `register` every issued key in the registry (stores a
   sha256/16 fingerprint, never the credential) so `list`/`rotate`/`revoke`
   still drive the operational flow from one place.

The blast radius of a leaked trainee credential remains the two training
collections (R2-2 accepted risk), same as the designed JWT.

### Self-upgrade path

`probe` exits 0 the day the cluster honours self-signed JWTs (e.g. tier
change, hybrid/private deployment, or Cloud exposing `jwt_rbac`). From that
day `mint` is fully operational with the designed claim set — including
`value_exists` — with no code change; switch new issuance to `mint` and let
console keys age out at rotation.

---

## Operations

All commands need `PODZONE_QDRANT_APIKEY` in env (harness env block, or wrap
in `mcp__secrets__secret_run -k podzone_qdrant_apikey`). Tokens are passed
via `$TRAINING_TOKEN` or `--token-file`, never argv.

### Check what the cluster supports

```bash
python3 tools/training-jwt.py probe
```

### Issue a credential (current fallback flow)

1. Cloud console → cluster → Database API Keys → create key: rw on
   `training_briefs` + `training_session_telemetry` only, expiry set.
2. Register it (fingerprint + expiry bookkeeping) and live-verify the scope:

```bash
python3 tools/training-jwt.py register --trainee norma \
    --token-file /path/to/key.txt --expires 2026-10-12
```

`register` live-verifies: reads on both training collections must succeed,
the fleet canary (`briefs`) must be denied. Exit 2 = mis-scoped — do not ship.

3. Ship the credential in the trainee repo config (T-030) — accepted-risk
   committed credential per R2-2 (private repo; scope + expiry + revocation
   are the safety properties).

### Issue a credential (designed flow — once probe exits 0)

```bash
python3 tools/training-jwt.py mint --trainee norma --days 30
```

Creates the registry point FIRST (a `value_exists` token must never exist
without its revocation point), prints the JWT (stdout — the shippable
credential), live-verifies scope. Exit 0 = honoured + scoped; exit 2 = the
cluster ignored the claims (tier finding above) — fall back to `register`.

### Rotate

```bash
python3 tools/training-jwt.py rotate --trainee norma --days 30
```

Mints/registers the new credential, then revokes the trainee's previous
actives. Deliver the new credential via an **operational-brief instruction**
(R2-1): the trainee's agent updates the repo config, the trainee approves
in-session, the agent acks with `ack_of_revision`.

### Revoke

```bash
python3 tools/training-jwt.py revoke --trainee norma        # all of a trainee's
python3 tools/training-jwt.py revoke --token-id <uuid>       # one credential
```

Deletes the registry point(s). For a `value_exists` token that IS the kill.
For a `cloud_key` credential **also delete the key in the Cloud console** —
the registry is bookkeeping, not enforcement, for those.

### Audit

```bash
python3 tools/training-jwt.py list [--trainee norma]
python3 tools/training-jwt.py verify --token-file key.txt   # decode + live scope check
```

---

## Tests

- Offline: `tests/proj011/test-training-collections.sh` (claim set, HS256
  signing, registry endpoint discipline, schema helpers).
- Live: `tests/proj011/test-training-jwt-live.sh` — mint→use→revoke
  round-trip against the cloud; key-gated; tolerates both tier outcomes
  (mint rc 0 honoured / rc 2 rejected) so it keeps passing across the
  self-upgrade.
