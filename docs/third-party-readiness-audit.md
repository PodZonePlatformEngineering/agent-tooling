# Third-party readiness audit

**Status:** v1 inventory + disposition proposal (PROJ-032/T-016, D3)
**Date:** 2026-05-27
**Author:** Hephaestus

## Purpose

Inventory every podzone-internal reference in the `agent-tooling` repo that
an external adopter would have to puzzle over, with a proposed disposition
(**keep**, **abstract**, **remove**) for each occurrence — grouped by category.

Per the brief, **inline rewrites of category-spanning patterns are deferred**
to a Hermes follow-up; this round applies only the strictly-isolated obvious
abstractions (see §6). Wholesale changes — especially anything that would
flip a default URL or rename an env var — would silently break current
podzone use and need operator counter-sign.

Counts are matches found by:

```
grep -rIn -E "(PROJ-[0-9]+|T-[0-9]+|CC-[0-9]+|podzoneAgentTeam|agenticflows\.co\.uk|agentsonly|qdrant\.agenticflows|ollama\.agenticflows|freyr|/Users/martincolley|hephaestus|hermes|thoth|atlas)" \
  --include="*.sh" --include="*.py" --include="*.md" --include="*.yaml" \
  --include="*.json" --include="*.template"
```

Total occurrences: **~435** (excluding `tests/proj038/fixtures/` design-doc
fixtures which are intentionally synthetic sample content).

---

## 1. Tasking identifiers (PROJ-XXX / T-YYY / CC-NNN)

**Where:** docstrings of tools, comments in install/scaffold scripts, test
docstrings, schema doc provenance lines.

| Occurrence pattern | Files (representative) | Disposition |
|---|---|---|
| `# Reference: planning/projects/PROJ-XXX-…` in script headers | `scaffold.sh:3`, `sync-agent-tooling.sh:3`, `install.sh:3,5` | **abstract** — replace with one-line "originated in PodZone's internal home-repo programme" or drop. Provenance not useful externally. |
| `PROJ-XXX/T-YYY` in tool docstrings (purpose lines) | `tools/efficiency-report.py:4`, `tools/usage-report.py:6-7,14`, `tools/rollup-report.py:4-6`, `tools/decay-detector.py:2,6,46` | **abstract** — keep the purpose sentence, drop the ticket reference. Optionally retain one line: "Originally specified under PROJ-XXX (PodZone-internal)" so internal git-blame search still resolves. |
| `T-NNN` in test bodies and stub-status comments | `tests/test_primitives.sh:164`, `tests/README.md:20`, `hooks/task-event.sh:6`, `hooks/stop-heartbeat.py:5` | **keep** — these are test fixture strings or internal stub markers; cost of churn exceeds benefit. Add a one-line note in `tests/README.md` that some test strings reference internal task IDs. |
| `PROJ-XXX` headings in `docs/*.md` provenance sections | `docs/sessions-schema.md`, `docs/work-items-schema.md`, `docs/sessions-schema.md` | **keep with disclaimer** — these docs describe the schema's history; external readers benefit from the provenance trail. Add a top-of-doc note: "Some sections reference internal PodZone task IDs (PROJ-XXX); these are historical pointers, not external dependencies." |

**Out of scope:** rewriting git commit messages (explicit brief exclusion).

---

## 2. PodZone-internal infrastructure references

### 2a. Qdrant URL default — `http://qdrant.agenticflows.co.uk:8080`

**Where:** ~15 occurrences across `primitives/`, `tools/`, `tests/` —
typically as `${AGENTSONLY_QDRANT_URL:-http://qdrant.agenticflows.co.uk:8080}`.

| File | Disposition |
|---|---|
| `primitives/qdrant/*.sh`, `primitives/getSecret.sh` | **abstract** — change default to be empty so the script errors with a clear "set AGENTSONLY_QDRANT_URL" message instead of silently calling a host the user can't reach. **Deferred to Hermes review** because it changes runtime behaviour for current PodZone use (any caller that relied on the default would now fail). |
| `tools/*.sh`, `tools/*.py`, `tools/create-secrets-collection.sh`, `tools/load-secrets.sh` | Same as above — abstract, defer. |
| `tests/**/*.sh` | **keep** — tests legitimately default to the PodZone fixture environment; `tests/fixtures/README.md` documents this. Add an explicit "to point tests at your own Qdrant, set `AGENTSONLY_QDRANT_URL`" line in `tests/README.md`. |
| `tools/one-shots/record_one_shot.py:33` (hardcoded, no env var) | **abstract** inline — this is a one-shot script, low blast radius. Apply this round. |
| `tests/fixtures/README.md` | **keep** — it explicitly documents the PodZone fixture environment; that's the file's job. |

### 2b. Env-var names — `PODZONE_*`, `AGENTSONLY_*`

**Where:** all primitives, tools, hooks (~20+ scripts), README, fixture docs.

**Disposition: keep, document.** Renaming the env vars would break every
existing PodZone install and every consumer script. The refreshed README §
"What's `PODZONE_…` and `AGENTSONLY_…` about?" already calls this out; that
is the right level of disclosure for v1. Future task candidate: introduce
neutral names (`QDRANT_API_KEY`, `QDRANT_URL`) as primary, keep the
`PODZONE_…` / `AGENTSONLY_…` names as fallbacks for compatibility.

### 2c. `agentsonly` / `freyr` / `minio-agentsonly` references

**Where:** `tools/dump-secrets.md`, `tools/create-secrets-collection.sh:2`,
`tools/load-secrets.sh`, `tests/secrets/*`, `primitives/getSecret.sh`.

These describe the PodZone secrets-on-Qdrant pattern. An external adopter
likely doesn't have a `minio-agentsonly` secret stored anywhere — the docs
are unactionable for them.

| File | Disposition |
|---|---|
| `tools/dump-secrets.md` examples that name `minio-agentsonly` | **abstract** — replace with generic `<service>` placeholder + an example. Apply this round (single doc file). |
| `tools/create-secrets-collection.sh:2` comment "on agentsonly Qdrant" | **abstract** — drop "agentsonly" qualifier; the script targets whatever `AGENTSONLY_QDRANT_URL` points at. Apply this round. |
| `tests/secrets/*` test strings | **keep** — fixture-environment data. |

---

## 3. PodZone-internal repo / path references

### 3a. `podzoneAgentTeam/` hardcoded as a path

**Where:**
- `tools/efficiency-report.py:41` — `Path.home() / "workspace" / "podzoneAgentTeam"` (output destination resolution)
- `tools/rollup-report.py:40,50` — same pattern
- `tools/usage-report.py:39` — same pattern, writes report to `~/workspace/podzoneAgentTeam/team/hermes/outgoing/...`
- `tools/usage-report.py:10` — docstring example pointing at the same path

**Disposition: abstract — defer to Hermes.** These three tools currently
write into the PodZone-internal `podzoneAgentTeam` repo by hardcoded path.
For external use they should accept an `--out-dir` argument (or fall back
to a configurable env var). Changing the default would break the existing
`/usage-report` and rollup workflows that depend on the path; this is a
contract change that wants operator sign-off.

### 3b. `team/{agent}/{outgoing,incoming,memory}` path patterns

**Where:** skill docs (`skills/*/SKILL.md`), schema docs, `collections/work_items.yaml:125`.

**Disposition: keep — they describe the convention** the home-repo template
imposes. External adopters who run `scaffold.sh` get the same layout, so
referencing it is correct. Add a one-line clarification near the top of
each skill where the path first appears: "this assumes the standard home-
repo layout created by `scaffold.sh`."

### 3c. Cross-repo strings in `scaffold.sh:271`

> "Cross-team work: raise draft in podzoneAgentTeam/briefs/{recipient}/ —
> do not write to other agents' home repos"

**Disposition: abstract — defer.** This string is written into the
generated home repo's READMEFIRST. For external use it should reference
the user's team-coordination repo, not `podzoneAgentTeam`. Needs a
templating decision (env var? scaffold arg?) — surface to Hermes.

---

## 4. Hard-coded agent identity / workspace paths in tests + fixtures

### 4a. Agent names (`hephaestus`, `hermes`, `thoth`, `atlas`)

**Where:** 8+ files, ~35 occurrences. Test fixtures, skill examples, schema
example payloads, one-shot script docstrings.

| Category | Files | Disposition |
|---|---|---|
| Test sample data (point names, payload examples) | `tests/proj03[48]/*`, `tests/test_primitives.sh` | **keep** — they read as fake-sounding agent names to an outsider, and changing them would churn the test suite for no functional gain. |
| Skill example output | `skills/session-start/SKILL.md`, `skills/session-end/SKILL.md` | **keep** — they're illustrative; the surrounding prose makes clear these are example agents. |
| Hardcoded `team/hermes/outgoing/...` output paths in tools | `tools/usage-report.py:10,39` | Covered by §3a — abstract via `--out-dir`. |
| `tools/one-shots/record_one_shot.py` example using `team/thoth/...` | `record_one_shot.py:16` | **keep** as docstring example. |

### 4b. `/Users/martincolley` workstation paths

**Where:** `tests/proj034/test_*.py` and `docs/sessions-schema.md:209,262`.

**Disposition: keep.** These are test fixture data strings (asserting
parser behaviour against a known input) and one schema example block. They
are never executed against a real path and never written. Apply a one-line
note in `tests/README.md` that some tests use the maintainer's workstation
path as fixture input.

---

## 5. Summary by disposition

| Disposition | Count (approx) | Action |
|---|---|---|
| **Keep** (no change, possibly add disclaimer) | ~300 | Disclaimers added in README + tests/README in this session. |
| **Abstract — apply inline this round** | ~10 | See §6. |
| **Abstract — defer to Hermes review** | ~50 | Listed below for the Hermes follow-up task. |
| **Remove** | 0 | None identified — every reference at least serves provenance. |

### Items deferred to Hermes (operator counter-sign needed)

1. **Qdrant URL default** in primitives/tools (§2a) — flipping the default
   from `http://qdrant.agenticflows.co.uk:8080` to empty (error-on-missing)
   would break current PodZone use until env vars are set.
2. **Env-var rename** `PODZONE_QDRANT_APIKEY` → `QDRANT_API_KEY` (§2b) —
   needs dual-name support window + downstream callers updated.
3. **Tools that write into `~/workspace/podzoneAgentTeam/...`** (§3a) —
   add `--out-dir` arg; pick a sensible fallback.
4. **`scaffold.sh:271` cross-team brief string** (§3c) — needs a
   templating story for "where does this team's cross-team coordination
   live?".

These are recorded in the session outbox as a follow-up note for the next
`/consolidate-tasks`.

---

## 6. Inline changes applied this round

Strictly isolated abstractions applied in the same PR as this audit:

- `tools/dump-secrets.md` — `minio-agentsonly` examples replaced with
  generic `<service>` placeholder examples.
- `tools/create-secrets-collection.sh:2` — drop "on agentsonly Qdrant"
  qualifier from the leading comment.
- `tools/one-shots/record_one_shot.py:33` — hardcoded
  `http://qdrant.agenticflows.co.uk:8080` replaced with the
  `${AGENTSONLY_QDRANT_URL:-…}` resolution pattern matching the rest of
  the tools.
- `tests/README.md` — add the "some tests use PodZone fixture
  environment and workstation paths" disclaimer.

Each is single-file, single-line, and does not change runtime behaviour
for any current consumer.
