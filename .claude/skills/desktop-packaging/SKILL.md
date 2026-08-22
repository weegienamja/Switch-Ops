---
name: desktop-packaging
description: Tauri v2 + FastAPI sidecar packaging for the Windows desktop build of SwitchOps.
---

# Desktop packaging skill

## Goal

Ship `SwitchOps.exe` (and an MSI) that:

1. Spawns the bundled FastAPI sidecar on `127.0.0.1:8765`.
2. Polls `GET /health` until ready (~20 s timeout).
3. Loads the Next.js dashboard.
4. Kills the sidecar on close.

## Steps

1. **Build the sidecar.** `desktop/scripts/build-backend-sidecar.ps1` runs PyInstaller with `--onefile --name switchops-backend` against `backend/app/main.py`, output to `desktop/binaries/switchops-backend-<target-triple>.exe`.
2. **Reference it in `tauri.conf.json`** as `bundle.externalBin`.
3. **Spawn from Rust** in `desktop/src-tauri/src/main.rs` via `tauri_plugin_shell::ShellExt::sidecar(...)`.
4. **Health check** with `reqwest` (or `tauri-plugin-http`) before showing the main window.
5. **Build** with `pnpm tauri build` — target `nsis` and `msi` on Windows.

## Constraints

- Sidecar binds `127.0.0.1` only. Never `0.0.0.0`.
- Tauri capabilities are the minimum needed (shell sidecar + http to `127.0.0.1`).
- Do not bundle real credentials. Setup wizard collects them at runtime.
