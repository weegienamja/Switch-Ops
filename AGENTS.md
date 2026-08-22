# AGENTS.md — Multi-agent operating rules for SwitchOps

This file defines how autonomous coding agents (Copilot, Claude, MCP-driven assistants) must behave inside this repository.

## Project context

SwitchOps is a **local-only** desktop dashboard for a single Cisco Catalyst WS-C3560CG-8PC-S (`SWITCHOPS-TEST-SW1`, `192.0.2.190`). It is a Tauri v2 app wrapping a Next.js frontend and a FastAPI sidecar.

## Hard rules — non-negotiable

1. **No raw CLI execution.** There must be no endpoint, tool, or function that accepts an arbitrary IOS command string from the UI, an LLM, or any external caller. All commands are referenced by allowlist name only.
2. **No secrets in source.** Never commit real passwords, enable secrets, or `.env` files. Sample data uses `__REPLACE_WITH_LOCAL_SECRET__`.
3. **No public exposure.** The backend binds to `127.0.0.1`. CORS allows only `localhost` and `tauri://localhost`.
4. **Protected interfaces are immutable.** `GigabitEthernet0/1`, `GigabitEthernet0/2`, and `Vlan1` are refused at the command registry — no exceptions, even with `ENABLE_WRITE_ACTIONS=true`.
5. **Backup before write.** Any safe-write action runs `terminal length 0` + `show running-config`, saves a timestamped backup, then performs the change, then `write memory`, then verifies.
6. **Redact secrets in logs.** Audit entries must never contain plaintext passwords or enable secrets.
7. **No telnet. No HTTP. No HTTPS on the switch.** Do not weaken switch configuration.

## Agent responsibilities

### Backend agent (`backend/**/*.py`)

- Pydantic models for every request and response.
- Allowlist-driven `command_registry.py` — adding a command requires adding it to the list and a parser.
- Tolerant parsers in `app/parsers/` — IOS 12.2 output can be loose; never crash on unexpected lines.
- Credential access via `credential_store.py` only.
- Two switch clients: `switch_client.py` (Netmiko) and `legacy_ssh_client.py` (patched Paramiko). A `MockSwitchClient` reads from `app/sample_outputs/`.
- pytest coverage for allowlist, redaction, parsers, backup naming, audit writes.

### Frontend agent (`frontend/**/*.{ts,tsx,css}`)

- TypeScript strict mode.
- Components handle loading, error, empty states explicitly.
- Motion-based animations, staggered cards, animated topology map, health pulse only when healthy.
- Respect `prefers-reduced-motion`.
- Dark navy/charcoal palette; green/amber/red for health states. No emoji. No generic SaaS purple gradient.
- Network-monospace command output blocks.

### Desktop agent (`desktop/**`)

- Tauri v2 with minimum capabilities — sidecar spawn, shell scope for sidecar only.
- Backend bundled as PyInstaller sidecar binding `127.0.0.1`.
- Health-check retry on launch before the UI loads.
- Windows installer target.

### Security agent (cross-cutting)

- Audits every commit for `Cisco12345` and similar placeholders.
- Owns `scripts/verify-no-secrets.ps1`.
- Reviews every new endpoint, parser, and skill for raw-CLI escape hatches.

### Testing agent

- Backend `pytest` must pass.
- Frontend `pnpm build` must pass (TypeScript checks included).
- Secret scan must pass before any push.

## Acceptance criteria (must all hold)

1. Monorepo scaffold present.
2. Backend runs in mock mode with zero credentials configured.
3. Frontend renders the full dashboard against mock backend.
4. Tauri dev launches frontend + sidecar.
5. Setup wizard exists and stores credentials via keyring or guarded file fallback.
6. No hardcoded secrets.
7. No raw CLI endpoint.
8. All commands allowlisted.
9. Config backup endpoint works in mock mode.
10. Audit logging writes both SQLite and JSONL.
11. Safe-write endpoints exist but 403 unless `ENABLE_WRITE_ACTIONS=true`.
12. Protected ports always 403.
13. UI is animated, polished, accessible.
14. Tests exist and pass.
15. README explains setup, packaging, safety.
16. Build/dev/test scripts exist.
17. Secret verification runs before commit.
18. Final push only after secret scan passes.
