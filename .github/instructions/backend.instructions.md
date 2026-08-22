---
applyTo: "backend/**/*.py"
---

# Backend instructions

- Python 3.11+, FastAPI, Pydantic v2, Netmiko, Paramiko, keyring, SQLite, pytest.
- Every request/response uses a Pydantic model from `app/models.py`.
- Every executable Cisco command is registered in `app/command_registry.py` by symbolic name. No raw command strings are accepted from HTTP.
- Parsers in `app/parsers/` must be tolerant of IOS 12.2 output and never raise on unexpected lines — return partial data with a `raw` fallback when needed.
- `credential_store.py` is the only module that reads secrets. Other modules ask for a `SwitchCredentials` object.
- Audit every command via `audit_store.py` (SQLite + JSONL). Redact passwords/enable secrets.
- Mock mode (`SWITCH_MOCK_MODE=true`) reads from `app/sample_outputs/`; tests rely on this.
- Write actions live in `app/tools/safe_write.py` and only run if `settings.enable_write_actions` is true; protected interfaces (`Gi0/1`, `Gi0/2`, `Vlan1`) always raise.
- pytest coverage required for: allowlist accept/reject, parsers, redaction, backup naming, credential API, audit writes, mock-mode endpoints.
- Bind to `127.0.0.1` only. Never `0.0.0.0`.
- Never log credential values. Use `"<redacted>"`.
