# EWPS v0.2 Alpha — Controlled Dual-Path Experimentation

**Experimental pre-release:** `ewps-v0.2.0-alpha`

EWPS v0.2 is a separate shadow-mode networking research release. It is not
SwitchOps v0.9, is not merged into `main`, and does not steer real application
traffic.

## Highlights

- explicit **SHADOW MODE — RECOMMENDATIONS ONLY** boundary;
- operator-started, fully removable WSL2 dual-path namespace laboratory with
  ten fixed impairment profiles and five repeatable scenarios;
- separated performance evidence confidence and topology confidence;
- bounded topology penalty and explicit structural-conflict handling;
- corrected validation-time freshness semantics and collection-duration data;
- slower evidence-density saturation (`k = 0.08`);
- five-probe cycles with raw outcomes and a bounded 50-outcome rolling-loss
  estimate;
- candidate lifecycle classification separating persistent unavailability,
  transient failures, and recovery;
- versioned schema, richer dual-path summaries, deterministic v0.2 replay, and
  unchanged v0.1 replay semantics;
- simulator controls that include agreement, slower-path preference, v0.1
  calibration, and deliberately adverse EWPS behavior; and
- explicit export success/error popup with exact selectable local path plus a
  narrowly allowlisted desktop **Open export folder** action.

Controlled logical paths do not demonstrate physical, ISP, or independent
failure-domain diversity. EWPS does not change Windows routes, production
interfaces, network devices, DNS, firewalls, or normal browser/application
routing. See [EWPS-V0.2-RESEARCH.md](EWPS-V0.2-RESEARCH.md) for the formulas,
methodology, safety boundary, scenario instructions, and limitations.
