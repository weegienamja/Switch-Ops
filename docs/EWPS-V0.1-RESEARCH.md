# EWPS v0.1 — Evidence-Weighted Path Selection Observatory

Release identifier: `ewps-v0.1.0-alpha`

Model version: `0.1.0`

Operating boundary: **SHADOW MODE — NO ROUTING CHANGES**

EWPS is an experimental model for asking whether a path-selection decision can
be made more reliable when uncertainty in the supporting measurements is
included explicitly in its cost. This release is an observatory, recorder,
replay engine, and deterministic simulator. It is not a routing protocol and
does not claim novelty in the academic literature.

## Research question

> Can path-selection decisions become more reliable when uncertainty in the
> evidence supporting network measurements is explicitly included in routing
> cost?

EWPS v0.1 compares five shadow strategies at every decision point using the
same observation set:

1. lowest current latency;
2. lowest loss, with a deterministic latency tie-break when loss is equal;
3. performance-only v0.1 SLA cost;
4. EWPS without hysteresis; and
5. EWPS with hysteresis.

None of these choices is executed. A “switch” is only a recorded
recommendation change.

## Mathematical formulation

### Freshness

For observation age `delta_t` in seconds and volatility decay constant
`lambda`:

```text
F_t = exp(-lambda * delta_t)
```

Missing, negative, or non-finite age is invalid and produces `F_t = 0`.
Freshness is monotonic for non-negative `lambda`.

### Stability

For a positive latency-like metric with rolling mean `mu` and rolling standard
deviation `sigma`:

```text
S_v = 1 / (1 + (sigma / mu)^2)
```

`mu <= 0`, a negative standard deviation, or a missing/non-finite input is
outside this formula’s domain and produces `S_v = 0`. Coefficient of variation
is used only for latency-like positive metrics in v0.1. Packet loss is not put
through this formula: zero loss is valid performance evidence, but its
coefficient of variation would be undefined and conceptually misleading.
Metric-specific stability functions remain an engine boundary for later work.

### Evidence density

```text
D_n = 1 - exp(-k * n)
```

The engine field is named `effective_samples`. v0.1 supplies the count of
successful bounded ICMP replies retained by the rolling window. This preserves
the model boundary for a later `n_eff` that accounts for correlation and
effective independent sample size. Missing, negative, or zero density evidence
produces `D_n = 0`.

### Topology confidence

`T_c` is derived from SwitchOps evidence semantics. The mapping is versioned as
`switchops-evidence-v1/ewps-map-v1`:

| EWPS evidence key | SwitchOps meaning | `T_c` |
|---|---|---:|
| `reciprocal_independent_direct` | Local and peer-side evidence independently confirm a direct relationship | 1.00 |
| `one_sided_direct` | A direct local, CDP, or LLDP observation exists without independent peer-side proof | 0.85 |
| `strong_inference` | Current forwarding evidence strongly infers the relationship | 0.60 |
| `weak_inference` | Evidence is incomplete, expected-only, or weakly inferred | 0.30 |
| `contradictory` | SwitchOps retains conflicting or ambiguous relationship evidence | 0.00 |
| `unknown` | No usable relationship evidence | 0.00 |

The mapping is backend model data, exposed by `/api/ewps/meta`, and is not
hidden in presentation code. Missing hops remain unknown and are never
invented by the Observatory.

### Composite certainty

The simple product is:

```text
P_cert = F_t * S_v * D_n * T_c
```

The configurable weighted geometric form follows the v0.1 research definition
without implicit normalization:

```text
P_cert = F_t^w_f * S_v^w_s * D_n^w_d * T_c^w_t
```

The complete weight vector is stored with every experiment. At least one
weight must be positive.

**`P_cert` is a dimensionless evidence-confidence index. It is not a
statistically calibrated probability of correctness.** The component
functions and topology scores are explicit heuristics whose utility must be
evaluated experimentally.

### Raw and EWPS cost

The v0.1 performance-only raw cost is:

```text
C_raw = latency_weight * latency_ms
      + jitter_weight * jitter_ms
      + loss_weight * loss_pct
```

EWPS applies the uncertainty penalty:

```text
C_EWPS = C_raw / P_cert^alpha
```

`alpha` is risk aversion. `alpha = 0` makes the eligible EWPS cost exactly the
raw cost. Eligibility remains a separate gate: if `P_cert < P_min`, required
telemetry is missing, topology evidence is unusable, or a computed cost is
non-finite, the path is `INELIGIBLE`. The API and UI use `null`/an em dash for
unavailable cost and never display infinity.

## Default model configuration

| Parameter | Default |
|---|---:|
| `lambda` | 0.035 |
| `k` | 0.35 |
| `alpha` | 1.0 |
| `P_min` | 0.25 |
| certainty form | simple product |
| component weights | 1.0 each |
| latency weight | 1.0 |
| jitter weight | 0.5 |
| loss weight | 10.0 |
| sample interval | 5 seconds |
| ICMP probes per observation | 3 |
| rolling window | 12 observations |
| minimum relative improvement | 0.08 |
| minimum dwell time | 30 seconds |
| minimum eligible-evidence duration | 15 seconds |
| recovery hold-down | 20 seconds |

All values are stored in the session record rather than inherited silently
from later application defaults.

## Hysteresis

EWPS with hysteresis maintains a recommendation state independently of the
instantaneous EWPS choice. A challenger is suppressed when the first
applicable condition is not met:

- eligible evidence has not persisted for the minimum evidence duration;
- the current recommendation has not met minimum dwell time;
- a recovered challenger remains within recovery hold-down; or
- relative cost improvement is below the configured threshold.

Every suppression records the challenger, retained recommendation,
deterministic reason, and blocker code. An accepted change still records
`switch_blocked_by = shadow_mode`: it is a recommendation, never an action.

## Live measurement method

EWPS discovers active, non-loopback IPv4 adapters already visible to
SwitchOps. A candidate path is an opaque SHA-256-derived path ID. Its source IP
is held only in process memory so the operating system can bind a bounded ICMP
probe to that adapter; the clear address is not written to the research
database or export.

Each observation uses a fixed IP target embedded in code. There is no API field
for a hostname, URL, command, or arbitrary target. Windows uses a fixed
`ping.exe` argument array with `-S`; other supported development hosts use a
fixed `ping` array with `-I`. `shell=False`, a maximum of five requests, and a
short timeout are enforced. EWPS does not run traceroute continuously.

The recorded telemetry can include:

- latency, jitter, loss, reachability, and observation age;
- rolling mean, rolling standard deviation, and retained effective sample
  count;
- aggregate interface packet/error/drop counters where `psutil` exposes them;
  and
- topology confidence derived from the current SwitchOps evidence model.

No application payload is captured. Workload context is an operator-selected
label such as `YouTube 4K`, `Netflix`, `Amazon Prime Video`, `Large download`,
`Normal browsing`, or `Idle baseline`.

## Experiment method

For a comparative live experiment:

1. make two or more independently measurable adapters/paths active;
2. keep the model configuration fixed for the session;
3. choose a workload label before starting;
4. allow the idle baseline and workload phases enough time to exceed rolling
   windows and hysteresis durations;
5. stop the session rather than closing the process during collection;
6. preserve the original database and export a privacy-safe bundle; and
7. compare recommendation changes, disagreement, ineligibility, stale
   evidence, instability, and suppression—not just average EWPS cost.

A one-path session is allowed so an operator can validate telemetry and
evidence behavior, but it cannot support a comparative path-selection result.

## Persistence and reproducibility

Runtime locations in a packaged Windows application are:

```text
%LOCALAPPDATA%\SwitchOps\data\ewps-research.sqlite3
%LOCALAPPDATA%\SwitchOps\data\ewps-exports\
```

Source runs use `backend/data/` under the checkout unless
`SWITCHOPS_DATA_ROOT` is set.

SQLite decision and observation rows are append-only and protected by triggers
against update or deletion. Session lifecycle fields may transition from
created to running/paused/completed. Each material calculation stores the raw
measurement, evidence inputs, certainty components, raw/EWPS costs,
eligibility, competing choices, hysteresis state, events, explanation, model
version, and exact session configuration.

Replay reads the immutable recorded inputs and feeds them through the same
`EWPSDecisionEngine` used by live collection. Repeating replay with the same
configuration yields the same ordered decisions and SHA-256 deterministic
digest. Parameter overrides create an in-memory counterfactual replay; they do
not modify the original session or measurements.

Exports are available as JSONL, JSON, and CSV. Export records omit the local
experiment name and adapter display name, use opaque path IDs, and never
contain the source IP or fixed target address.

## Privacy and safety boundary

EWPS v0.1:

- binds through the existing loopback-only FastAPI sidecar;
- does not add an arbitrary shell, command, or target endpoint;
- cannot call host or Cisco route-changing functions;
- cannot modify routes, metrics, interfaces, policy, QoS, tunnels, firewall,
  DNS, Meraki state, or Change Assurance authority;
- uses bounded, fixed-argument subprocesses only for ICMP measurement;
- does not inspect or retain payloads, TLS data, URLs, titles, account data,
  cookies, credentials, DNS history, or personally identifying traffic
  content; and
- keeps research databases, exports, binaries, and checksums ignored by Git.

## Deterministic simulator

The simulator covers fast/stable, high variance, stale evidence, sparse
evidence, telemetry failure, topology degradation, sudden loss, recovery,
repeated raw crossings, conventional latency flapping with stable EWPS plus
hysteresis, and a slower path winning because faster-path evidence is
insufficient. Simulator observations use the same calculation and decision
engine as live observations.

## Assumptions and limitations

- The confidence functions and topology mapping are heuristics, not learned or
  statistically calibrated quantities.
- The product form treats components as separable. Correlation among freshness,
  variance, sampling, and topology evidence can make the product overconfident
  or overly punitive.
- v0.1 uses successful retained replies as `effective_samples`; it does not yet
  estimate independent `n_eff` under temporal autocorrelation.
- ICMP treatment can differ from streaming traffic treatment. A fixed probe
  target is a path indicator, not proof of application quality or identical
  forwarding for a video service.
- Source-bound probes compare active local egress adapters. They do not create
  routes or force an otherwise inactive route into service.
- A host with only one active path can test collection and replay but cannot
  answer the comparative research question from that session.
- Interface counters are cumulative and can reflect traffic unrelated to the
  manually labelled workload.
- The v0.1 raw cost weights and hysteresis defaults are hypotheses to test, not
  production recommendations.
- Topology confidence can change when SwitchOps evidence ages or conflicts; it
  should not be interpreted as a service-availability probability.

## Interpreting results

EWPS performing differently from lowest latency is not by itself success. A
useful outcome requires examining whether disagreement reduced avoidable
flapping or failure exposure without causing excessive ineligibility or
retaining a materially worse path. Hysteresis suppressions should be examined
alongside crossings. Replay with alternate parameters is exploratory; the
original configuration remains the primary evidence for the recorded session.
