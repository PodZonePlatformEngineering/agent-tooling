# Bootstrapping the secrets collection

`load-secrets.sh` is a pure Qdrant writer — it has no vault access. The Claude Code
agent must enumerate the vault using secretctl MCP tools and pipe the result in.

## Workflow (run inside a Claude Code session)

1. Call `mcp__secrets__secret_list` → returns list of secret names
2. For each name, call `mcp__secrets__secret_get_field` to retrieve the value
3. Assemble a JSON array:
   ```json
   [{"name": "podzone_qdrant_apikey", "value": "..."},
    {"name": "podzone_cloud_bot_token", "value": "..."}]
   ```
4. Write to `/tmp/secrets-dump.json` (gitignored)
5. Run:
   ```bash
   bash tools/load-secrets.sh --secrets-file /tmp/secrets-dump.json
   ```
6. Delete `/tmp/secrets-dump.json` after completion

## Dry-run first

```bash
bash tools/load-secrets.sh --secrets-file /tmp/secrets-dump.json --dry-run
```

## Stdin alternative

```bash
echo '[{"name":"foo","value":"bar"}]' | bash tools/load-secrets.sh --dry-run
```

## Auth required

`PODZONE_QDRANT_APIKEY` must be set (Qdrant writes only). No vault credentials needed.
