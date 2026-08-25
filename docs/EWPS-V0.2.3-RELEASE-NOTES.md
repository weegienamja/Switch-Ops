# EWPS v0.2.3 Alpha — Scenario Phase Provenance

**Experimental pre-release:** `ewps-v0.2.3-alpha`

EWPS v0.2.3 is a narrowly scoped provenance and instrumentation correction on
the `research/ewps-v0.2` line. The EWPS mathematical model remains `0.2.0`.

## Reproducible controlled phases

New controlled experiments start from a backend-owned phase-0 snapshot. Every
new schema-v4 measurement records the authoritative scenario ID, phase index,
phase ID, lab instance, and both paths' requested/applied fixed profile IDs and
normalized netem parameters.

An operator phase advance is serialized between probe cycles. The backend
applies only fixed allowlisted profiles in the owned EWPS gateway namespaces,
queries `tc -j qdisc`, normalizes the kernel-reported state, and appends one
immutable `SCENARIO_PHASE_CHANGED` event only when verification passes. A
mismatch appends `SCENARIO_PHASE_APPLY_FAILED`, attempts a verified rollback,
and leaves the previous phase authoritative.

Schema-v4 JSONL, JSON, and CSV exports carry phase events and phase summaries.
Replay preserves the recorded phase/event timing while recalculating only EWPS
outputs. Phase provenance and cadence instrumentation are excluded from the
deterministic model digest, so existing v0.1/v0.2 replay digests remain stable.

## Explicit non-changes

- No EWPS equations, confidence weights, raw-performance weights, loss
  handling, evidence threshold, or hysteresis changed.
- No impairment profile or scenario progression changed.
- No topology semantics, candidate identity, or controlled-path architecture
  changed.
- No routing or traffic steering was added; the Observatory remains shadow
  mode only.

Phase transitions are now reproducible and auditable research provenance.
