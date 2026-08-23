# Changelog

## 0.6.0 - Unreleased

### Change Assurance

- Added durable, single-device, single-step change sessions above the existing
  bounded operation executor. Plans support only administrative up/down, PoE
  auto/off, and sanitized interface descriptions; arbitrary CLI remains absent.
- Added unlock-free, read-only preflight with explicit PASS, WARN, INFO, and
  BLOCK evidence for device state, interface policy, rollback representation,
  discovery freshness, observed attachments, uplink indicators, topology
  uncertainty, control-path correlation, and running/startup divergence.
- Added evidence-backed blast-radius explanations without invented risk scores.
  Confirmed local-host or gateway paths block disruptive admin-down and PoE-off
  plans before any IOS configuration is attempted.
- Added normalized before/after snapshots for target and unrelated interfaces,
  configuration fingerprints, topology, health, and evidence timestamps. Raw
  configurations and MAC addresses are not stored in the change-session DB.
- Added conservative comparison outcomes: verified direct changes can succeed,
  unrelated observations produce warnings without causal claims, verified
  primitive rollback is reported as rolled back, and lost proof is explicitly
  indeterminate.
- Added crash recovery and immutable terminal audit records in a private local
  `change-sessions.sqlite` history store.
- Added Change Control review, preflight, execution, assurance, and durable
  history UI. Planning stays available while writes are locked or policy-blocked;
  execution still requires every backend write gate.
- Kept startup saves separate and explicit. Change sessions modify and verify
  running configuration only and never invoke an automatic save.

## 0.5.0 - 2026-08-23

### Discovery and identity

- Added structured, timestamped discovery evidence with explicit claim support,
  provenance, categorical confidence, and current/aging/stale/historical state.
- Separated endpoint existence confidence from identity confidence and made
  link-only unknown endpoints first-class topology entities.
- Added offline OUI vendor hints through the bundled `netaddr` IEEE registry,
  with conservative handling of local, multicast, broadcast, invalid, and
  unregistered addresses.
- Correlated single learned MAC addresses with ARP IP observations without
  treating ARP or MAC reachability as direct physical attachment.
- Added stable entity identities across port moves, conflict records, evidence
  revocation, and retained historical identities.

### Topology and reconciliation

- Removed description-only expected devices and links from the observed graph.
  Interface descriptions now produce port-level expectation records only.
- Added Observed, Reconciled, and Expected views plus a structured evidence
  inspector with confidence, freshness, provenance, and last-seen detail.
- Kept multiple addresses on uplinks as learned-behind evidence rather than
  multiplying direct endpoint cards.
- Made the reconciler consume the authoritative evidence-backed topology and
  surface conflicts as uncertainty.

### Persistence and live API

- Added an explicitly versioned `discovery-history.sqlite` schema for stable
  entity/evidence continuity and observation history.
- Added an in-place schema migration marker for v0.4.1 topology-intent data.
- Added authoritative typed topology envelopes to the local SSE stream; fast
  interface overlays age nodes rather than rebuilding identity in the UI.

## 0.4.1 - 2026-08-23

### Public release hardening

- Removed production fixture runtime behavior. Packaged builds force real mode,
  sample IOS output is no longer bundled, and a clean first launch contains no
  configured device or generated network state.
- Removed device-specific onboarding defaults and deployment branding.
- Replaced the fixed port layout with a validated, local, per-device interface
  policy: PROTECTED, OPERABLE, or fail-closed UNMANAGED.
- Made controlled writes default off and retained the process-local lock that
  starts engaged on every launch.
- Added a deliberate Settings workflow for global write opt-in and per-interface
  policy changes. The backend remains authoritative and holds policy stable for
  each complete transaction.
- Generalized physical-interface normalization across common Catalyst Fast,
  Gigabit, 10/25/40/100-Gigabit naming and dynamic front-panel rendering.
- Added honest first-run connection testing, hardened runtime/release ignore
  rules, public privacy documentation, a security policy, and third-party
  provenance notes.
- Standardized packaged runtime data at `%LOCALAPPDATA%\SwitchOps` and versioned
  all application metadata at 0.4.1.

## 0.4.0 - 2026-08-22

### Live operations

- Added a single persistent, host-key-pinned Catalyst session worker with
  priority scheduling, reconnect backoff, stale/offline state, and strict SSH
  serialization.
- Added 5-second fast, 20-second rotating medium, 60-second slow discovery,
  and retained-history tiers plus typed server-sent events.
- Integrated LIVE / STALE / RECONNECTING / OFFLINE state, per-tier freshness,
  live port and PoE overlays, and fixed-stage operation progress into the
  Visual network.

### Controlled changes

- Added a process-local write lock and a fixed operation catalog for admin
  up/down, PoE auto/off, and sanitized descriptions.
- Added precheck, exact-state capture, backup, IOS rejection detection,
  property verification, audit, and rollback semantics.
- The initial deployment used a fixed protected/operable layout; v0.4.1
  replaces that deployment-specific rule with local per-device policy.
- Removed automatic startup saves. Running-vs-startup divergence is visible;
  save is separate and requires explicit confirmation.

### Evidence and discovery

- Added allowlisted LLDP summary/detail commands, tolerant parsers, normalized
  topology/reconciliation evidence, slow-tier updates, and a read-only guide.
- Added conservative local-PC identity correlation using the active local NIC,
  management subnet, switch MAC table, ARP agreement, access-port state, and
  ambiguity rejection. Full MAC addresses are never returned.
- Added read-only SNMP inspection that reports only versions and counts. v0.4
  neither configures nor depends on SNMP.

### Validation

- Hardware validation confirmed persistent-session performance, reversible
  operation verification, rollback, and restored running/startup state.
- Repaired Recent observations geometry and semantics for missing, sparse,
  irregular, and reset data.

## 0.3.0

- Added topology reconciliation across observed, expected, historical,
  inferred, and unknown evidence without treating descriptions as sightings.

## 0.2.1

- Corrected topology correlation, evidence labels, chart honesty, Settings,
  and bounded connection diagnostics.

## 0.2.0

- Added historical telemetry, delta health, network events, the visual network,
  Lab Guide, configuration history, and read-only planning.
