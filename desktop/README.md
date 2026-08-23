# Desktop — SwitchOps (Tauri v2)

Wraps the Next.js frontend and bundles the FastAPI backend as a PyInstaller sidecar bound to `127.0.0.1:8765`.

## Dev

```powershell
# 1. Build the backend sidecar
powershell -ExecutionPolicy Bypass -File scripts/build-backend-sidecar.ps1

# 2. Start Tauri from the repository root
cd ../..
pnpm desktop:dev
```

The desktop always starts its sidecar with fixture mode disabled. A clean
launch opens first-run setup until real device credentials are saved and the
connection test succeeds.

## Build (Windows installer)

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-backend-sidecar.ps1
powershell -ExecutionPolicy Bypass -File scripts/package-windows.ps1
```

Output: `src-tauri/target/release/bundle/` (MSI + NSIS).

## Prerequisites

- Rust + cargo (rustup)
- Node 20+, pnpm 9+
- Python 3.11-3.13 with venv (the build script pins PyInstaller)
- WebView2 runtime (ships with Windows 11)
