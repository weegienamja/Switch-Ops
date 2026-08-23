# SwitchOps

SwitchOps is a Windows desktop application for observing a Cisco IOS Catalyst
switch and applying a small catalog of explicitly authorized, bounded changes.
It runs locally, keeps device data on the operator's PC, and does not provide a
raw CLI endpoint.

## Install the Windows app

Normal users do not need Python, Node.js, Rust, pnpm, or a source checkout.

1. Open the repository's [Releases](https://github.com/weegienamja/Switch-Ops/releases) page.
2. Open **SwitchOps v0.4.1 - Live Operations**.
3. Download `SwitchOps_0.4.1_x64-setup.exe` (NSIS) or
   `SwitchOps_0.4.1_x64_en-US.msi` (MSI).
4. Install and launch SwitchOps.
5. Enter the management IP or hostname, username, password, and optional
   enable secret for your own Cisco IOS switch.
6. Select **Save and test connection**. The credentials remain stored locally,
   and the first dashboard appears only after a real connection succeeds.

The first launch has no configured device, topology, telemetry, or history.
Production installers do not include the source-only sample-output fixtures and
cannot be switched into fixture mode. Unsigned development releases may cause
Windows SmartScreen to show its standard publisher warning.

## v0.4.1: Live Operations

- A persistent, serialized SSH worker owns the device session and reconnects
  with bounded backoff. Host keys are pinned on first use and later changes are
  refused.
- Tiered collectors update interface state, PoE, health, MAC/ARP/CDP/LLDP
  evidence, reconciliation, and retained local history without concurrent CLI
  sessions.
- Typed server-sent events update live state and bounded-operation progress.
- Five predefined interface actions are available: administrative up/down,
  PoE auto/off, and a sanitized description. There is no arbitrary command
  input.
- Each change performs a precheck, captures exact bounded state, creates a
  local running-configuration backup, classifies IOS output, verifies the
  requested property, audits the result, and rolls back when verification
  fails.
- Operations modify running configuration only. Saving to startup
  configuration is separate, explicit, and confirmed; SwitchOps never saves
  automatically.

## Interface write policy

SwitchOps does not contain a public, device-specific port allowlist.

- `UNMANAGED` is the default for every new device and interface and cannot be
  modified.
- `PROTECTED` explicitly denies modification.
- `OPERABLE` can be assigned only to a validated physical interface and is the
  only state that can authorize a bounded interface operation.
- The device is identified in the policy by a SHA-256 digest of its configured
  address; the clear-text address is not stored in the policy file.
- A management SVI whose address exactly matches the configured device address
  is automatically marked `PROTECTED`.
- Controlled writes are globally off by default. Enabling them requires a
  deliberate confirmation in Settings.
- Every process starts with device control locked. A separate session unlock is
  required, and restarting SwitchOps locks it again.

The FastAPI backend enforces every gate. UI state is never treated as
authorization, and a missing, malformed, or invalid policy fails closed to
read-only.

## Evidence model

SwitchOps keeps observed facts separate from expected topology. Interface
descriptions are intent, not proof that a named device is present. CDP and LLDP
announcements are direct evidence; MAC learning proves reachability through a
port but does not by itself identify the directly attached device. The UI
reports `aligned`, `drift`, `expected-not-observed`, `unexpected`, `uncertain`,
or `not-applicable` without converting uncertainty into a guess.

## Local data and privacy

The packaged backend binds only to `127.0.0.1:8765`. It has no cloud service,
analytics uploader, or remote telemetry destination.

Runtime data is stored below `%LOCALAPPDATA%\SwitchOps`:

- `data/` — local SQLite telemetry, audits, configuration history, topology
  intent, host-key pin, and the per-device interface policy;
- `backups/` — running-configuration backups created on demand or before a
  bounded change;
- `logs/` — redacted local application logs.

Credentials use Windows Credential Manager when available. If the OS credential
backend is unavailable, SwitchOps uses an access-restricted local fallback file
and reports that storage choice in Settings. Passwords and enable secrets are
never returned by the API. Configuration history and backups can contain
sensitive network configuration and must be protected as private local data.

Clearing credentials removes the stored login but does not delete telemetry,
backups, policy, or configuration history. To reset all local application data,
exit SwitchOps and remove `%LOCALAPPDATA%\SwitchOps` using the Windows account
that created it.

See [SECURITY.md](SECURITY.md) for the threat boundary, reporting process, and
supported-version policy.

## Compatibility and safety boundary

v0.4.1 targets Windows x64 and Cisco IOS devices supported by Netmiko's
`cisco_ios` driver. Some older IOS releases require legacy SSH algorithms.
SwitchOps enables those algorithms only inside its backend process; it does not
weaken Windows or system-wide SSH configuration. Keep management SSH reachable
only from a trusted management network.

The application intentionally does not provide Telnet, switch HTTP/HTTPS
configuration, arbitrary CLI, automatic startup saves, SNMP configuration, or
cloud control.

## Build from source (developers)

Prerequisites:

- Windows 10 or 11 x64 with WebView2
- Python 3.11–3.13
- Node.js 20+ and pnpm 9
- Rust stable with the MSVC target and Windows build tools

```powershell
pnpm install
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

# Validate
.\.venv\Scripts\python.exe -m pytest backend\app\tests -q
pnpm --dir frontend test
pnpm --dir frontend typecheck
pnpm --dir frontend lint
pnpm --dir frontend build
cargo test --manifest-path desktop\src-tauri\Cargo.toml --locked

# Package the sidecar and both Windows installers
powershell -ExecutionPolicy Bypass -File desktop\scripts\build-backend-sidecar.ps1
powershell -ExecutionPolicy Bypass -File desktop\scripts\package-windows.ps1
```

Source-only tests can set `SWITCH_MOCK_MODE=true` to use the synthetic fixtures
under `backend/app/sample_outputs/`. The packaged sidecar forces this setting
off, and the desktop launcher also sets it to false.

Generated binaries and checksums are release artifacts and are ignored by Git;
they must not be committed to `main`.

## Project layout

- `backend/` — FastAPI sidecar, SSH worker, parsers, policy, audit, and tests
- `frontend/` — statically exported Next.js/React interface
- `desktop/` — Tauri v2 shell, PyInstaller sidecar build, NSIS/MSI packaging
- `docs/` — architecture and historical design notes
- `scripts/` — privacy, secret, and rendered-UI validation helpers

Dependency manifests and lockfiles are committed for reproducible review. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for provenance and license
verification notes.
