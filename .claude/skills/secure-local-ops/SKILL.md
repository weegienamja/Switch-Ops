---
name: secure-local-ops
description: Local credential handling, redaction, and keyring usage for SwitchOps.
---

# Secure local ops skill

## Credential storage priority

1. **OS keyring** via the Python `keyring` library. Service name: `switchops`. Account names: `switch_host`, `switch_username`, `switch_password`, `switch_enable_secret`, `switch_device_type`.
2. **Local file fallback** at `backend/data/credentials.json` if keyring raises `NoKeyringError`. File is git-ignored; UI shows a warning banner.
3. **Environment variables** (`SWITCH_*`) are accepted in dev only and never written to disk by the app.

## Redaction

`logging_config.py` installs a filter that replaces known secret values in any log record with `<redacted>`. The audit store applies the same filter before writing.

## Never

- Return password or enable_secret values through any API. Status endpoints expose booleans only (`hasPassword: true`).
- Log credential values, even on error paths.
- Print credentials to stdout/stderr from the sidecar.

## API surface

- `GET /api/setup/status` — `{ configured: bool, hasPassword: bool, hasEnableSecret: bool, storage: 'keyring' | 'file', mockMode: bool }`.
- `POST /api/setup/credentials` — accepts credentials; stores and returns status.
- `DELETE /api/setup/credentials` — clears all stored credentials.
