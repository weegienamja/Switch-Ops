# EWPS v0.2.1 Alpha — Controlled Path Binding Fix

**Experimental pre-release:** `ewps-v0.2.1-alpha`

EWPS v0.2.1 is a corrective release on the `research/ewps-v0.2` line. The EWPS
v0.2 mathematics remain unchanged.

## Corrected integration defect

EWPS v0.2.0 could prepare and independently verify the contained WSL2 dual-path
testbed, but experiment startup in the observed configuration used the normal
real-interface candidate selection. A controlled-looking experiment could
therefore record Windows Ethernet candidates instead of the intended lab paths.
The v0.2.0 tag and release remain unchanged as the historical record.

v0.2.1 makes the backend authoritative for experiment source identity:

- every new live experiment records `REAL_INTERFACES` or
  `CONTROLLED_DUAL_PATH` as an immutable source mode;
- controlled startup requires passing prerequisites, an EWPS-owned and
  reconciled topology, fresh independent verification of both collectors, an
  explicit prepared scenario, and exactly `lab-path-a` plus `lab-path-b`;
- real-interface discovery cannot supplement or replace a controlled snapshot;
- source mode, candidate labels and kinds, lab instance ID, topology version,
  initial verification, and scenario are persisted, exported, and retained by
  replay;
- restart inspects the actual namespaces and ownership marker, then re-verifies
  both paths before reporting `LAB READY`;
- a lost or replaced lab records `CONTROLLED_LAB_LOST` and makes both bound
  paths unavailable without falling back to Windows interfaces; and
- the Observatory polls backend lab truth, exposes an explicit source selector,
  and renders controlled candidates as logical lab paths.

The experiment supplied to diagnose the defect remains valid only as evidence
of the v0.2.0 source-binding failure. Its single viable real Ethernet stream and
zero algorithm disagreement do not evaluate the intended controlled dual-path
hypothesis.

Controlled logical paths do not demonstrate physical, ISP, or independent
failure-domain diversity. EWPS remains read-only shadow-mode research and does
not steer application traffic.
