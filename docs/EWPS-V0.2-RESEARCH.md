# EWPS v0.2 — Controlled Dual-Path Experimentation and Model Calibration

Current corrective release identifier: `ewps-v0.2.3-alpha`

The EWPS mathematics and model version remain `0.2.0`. The `0.2.3` release
adds authoritative scenario-phase snapshots, verified immutable transition
events, schema-v4 exports, and phase summaries. It retains the `0.2.2`
start-to-start scheduler correction and the `0.2.1` controlled-lab binding fix.
It does not reinterpret historical observations or alter scoring.

Model version: `0.2.0`

EWPS v0.2 is a separate experimental pre-release. It remains strictly in
**SHADOW MODE — RECOMMENDATIONS ONLY**: its decisions are recorded and
displayed, but cannot alter Windows routing, production interfaces, Cisco or
Meraki state, DNS, firewall policy, or application traffic. Controlled logical
paths are not evidence of physical path diversity, ISP diversity, or
independent failure domains.

## Experiment 001 baseline

The first real v0.1 Amazon Prime Video session remains the baseline. It ran for
about 21.4 minutes and contained 115 decision points and 345 measurements. Only
one detected path was genuinely usable; two candidates were unavailable for
the whole session and accounted for 230 repeated failed observations. The
working path averaged roughly 0.2448 composite v0.1 confidence, was ineligible
24 times, experienced five actual telemetry failures, produced four EWPS state
changes, and had four hysteresis-suppressed changes. A one-path observation
could not produce meaningful path-selection disagreement.

The raw session and exports are local evidence and are not committed. v0.2
adds only privacy-safe mathematical fixtures. Existing `ewps_version = 0.1.0`
rows are parsed by the unchanged v0.1 models and replayed through the unchanged
v0.1 engine. Schema migration is additive and does not rewrite observations.

## Why confidence is separated

v0.1 multiplied topology confidence into the same composite as directly
measured telemetry. Experiment 001 showed that weak intermediate-topology
inference could make a healthy end-to-end measurement knife-edge eligible.
v0.2 therefore assigns different authority to two different questions:

- performance evidence confidence, `P_perf`, describes how fresh, stable, and
  dense the measured performance estimate is; and
- topology confidence, `T_c`, describes structural evidence and is recorded
  separately.

Neither number is a calibrated probability.

With normalized defaults `w_f = 0.40`, `w_s = 0.35`, and `w_d = 0.25`:

```text
F_t    = exp(-lambda * age_seconds)
S_v    = 1 / (1 + (standard_deviation / mean)^2)
D_n    = 1 - exp(-k * effective_samples)
P_perf = F_t^w_f * S_v^w_s * D_n^w_d

topology_penalty = 1 + beta * (1 - T_c)
C_EWPS = (C_raw / P_perf^alpha) * topology_penalty
```

`alpha` is performance-uncertainty aversion. `beta` bounds the penalty from
uncertain structure. `alpha = 0` removes the performance-confidence penalty;
`beta = 0` removes the topology penalty. Weak or unknown topology remains
visible and affects cost, but does not by itself invalidate directly measured
telemetry. Explicit contradictory topology evidence is structurally
ineligible. A lack of diversity proof prevents diversity claims.

## Eligibility and candidate lifecycle

The default `P_perf_min` is `0.50`. A path is eligible only when routing metrics
are usable, required metrics are valid, effective evidence exists,
`P_perf >= P_perf_min`, and no explicit topology conflict exists. The recorder
retains the distinct states `PERFORMANCE_EVIDENCE_INSUFFICIENT`,
`ELIGIBLE_TOPOLOGY_UNKNOWN`, `ELIGIBLE_TOPOLOGY_WEAK`, `TOPOLOGY_CONFLICT`,
`TELEMETRY_UNAVAILABLE`, and `UNREACHABLE` rather than flattening them.

Candidates move through `PROBING`, `VIABLE`, `PERSISTENTLY_UNAVAILABLE`,
`RECOVERING`, and `DISABLED`. Three consecutive initial failures classify an
unavailable candidate. It is then reprobed once every six cycles. Unavailable
events, failures after a path was viable, and recovery events are reported
separately; deferred reprobes are not counted as transient telemetry failures.

## Freshness, density, and loss calibration

A successful observation records `collection_started_at`,
`observation_validated_at`, and collection duration. Freshness age is measured
from validation, so a newly completed multi-probe observation normally starts
near `F_t = 1`. When collection fails, no validation timestamp is fabricated;
the last valid rolling evidence ages naturally.

The density default changes from `k = 0.35` to `k = 0.08`. This preserves useful
separation across a larger evidence window. `effective_samples` is deliberately
named so a future model can estimate independent information rather than count
correlated probes.

Each five-probe cycle stores its individual Boolean outcomes and instantaneous
loss for diagnostics. Routing cost uses a bounded 50-outcome rolling window
(roughly ten default cycles), so one missed response contributes 2% after a
full window rather than being treated as authoritative 20% or 33% current
loss. Persistent loss still accumulates a strong penalty; complete failure and
unreachability remain separate signals. The default five probes at a five
second sampling interval are bounded and are not increased merely to smooth
the graph.

## Controlled dual-path laboratory

The Windows application can observe two kinds of candidates. Real-interface
mode binds the fixed probe to each actual source address and independently
validates it; two adapters are not called two ISPs. If only one interface
works, EWPS reports only one viable path.

The optional lab is created only after an operator presses **Create contained
lab**. On a supported WSL2 host it creates four namespaces:

```text
ewps02-src -- veth -- ewps02-gwa -- veth -- ewps02-target  (Path A)
          `-- veth -- ewps02-gwb -- veth --'               (Path B)
```

Each gateway chain has a separate subnet, source binding, target address, and
`tc netem` profile. Fixed namespace-local host routes connect only those lab
addresses. The implementation never adds a Windows route or changes a host or
production adapter. The loopback API accepts only enumerated path, profile,
and scenario names—not commands, addresses, or shell text. Teardown deletes
all four `ewps02-*` namespaces and verifies their absence.

The available profiles are Fast Stable, Slow Stable, Fast Noisy, Moderate
Jitter, Intermittent Loss, Sustained Loss, Telemetry Stale, Temporary Failure,
Recovery, and Crossing Latency. Lab telemetry feeds the same collector,
rolling-loss estimator, v0.2 engine, SQLite recorder, summary, replay, and
export path used by real interfaces.

## Standard methodology and reproduction

1. Install or enable WSL2 with a default Linux distribution containing
   `iproute2`, `tc`, and `ping`. The lab needs non-interactive root inside that
   distribution to create its contained namespaces.
2. Open EWPS Observatory and run **Check prerequisites**.
3. Press **Create contained lab**. Confirm both Path A and Path B say
   independently validated. This proves two separately source-bound controlled
   forwarding chains were observed; it does not prove physical diversity.
4. Select one named scenario and press **Prepare scenario**. Creation and
   preparation do not start an experiment.
5. Create the v0.2 experiment, then explicitly press **Start experiment**.
6. For a multi-phase scenario, press **Advance phase** at controlled times.
7. Stop the experiment, inspect the summary and deterministic replay, and
   export JSONL/JSON/CSV. The popup shows the exact local output path.
8. Stop any lab-backed experiment and press **Remove contained lab**. Confirm
   the UI reports that no `ewps02` namespaces remain.

The five named scenarios are:

- **Conventional agreement:** A is fast, stable, fresh, and dense; agreement is
  expected.
- **Faster but epistemically weak:** A begins faster, then variance and
  staleness are introduced while B remains slower and well evidenced.
  Performance-only logic may prefer A while EWPS may prefer B; the observed
  result must be reported honestly.
- **Raw-metric flapping:** current latency crosses repeatedly; hysteresis is
  expected to suppress some recommendation oscillation.
- **Evidence outage:** validated A observations stop while previous evidence
  ages, allowing freshness and eligibility loss to be observed.
- **Recovery:** evidence returns after failure and passes through recovery and
  hold-down behavior.

The deterministic simulator adds Experiment 001 calibration, a case where
conventional routing is preferable, and an adversarial configuration where
EWPS can make a poor choice. Disagreement is not labelled objectively better.

## Recording, replay, and analysis

Schema v2 stores raw latency/jitter/loss, individual probe outcomes, rolling
metrics, loss sample count, both timestamps, duration, lifecycle and telemetry
state, `F_t`, `S_v`, `D_n`, `P_perf`, `T_c`, topology penalty, raw/EWPS cost,
eligibility state, all strategy choices, and hysteresis decisions. Replay may
vary lambda, k, alpha, beta, normalized weights, threshold, rolling-loss window,
and hysteresis without mutating the original session.

Summaries separate availability, telemetry reliability, and algorithm
decisions. They include usable-path counts over time, unavailable candidates,
unavailability/transient/recovery events, performance and topology confidence
distributions, raw and EWPS cost distributions, overall and pairwise algorithm
disagreement, preference duration, recommendation switches, suppressed
switches, time below threshold, rolling-loss/stale events, EWPS-versus-latency
difference rate, and the weakest evidence component most often associated with
that difference.

## Defaults and limitations

| Parameter | Default |
| --- | ---: |
| lambda | 0.035 |
| k | 0.08 |
| alpha | 1.0 |
| beta | 0.25 |
| P_perf_min | 0.50 |
| freshness / stability / density weights | 0.40 / 0.35 / 0.25 |
| latency / jitter / loss cost weights | 1.0 / 0.5 / 10.0 |
| probes per cycle / sample interval | 5 / 5 seconds |
| latency window / loss-outcome window | 12 / 50 |
| unavailable threshold / reprobe interval | 3 / 6 cycles |

These are explicit research hypotheses, not proof that EWPS is novel,
calibrated, or superior. ICMP may differ from application performance; samples
are temporally correlated; topology mappings are heuristic; WSL scheduling and
`netem` add noise; controlled logical paths share one host; and shadow-mode
recommendations establish neither causal benefit nor safe traffic steering.
A future v0.3 may consider a separately controlled steering experiment only if
v0.2 evidence supports it.
