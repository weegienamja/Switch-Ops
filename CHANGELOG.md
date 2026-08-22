# Changelog

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

- Added process-local write lock and a fixed operation catalog for admin
  up/down, PoE auto/off, and sanitized descriptions on Gi0/3-Gi0/8 only.
- Added precheck, exact-state capture, backup, IOS rejection detection,
  property verification, audit, and rollback semantics.
- Kept Gi0/1, Gi0/2, and Vlan1 immutable across short, long, mixed-case, and
  leading-zero aliases.
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

- Measured warm full observations at about 1.93-1.98 seconds versus about
  11.75 seconds for the original cold architecture; prompt-anchored port reads
  measured about 46 ms versus about 509 ms.
- Temporary real-switch LLDP enable/read/restore completed with zero advertised
  neighbours; running configuration was restored and startup was unchanged.
- Real Gi0/6 validation completed `disabled -> notconnect -> disabled`; final
  running/startup fingerprints match, pending operations are zero, and writes
  are disabled again.
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
