# Test Fixtures

Documents the shared test environments used by `agent-tooling` primitive tests.

---

## Qdrant

**Collection:** `agent-tooling-test`
**Dimensions:** 768 (nomic-embed-text cosine)
**URL:** `https://qdrant.agenticflows.co.uk`
**Auth env var:** `PODZONE_QDRANT_APIKEY`

The collection is pre-created and persistent. Tests add and clean up their own points
using UUIDs scoped to the test run. Do not drop or recreate the collection between runs.

> **Note (2026-05-13):** `qdrant.agenticflows.co.uk` TLS certificate has expired — the
> `agent-tooling-test` collection has not yet been created. Atlas to renew the cert;
> collection creation is pending. See current blockers in `planning/STATUS.md`.

To create or verify the collection:

```bash
curl -s -X PUT "https://qdrant.agenticflows.co.uk/collections/agent-tooling-test" \
  -H "api-key: $PODZONE_QDRANT_APIKEY" \
  -H "Content-Type: application/json" \
  -d '{"vectors": {"size": 768, "distance": "Cosine"}}'
```

---

## Telegram

**Bot:** `podzone_bot` / `@podzone_cloud_bot`
**Auth env var:** `PODZONE_CLOUD_BOT_TOKEN`
**Test chat_id:** confirm with Martin before running Telegram primitive tests.

Bot token is available via secretctl: `secretctl run -k podzone_cloud_bot_token -- env | grep PODZONE_CLOUD_BOT_TOKEN`
or injected directly from `~/.claude/settings.json` `env` block (preferred in hook context).

---

## Gmail

**Account:** `podzone.cloud@gmail.com`
**OAuth token file:** `~/.config/podzone/gmail-token.json`

Verify the token is current before running Gmail primitive tests:

```bash
python3 -c "
import json, time
t = json.load(open('$HOME/.config/podzone/gmail-token.json'))
exp = t.get('expiry') or t.get('token_expiry')
print('token expiry:', exp)
"
```

If expired, re-run the Gmail MCP OAuth flow or refresh via the podzone credentials doc.

---

## Notes

- Tests must not leave persistent state in shared environments (delete created points/drafts).
- Telegram test messages go to the shared `@podzone_cloud_bot` channel — keep them brief.
- Gmail test drafts must be deleted after each test run.
