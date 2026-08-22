---
applyTo: "desktop/**"
---

# Desktop instructions

- Tauri v2. Minimum capabilities — `shell:allow-execute` scoped only to the bundled sidecar binary.
- Backend is packaged with PyInstaller to `desktop/binaries/switchops-backend-x86_64-pc-windows-msvc.exe`.
- On `setup`, spawn the sidecar with `--host 127.0.0.1 --port 8765`. Poll `GET http://127.0.0.1:8765/health` up to ~20 s before navigating the webview.
- On window-close, kill the sidecar process.
- Never bind the sidecar to a non-loopback interface.
- Windows installer target (MSI + NSIS) named "SwitchOps".
