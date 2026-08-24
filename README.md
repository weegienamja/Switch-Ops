# SwitchOps

SwitchOps is a Windows desktop application for observing a Cisco IOS Catalyst
switch and applying a small catalog of explicitly authorized, bounded changes.
It runs locally, keeps device data on the operator's PC, and does not provide a
raw CLI endpoint.

## Install the Windows app

Normal users do not need Python, Node.js, Rust, pnpm, or a source checkout.

1. Open the repository's [Releases](https://github.com/weegienamja/Switch-Ops/releases) page.
2. Open **SwitchOps v0.6.0 - Change Assurance**.
3. Download `SwitchOps_0.6.0_x64-setup.exe` (NSIS) or
   `SwitchOps_0.6.0_x64_en-US.msi` (MSI).
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

## v0.5.0: Discovery & Identity

v0.5.0 added an evidence-aware identity and attachment model.

- Full nodes in the default **Observed** topology require current evidence of
  existence. An interface description alone stays on the port as expected
  intent and cannot create an observed television, server, AP, or other device.
- Unknown endpoints are first-class: SwitchOps can establish that something is
  attached while keeping its name, vendor, model, and role unknown.
- Structured evidence records cover interface link and description, CDP, LLDP,
  MAC learning, ARP correlation, local-host correlation, local OUI lookup,
  SwitchOps intent, accepted plans, and prior observations.
- Existence confidence and identity confidence are separate categorical claims:
  `unknown`, `low`, `medium`, `high`, or `confirmed`. Contradictory sources are
  retained, lower confidence, and surface as uncertainty rather than a silent
  last-writer-wins decision.
- Evidence is `current`, `aging`, `stale`, or `historical`. Missed polls and
  reconnects age facts; only a successful superseding observation revokes them
  into history.
- Stable entity identifiers preserve continuity across port moves. A successful
  disappearance or replacement retains the previous identity locally as
  history without presenting it as current fact.
- **Observed**, **Reconciled**, and **Expected** network modes give intent the
  right visual authority. Expected-only relationships are compact port records,
  not peers of currently observed endpoints.

## v0.6.0: Change Assurance

The current public release adds durable, evidence-backed assurance around the
existing bounded operation executor.

- Every plan targets one device interface and contains exactly one symbolic
  operation: admin up/down, PoE auto/off, or a sanitized description.
- Planning and preflight are read-only and do not require the process-local
  unlock. Preflight explains attachments, learned-behind entities, uplink and
  control-path evidence, topology uncertainty, rollback availability, and
  running/startup divergence.
- Confirmed local-host or gateway-path evidence blocks disruptive operations
  before IOS configuration. Discovery can block a write but can never grant
  write authority or change interface policy.
- Execution rechecks all evidence while holding the existing write and policy
  gates, then uses the trusted v0.5 bounded executor for backup, IOS response
  classification, direct verification, audit, and bounded rollback.
- Normalized before/after snapshots distinguish declared effects from unrelated
  changes. Unrelated differences produce warnings without claiming causality;
  missing final proof produces an honest `INDETERMINATE` result.
- Sessions and blocked plans are retained in private local history. Interrupted
  in-flight records recover as `INDETERMINATE`, and terminal audit records are
  immutable.
- A verified change affects running configuration only. Startup configuration
  remains unchanged until the operator separately unlocks and confirms Save.

## v0.7.0 source: Unified Lab

The current source tree adds an optional, read-only Meraki Dashboard evidence
source alongside the existing Catalyst observation. It does not turn Meraki
evidence into IOS write authority and does not contain a Meraki write path.

- **Unified Inventory** retains Catalyst and Meraki provider records, source
  health, freshness, compact claims, and structured provenance.
- Exact serials and globally administered device/chassis MACs can confirm an
  identity. Management addresses and reciprocal LLDP/CDP are supporting
  evidence. Names and models are weak hints only. Strong disagreement merges
  nothing.
- Identity, attachment, relationship, availability, name, model, VLAN, and
  port context reconcile independently as `AGREED`, `PROVIDER_ONLY`, `STALE`,
  `AMBIGUOUS`, `CONFLICT`, or `UNKNOWN`.
- Ambiguous identity candidates remain separate until the operator confirms or
  rejects the relationship locally. A strong conflict cannot be overridden.
- The Meraki boundary exposes only named GET operations for organization and
  network selection, inventory, availability, MX uplinks/ports, LLDP/CDP,
  applicable MS port status, and recent clients. There is no URL/endpoint
  proxy.
- Raw Dashboard payloads are discarded after normalization. Device serials,
  hardware MACs, management addresses, and recent-client identifiers are
  protected with a per-install local HMAC key. Recent-client usage, usernames,
  IP addresses, and raw MAC addresses are not retained.
- The Visual Network keeps its Catalyst geometry and adds an explicitly
  labelled Meraki overlay. Change Control remains essentially unchanged.

Configure the optional source in **Settings > Meraki evidence source**. The API
key is accepted only when Windows Credential Manager is available; there is no
file or environment fallback for it. See
[docs/V0.7-UNIFIED-LAB.md](docs/V0.7-UNIFIED-LAB.md) for the evidence contract
and security boundary.

## v0.8.0 development: Lab Assurance

The current development branch adds a read-only, capability-driven view of an
actual lab: what depends on what, which paths are proven, and what the observed
graph says will be affected by a failure.

- Additional IOS/IOS-XE targets are explicit and keyring-only. Their local
  registry persists opaque IDs, not device addresses, usernames, labels, or
  secrets.
- Capabilities are `SUPPORTED`, `UNSUPPORTED`, or `UNKNOWN` based on current
  command/configuration evidence. Vendor or model is never enough by itself.
- CDP/LLDP can prove a direct adjacency. Reciprocal observations increase
  confidence. MAC learning and ARP remain reachability/correlation evidence and
  cannot manufacture a cable or device identity.
- Findings cover resiliency, Layer 2 consistency, access-edge protections,
  management exposure, PoE, capacity, interface errors, and evidence gaps.
  SwitchOps deliberately does not assign a numeric network score.
- Path Explorer labels every hop `PROVEN`, `INFERRED`, `EXPECTED`,
  `AMBIGUOUS`, or `UNKNOWN`; failure scenarios explain consequence confidence
  and possible loss of SwitchOps control reachability.
- VLAN, trunk, SVI, gateway, and VRF concepts form the logical view. Different
  VLANs are not called isolated without separate enforcement evidence.
- Bounded PC-originated probes distinguish service health from link state and
  report latency, jitter, loss, availability, and route changes where observed.

See [docs/V0.8-LAB-ASSURANCE.md](docs/V0.8-LAB-ASSURANCE.md) for contracts,
boundaries, and the current implementation scope.

## Experimental research branch: EWPS v0.1 Alpha

The separate `research/ewps-v0.1` branch adds the **EWPS Observatory** for
evidence-weighted path-selection research. It is not SwitchOps v0.9 and does
not change the normal SwitchOps release version or roadmap.

- EWPS is observation/shadow mode only and has no route-changing execution
  path.
- Safe source-bound telemetry, comparison strategies, explicit hysteresis,
  local SQLite recording, privacy-safe export, deterministic replay, and a
  deterministic simulator share one versioned EWPS engine.
- `P_cert` is a dimensionless evidence-confidence index built from heuristic
  functions, not a statistically calibrated probability.

See [docs/EWPS-V0.1-RESEARCH.md](docs/EWPS-V0.1-RESEARCH.md) for the model,
methodology, safety boundary, limitations, and reproduction instructions.

## Experimental research branch: EWPS v0.2 Alpha

The separate `research/ewps-v0.2` branch extends the observatory with an
operator-started controlled WSL2 dual-path lab and v0.2 calibration changes.
It remains **SHADOW MODE — RECOMMENDATIONS ONLY**, does not steer normal
traffic, is not SwitchOps v0.9, and is not merged into `main`.

- Performance evidence confidence is separated from topology confidence;
  weak/unknown structure stays visible without normally invalidating good
  direct telemetry.
- Freshness starts from observation validation, evidence density saturates
  more slowly, and rolling loss uses retained per-probe outcomes.
- Persistent candidate unavailability, transient failures, and recovery are
  classified and summarized separately.
- The contained lab exposes two independently probed controlled logical paths;
  it makes no physical, ISP, or independent failure-domain diversity claim.
- Successful exports now show their exact saved path and the desktop build can
  open only the fixed EWPS export folder.
- Stored v0.1 sessions remain readable and replay under unchanged `0.1.0`
  semantics.

See [docs/EWPS-V0.2-RESEARCH.md](docs/EWPS-V0.2-RESEARCH.md) and
[docs/EWPS-V0.2-RELEASE-NOTES.md](docs/EWPS-V0.2-RELEASE-NOTES.md).

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

SwitchOps keeps `observed`, `expected`, `inferred`, and `historical` knowledge
separate. It also separates existence, identity, role, relationship, evidence,
and confidence. Interface descriptions are intent, not proof that a named
device is present. CDP and LLDP can establish a direct neighbour; MAC learning
establishes reachability through a port but may represent clients behind an
uplink, AP, bridge, hypervisor, or downstream switch. ARP correlates an IP with
a MAC but does not prove physical attachment. OUI supplies only an offline
vendor hint and is ignored for invalid, multicast, broadcast, and locally
administered addresses.

Relationship types are `direct-neighbour`, `attached-endpoint`,
`learned-behind`, `gateway-path`, and `expected-neighbour`. Reconciliation
reports `aligned`, `drift`, `expected-not-observed`, `unexpected`, `uncertain`,
or `not-applicable` without converting uncertainty into a guess. Health remains
separate: a healthy link can still have topology drift.

Detailed evidence, source provenance, confidence, freshness, and last-seen time
are available from the selected port inspector. Structured history is stored in
the local `discovery-history.sqlite` database. Schema migrations are additive
and preserve existing v0.4.1 topology intent.

## Local data and privacy

The packaged backend binds only to `127.0.0.1:8765`. It has no SwitchOps cloud
service, analytics uploader, or remote telemetry destination. When the operator
explicitly configures the optional Meraki source, the local backend makes only
its allowlisted read requests to `api.meraki.com`.

Runtime data is stored below `%LOCALAPPDATA%\SwitchOps`:

- `data/` — local SQLite telemetry, durable change sessions, normalized
  Unified Lab snapshots, local identity decisions, audits, configuration
  history, topology intent, host-key pin, and the per-device interface policy;
- `backups/` — running-configuration backups created on demand or before a
  bounded change;
- `logs/` — redacted local application logs.

IOS credentials use Windows Credential Manager when available. If that backend
is unavailable, the IOS store uses an access-restricted local fallback file and
reports the choice in Settings. The optional Meraki API key is stricter: it is
stored only in Windows Credential Manager and is rejected if that store is
unavailable. Secrets are never returned by the API. Configuration history,
backups, selected provider scope, and normalized network evidence can contain
sensitive local information and must be protected as private local data.

Clearing credentials removes the stored login but does not delete telemetry,
backups, policy, or configuration history. To reset all local application data,
exit SwitchOps and remove `%LOCALAPPDATA%\SwitchOps` using the Windows account
that created it.

See [SECURITY.md](SECURITY.md) for the threat boundary, reporting process, and
supported-version policy.

## Compatibility and safety boundary

The formally verified hardware remains Cisco `WS-C3560CG-8PC-S` running IOS
`12.2(55)EX2` on Windows x64. Other Catalyst models supported by Netmiko's
`cisco_ios` driver may work but are not claimed as validated. Some older IOS
releases require legacy SSH algorithms.
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
