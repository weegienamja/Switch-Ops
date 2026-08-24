# EWPS v0.1 Alpha — Evidence-Weighted Path Selection Observatory

**Experimental pre-release:** `ewps-v0.1.0-alpha`

EWPS v0.1 is a separate networking research release built from the SwitchOps
v0.8 development baseline. It does not change the normal SwitchOps roadmap or
version and is not SwitchOps v0.9.

## Included

- shadow mode only; no live routing changes;
- versioned experimental EWPS mathematical engine;
- real, bounded, source-bound ICMP telemetry and aggregate interface counters;
- explicit evidence freshness, stability, density, and topology confidence;
- conventional algorithm comparison plus EWPS with and without hysteresis;
- persistent local SQLite experiment logging;
- privacy-safe JSONL, JSON, and CSV export;
- deterministic replay with optional counterfactual parameters;
- deterministic scenario simulator using the live engine;
- live EWPS Observatory with cost/confidence timelines, topology evidence,
  events, disagreement, explanations, and completed-session summaries; and
- Windows x64 desktop artifacts when packaging prerequisites are available.

## Safety boundary

EWPS records what each strategy would choose. It cannot modify Windows or Cisco
routes, routing metrics, interfaces, QoS, tunnels, firewall rules, DNS,
Meraki state, or Change Assurance permissions. It does not inspect application
payloads or TLS and does not retain URLs, video titles, account data, cookies,
credentials, packet payloads, or DNS browsing history.

## Research qualification

`P_cert` is a dimensionless evidence-confidence index, not a calibrated
probability. Its component functions and default values are explicit
heuristics. See [EWPS-V0.1-RESEARCH.md](EWPS-V0.1-RESEARCH.md) for assumptions,
limitations, methodology, reproduction, and interpretation.
