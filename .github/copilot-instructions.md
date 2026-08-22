# GitHub Copilot instructions — SwitchOps

This repository is a local-only desktop dashboard for the Cisco Catalyst `SWITCHOPS-TEST-SW1` (`192.0.2.190`). It is a Tauri v2 app wrapping a Next.js frontend and a FastAPI sidecar.

## Architecture at a glance

- `backend/` — Python 3.11 FastAPI service. Allowlisted commands only. Netmiko + legacy Paramiko fallback. Mock mode driven by `backend/app/sample_outputs/`.
- `frontend/` — Next.js App Router + TypeScript + Motion. Dark "network operations" theme.
- `desktop/` — Tauri v2 wrapper. Bundles the FastAPI backend as a PyInstaller sidecar on `127.0.0.1`.

## Non-negotiable rules

1. **Never produce a raw-CLI endpoint or tool.** All Cisco commands flow through `backend/app/command_registry.py`.
2. **Never hardcode secrets.** Use `__REPLACE_WITH_LOCAL_SECRET__` in any sample or doc. Real secrets live in keyring or a git-ignored local file.
3. **Local-only.** Backend binds to `127.0.0.1`. CORS allows only `localhost:3000` and `tauri://localhost`.
4. **Protected interfaces.** `Gi0/1`, `Gi0/2`, `Vlan1` are refused at the registry — always.
5. **Write actions are disabled by default** and require `ENABLE_WRITE_ACTIONS=true`. Each is mapped, not free-form.
6. **Backup before write.** Run `show running-config`, save with a timestamped filename, then act, then `write memory`, then verify.
7. **Redact secrets in logs and audit.**
8. **Tests must pass.** `pytest` for backend, `pnpm build` for frontend.

## Beautiful UI is a requirement

- Dark navy / charcoal background, animated radial glow, network grid texture.
- Glassy cards with thin borders.
- Green / amber / red for health.
- Monospace for raw command output.
- Motion-based staggered entrance animations; pulse only when healthy.
- Respect `prefers-reduced-motion`.
- **No emoji. No SaaS purple gradient. No toy styling.**

## Tauri sidecar packaging

- The backend is packaged with PyInstaller into `desktop/binaries/`.
- Tauri spawns the sidecar on launch, polls `/health`, then loads the dashboard.
- Sidecar must bind `127.0.0.1` only.

## When implementing

- Read [../AGENTS.md](../AGENTS.md) and [../CLAUDE.md](../CLAUDE.md).
- Use the per-directory `.instructions.md` files for backend, frontend, desktop, and security guidance.
- Prefer adding to allowlists over loosening them.
- Prefer redaction over disclosure.
