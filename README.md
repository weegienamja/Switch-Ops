# SwitchOps

A polished local desktop dashboard for the SWITCHOPS-TEST-SW1 Cisco Catalyst WS-C3560CG-8PC-S, built around safe, allowlisted operations.

> **Local lab dashboard. Do not expose publicly.** Access locally or through a private VPN such as Tailscale. The target switch runs legacy SSH cryptography (Cisco IOS 12.2(55)EX2) and must never be reachable from the public internet.

## What it is

SwitchOps is a Windows-first desktop application that:

- Connects to the local Cisco Catalyst over SSH using Netmiko, with a legacy-SSH Paramiko fallback for the old KEX/cipher/MAC suites the switch requires.
- Runs **only allowlisted read-only commands** in v1.
- Surfaces switch state — health, ports, PoE, environment, CPU, memory, MAC table, logs — in a polished dark "network operations" UI.
- Logs every executed command into both SQLite and a JSONL audit log.
- Backs up the running configuration on demand with timestamped filenames.
- Ships a controlled safe-write mode (disabled by default) for a tiny set of mapped actions on spare ports.
- Never accepts arbitrary CLI from the UI or any future LLM/MCP integration.

## Switch under management

| Field            | Value |
| ---------------- | --- |
| Hostname         | SWITCHOPS-TEST-SW1 |
| Model            | Cisco Catalyst WS-C3560CG-8PC-S V03 |
| IOS              | 12.2(55)EX2 |
| Management IP    | 192.0.2.190/24 |
| Default gateway  | 192.0.2.19 |
| PoE budget       | 124 W |
| LAN              | 192.0.2.18/24 |

Protected interfaces (refuse all automation): `GigabitEthernet0/1` (router uplink), `GigabitEthernet0/2` (main PC), `Vlan1` (management).

## Architecture

```
┌──────────────────────────────────────────────────┐
│  Tauri v2 Windows shell (SwitchOps.exe) │
│ ┌────────────────────────┐  ┌──────────────────┐ │
│ │ Next.js dashboard UI   │  │ FastAPI sidecar  │ │
│ │ React + Motion         │◀─│ 127.0.0.1:8765   │ │
│ │ Dark NetOps theme      │  │ allowlisted only │ │
│ └────────────────────────┘  └────────┬─────────┘ │
└─────────────────────────────────────┬┴───────────┘
                                      │ SSH (LAN only)
                              ┌───────▼────────┐
                              │  SWITCHOPS-TEST-SW1  │
                              │ 192.0.2.190   │
                              └────────────────┘
```

## Quick start (mock mode, no switch required)

Sample IOS outputs in `backend/app/sample_outputs/` drive the full UI without touching the network. The desktop defaults to real mode, so mock mode must be selected explicitly.

### Backend

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
$env:SWITCH_MOCK_MODE = "true"
.\.venv\Scripts\python.exe -m backend.app.main
```

The API listens on `http://127.0.0.1:8765`. Health: `GET /health`.

### Frontend

```powershell
pnpm install
pnpm --filter @switchops/frontend dev
```

Open `http://localhost:3000`.

## Real switch mode

1. Build the sidecar once, then launch the desktop in its default real mode:

   ```powershell
   powershell -ExecutionPolicy Bypass -File desktop/scripts/build-backend-sidecar.ps1
   Remove-Item Env:SWITCHOPS_DESKTOP_MODE -ErrorAction SilentlyContinue
   pnpm desktop:dev
   ```

2. On first run, use the **Setup Wizard** to enter the switch host, username, password, optional enable secret, and the fixed `cisco_ios` device type.
3. The backend stores them via Windows Credential Manager (preferred) or, if keyring is unavailable, a restricted local credentials file with a warning banner. Packaged runtime data lives below `%LOCALAPPDATA%\SwitchOps\SwitchOps`.

For a safe desktop demo, replace the second and third commands above with:

```powershell
$env:SWITCHOPS_DESKTOP_MODE = "mock"
pnpm desktop:dev
```

## Legacy SSH troubleshooting

This switch ships with weak crypto. The backend asks Paramiko/Netmiko to allow:

- KEX: `diffie-hellman-group1-sha1`
- Ciphers: `aes128-cbc`, `3des-cbc`, `aes256-cbc`
- MACs: `hmac-sha1`, `hmac-sha1-96`, `hmac-md5`, `hmac-md5-96`
- Host key & pubkey: `ssh-rsa`

If Netmiko fails with a KEX or cipher negotiation error, the backend automatically retries with an in-process Paramiko transport. The compatibility change is process-local; it does not weaken system-wide SSH settings.

## Desktop packaging

```powershell
# 1. Package the FastAPI backend as a sidecar exe
powershell -ExecutionPolicy Bypass -File desktop/scripts/build-backend-sidecar.ps1

# 2. Build the Tauri Windows installer
powershell -ExecutionPolicy Bypass -File desktop/scripts/package-windows.ps1
```

The result is `SwitchOps.exe` (and an MSI installer). It launches the bundled sidecar on `127.0.0.1`, waits for `/health`, then loads the dashboard.

## Backend dev

```powershell
cd backend
pip install -r requirements.txt
pytest
python -m app.main
```

## Frontend dev

```powershell
cd frontend
pnpm install
pnpm dev
pnpm build
```

## Tests

```powershell
cd backend
pytest -v
```

Frontend type-check + build:

```powershell
cd frontend
pnpm build
```

## Security model

- Credentials live in OS keyring or local `data/credentials.json` (git-ignored). Never in source.
- The switch SSH host key is pinned on first use in the restricted runtime directory; later changes fail closed.
- The backend binds to `127.0.0.1` only.
- Every command is allowlisted by name; there is **no raw CLI endpoint**.
- Write actions are gated by `ENABLE_WRITE_ACTIONS=true` and a per-action allowlist.
- Protected interfaces (`Gi0/1`, `Gi0/2`, `Vlan1`) are refused at the registry level.
- Every command is logged to SQLite and JSONL, with secrets redacted.
- `scripts/verify-no-secrets.ps1` scans for committed secrets before pushing.

## API endpoints

```
GET  /health
GET  /api/setup/status
POST /api/setup/credentials
GET  /api/switch/dashboard
GET  /api/switch/summary
GET  /api/switch/interfaces
GET  /api/switch/poe
GET  /api/switch/errors
GET  /api/switch/environment
GET  /api/switch/cpu
GET  /api/switch/memory
GET  /api/switch/mac-table
GET  /api/switch/logs
GET  /api/switch/audit
POST /api/switch/backup-config

# disabled unless ENABLE_WRITE_ACTIONS=true
POST /api/switch/ports/{port}/enable
POST /api/switch/ports/{port}/disable
POST /api/switch/ports/{port}/description
POST /api/switch/ports/{port}/poe/enable
POST /api/switch/save-config
```

## Roadmap

- **v1 (this release)** — read-only dashboard, audit logging, config backup, mock mode.
- **v2** — safe writes on spare ports, PoE toggle on Meraki port, save config.
- **v3** — MCP server exposing the *same* safe tools to an LLM. Never raw CLI.

## Troubleshooting

- **SSH KEX failure** — legacy options are auto-applied; ensure `SWITCH_LEGACY_SSH=true`. Confirm with the working OpenSSH one-liner in `docs/troubleshooting.md`.
- **Credentials wrong** — Setup panel → Reset → re-enter.
- **Enable secret wrong** — same flow; nothing is printed back.
- **Switch unreachable** — `ping 192.0.2.190`, verify VLAN 1 IP, check cable on Gi0/2.
- **CORS** — backend only allows `localhost:3000` and `tauri://localhost` by default.
- **Sidecar not starting** — in a packaged run, check `%LOCALAPPDATA%\SwitchOps\SwitchOps\logs\server.log`; ensure port 8765 is free.
- **Keyring unavailable** — backend falls back to a restricted credentials file under the runtime data directory. A warning banner shows in the UI.
- **GitHub push blocked due to secret** — run `scripts/verify-no-secrets.ps1`; rewrite history with `git filter-repo` if a real secret leaked.
