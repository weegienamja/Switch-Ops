---
applyTo: "**"
---

# Security instructions (cross-cutting)

- **Never commit secrets.** Use `__REPLACE_WITH_LOCAL_SECRET__` in all samples, prompts, docs, and tests. Real secrets live in OS keyring or a git-ignored local file.
- **No raw CLI endpoint.** Every Cisco command is allowlisted by symbolic name in `backend/app/command_registry.py`.
- **Local-only.** Backend binds `127.0.0.1`. CORS is `localhost:3000` / `tauri://localhost` only.
- **Protected interfaces** (`GigabitEthernet0/1`, `GigabitEthernet0/2`, `Vlan1`) cannot be changed by any action, even with `ENABLE_WRITE_ACTIONS=true`.
- **Credential values are never logged.** Audit log redacts secrets to `<redacted>`.
- **Never push if `scripts/verify-no-secrets.ps1` fails.**
- **Never enable HTTP/HTTPS on the switch.** Never propose telnet.
- **No LLM/MCP raw-CLI tool.** Only expose the same allowlisted tools.
