# SwitchOps Roadmap

SwitchOps is evolving from a local Catalyst operations tool into an evidence-first
network assurance workstation. The goal is not to reproduce Cisco Catalyst
Center, Meraki Dashboard, SD-WAN Manager, ThousandEyes, or any other controller.
The goal is to use the same architectural ideas that make modern network
operations reliable, while keeping SwitchOps local, conservative, explainable,
and useful on networks where only partial evidence is available.

This roadmap is directional. A capability is not considered shipped until the
implementation, tests, supported hardware, packaging, and release artifacts are
publicly validated. Cisco Validated Designs are used as architectural research
and inspiration, not as a claim that SwitchOps or a SwitchOps-managed network is
Cisco Validated, Cisco certified, or compliant with a particular CVD.

## Principles that apply to every milestone

1. **Evidence before inference.** Observed facts, operator intent, inference, and
   history remain separate. Unknown remains a valid answer.
2. **Read-only first.** New discovery, assurance, topology, protocol, security,
   path, and lifecycle capabilities begin as observation and analysis features.
3. **No arbitrary CLI.** Device interaction remains a fixed, typed, symbolic
   command surface. New protocol support does not create a generic command
   channel.
4. **Discovery never grants authority.** Evidence may warn or block a change,
   but only explicit local policy can authorize a bounded write.
5. **Verification is independent of intent.** Configuration presence is not proof
   of operational success. Where practical, SwitchOps must verify outcomes using
   separate observations.
6. **Uncertainty is visible.** Path, identity, topology, role, health, and policy
   conclusions carry provenance, freshness, and confidence rather than being
   silently converted into facts.
7. **Local control remains the default.** Cloud integrations, if added, are
   optional evidence providers. SwitchOps must remain useful without a hosted
   SwitchOps control plane.
8. **Safety gates survive future automation.** AI, workflows, profiles, or CVD
   knowledge must never bypass the existing write lock, interface policy,
   preflight, backup, verification, audit, rollback, or explicit startup-save
   boundaries.

## Roadmap at a glance

| Milestone | Theme | Primary outcome |
| --- | --- | --- |
| v0.5 | Discovery & Identity | Establish what exists, what it might be, and why SwitchOps believes it |
| v0.6 | Change Assurance | Explain and verify bounded interface changes before and after execution |
| v0.7 | Unified Lab | Reconcile Catalyst and Meraki evidence into one provider-neutral topology |
| v0.8 | Path Intelligence | Model how traffic can move through the observed topology and whether a path is healthy |
| v0.9 | Resilience Lab | Simulate failures, identify single points of failure, and explain alternate-path evidence |
| v1.0 | Intent Assurance | Compare observed behavior with explicit operator requirements and CVD-inspired design profiles |
| v1.1 | Experience Assurance | Correlate path health, synthetic tests, and application/service objectives |
| v1.2 | Lifecycle & Readiness | Add software, configuration, maintenance, and change-readiness assurance |
| v1.3 | Multi-Domain Context | Normalize context from additional Cisco domains without flattening provenance |
| v1.x | Advanced Fabric Awareness | Read-only verification for modern routing, segmentation, and fabric capabilities |
| Long term | Assisted Operations | Evidence-grounded troubleshooting and planning with human-controlled execution |

---

## v0.7 - Unified Lab

### Objective

Move from a single-switch mental model to a provider-neutral view of the local
network. Catalyst CLI evidence and Meraki evidence should describe the same
physical and logical environment without one source automatically overruling the
other.

### Core capabilities

- Collect Catalyst and Meraki observations through separate provider adapters.
- Reconcile devices, interfaces, neighbors, uplinks, and endpoint attachment
  conservatively.
- Preserve contradictions instead of silently selecting a preferred source.
- Show provider provenance in the inspector for every reconciled claim.
- Keep Observed, Reconciled, and Expected topology authority distinct.
- Allow the visible topology to change as live evidence changes rather than
  preserving a static lab diagram.
- Keep all Meraki collection read-only in this milestone.

### Architectural requirement

The provider-neutral evidence model introduced here becomes the base for every
later path, resilience, policy, and multi-domain feature. Future assurance must
reason over normalized claims while preserving the original source records.

---

## v0.8 - Path Intelligence

### Objective

Answer a question SwitchOps cannot answer from a node-and-port topology alone:

> How can traffic move between these two points, what evidence supports that
> path, and is the path operationally healthy?

Cisco's recent mission-critical SRv6 and SD-WAN designs repeatedly separate
simple reachability from path quality. They measure loss, latency, jitter,
liveness, transport eligibility, and application requirements rather than
assuming that an `up` interface is a good path. SwitchOps should adopt that
operational model without requiring SRv6 or SD-WAN.

### New path model

Introduce provider-neutral concepts such as:

- `PathEndpoint`
- `PathSegment`
- `PathObservation`
- `LinkObservation`
- `PerformanceSample`
- `PathConstraint`
- `FailureDomain`
- `PathConfidence`

A path is derived only from current or explicitly permitted aging evidence. An
unknown hop, inferred relationship, or ambiguous attachment must remain visible
as uncertainty rather than being silently bridged.

### Path inspector

Allow an operator to select two known endpoints and inspect:

- observed hops and interfaces;
- VLAN or routed-boundary evidence where available;
- link speed and duplex;
- interface error counters;
- utilization where the platform exposes it safely;
- latency, loss, and jitter from available probes;
- trust or transport tags supplied by explicit operator intent;
- provider provenance for each segment;
- freshness and confidence;
- known bottlenecks;
- known single points of failure;
- whether an alternate observed path exists.

### Hard failure vs soft degradation

Formalize two separate health classes.

**Hard failure** includes evidence such as:

- interface down;
- device unreachable;
- adjacency or neighbor loss;
- probe liveness failure;
- upstream gateway loss.

**Soft degradation** includes evidence such as:

- rising latency;
- packet loss;
- jitter;
- interface errors;
- congestion or sustained utilization;
- path quality outside an operator-defined objective.

A path can therefore be `REACHABLE` and `DEGRADED` at the same time.

### Baselines

Store local rolling baselines for measurable properties when enough samples
exist. Baselines must be descriptive rather than predictive in the first
version. SwitchOps should be able to show that a value changed materially from
its recent history without claiming a root cause it cannot prove.

### Safety boundary

v0.8 observes and explains paths. It does not steer traffic, change routing
metrics, create policy, or alter QoS.

### Exit criteria

- A path explanation is reproducible from stored evidence.
- Every segment has provenance and freshness.
- Unknown or ambiguous segments are not presented as confirmed.
- Link-up and path-healthy are separate states.
- No path-analysis result can authorize a write.

---

## v0.9 - Resilience Lab

### Objective

Turn the observed topology into a conservative counterfactual model that can
answer:

> What is likely to lose connectivity if this device, interface, or path fails?

This borrows from the reasoning behind TI-LFA, redundant branch design, VXLAN
backup paths, and Cisco's current SD-WAN/Starlink validation guidance. SwitchOps
will not implement forwarding repair. It will model failure impact from the
evidence it has.

### Failure simulation

Add non-destructive simulations for:

- interface loss;
- device loss;
- uplink loss;
- gateway loss;
- one member of a redundant pair or stack disappearing;
- a transport becoming unavailable;
- a path remaining physically up but becoming performance-ineligible.

The simulation engine removes or degrades selected evidence in memory and then
recomputes reachability. It never changes the real device.

### Blast-radius analysis

For each simulated failure, report:

- directly affected entities;
- entities learned behind the failed component;
- likely upstream isolation;
- alternate observed paths;
- path-confidence changes;
- whether redundancy is proven, inferred, unknown, or absent;
- whether the management/control path itself is at risk.

### Single-point-of-failure detection

Identify topology elements whose removal disconnects currently observed parts of
the graph. Avoid generic risk scores. Report concrete graph consequences and the
evidence used to establish them.

### Separate failure and recovery tests

Cisco's current multi-transport designs explicitly test physical loss, upstream
impairment, policy failure, failover, and failback as different cases. SwitchOps
should preserve this distinction. Recovery to the original path should be
validated separately from successful failover.

### Failure domains

Allow explicit local grouping of components into failure domains such as:

- same switch stack;
- same router pair;
- same power source;
- same ISP or transport;
- same physical uplink;
- same site.

SwitchOps must not infer shared physical risk merely because two interfaces are
near each other in the topology.

### UI concept

A topology node or edge can expose **Simulate failure**. The result should be an
explainable impact panel, not an animation that implies packet-level certainty.

### Exit criteria

- Simulations are deterministic for the same evidence snapshot.
- No simulated result is written into current observed state.
- Alternate paths are claimed only when the graph contains supporting evidence.
- Failure-domain assumptions are operator-authored or explicitly sourced.

---

## v1.0 - Intent Assurance

### Objective

Move from "what is the network doing?" to:

> Is the observed network satisfying the requirements the operator actually
> cares about?

Cisco's current architectures increasingly combine identity, policy, telemetry,
segmentation, path constraints, and assurance. SwitchOps should represent intent
as a separate local data model and reconcile it against observation.

### Declarative local intent

Add a human-readable, versionable intent model for requirements such as:

- expected device role;
- expected neighbor or attachment;
- expected VLAN or segment membership;
- management-path expectations;
- minimum uplink redundancy;
- permitted or prohibited transport classes;
- trust-zone membership;
- path latency, loss, or jitter objectives;
- required PoE behavior;
- required security capability presence;
- expected software family or version policy;
- maintenance constraints.

Intent describes desired outcomes, not raw IOS configuration snippets.

### Assurance outcomes

Use explicit states such as:

- `ALIGNED`
- `DRIFT`
- `VIOLATED`
- `INSUFFICIENT_EVIDENCE`
- `NOT_APPLICABLE`
- `STALE`

Do not report `ALIGNED` when SwitchOps lacks the observations required to test a
requirement.

### CVD-inspired reference profiles

Introduce optional reference profiles for common architectures, initially as
read-only design-review templates. Examples could include:

- single-uplink small site;
- dual-uplink resilient site;
- Catalyst access switch plus Meraki security appliance and AP;
- redundant distribution/access topology;
- segmented guest and corporate access;
- management-plane separation;
- multi-transport branch.

Profiles may encode general design principles derived from public Cisco design
guidance, but SwitchOps must label them **CVD-inspired**, not Cisco Validated.
They are an operator aid, not a certification engine.

### Design review

For a selected profile, report concrete findings such as:

- "Only one upstream path is currently observed."
- "Expected redundant uplink is not observed."
- "Two links marked diverse terminate in the same declared failure domain."
- "Observed STP mode differs between switches where a common mode is expected."
- "Management path depends on the interface selected for a disruptive change."

### Change assurance integration

Existing preflight can consume intent as additional blocking or warning evidence,
but intent cannot make an `UNMANAGED` interface operable. Write authorization
remains exclusively under the existing interface policy and session gates.

### Exit criteria

- Intent is stored separately from observations.
- Every assurance conclusion names the tested requirement and evidence.
- Missing evidence never becomes compliance.
- Reference profiles never imply Cisco endorsement or certification.

---

## v1.1 - Experience & Application Assurance

### Objective

Close the loop between topology health and service experience.

Cisco's application-aware SRv6 work separates three questions:

1. What is the traffic or service?
2. What path or SLA should it receive?
3. Did the resulting experience actually meet the objective?

SwitchOps can adopt the same assurance loop without initially performing traffic
steering.

### Synthetic tests

Start with safe tests originated from the local SwitchOps host, for example:

- ICMP reachability where permitted;
- TCP connection establishment;
- DNS resolution;
- HTTP or HTTPS response timing;
- repeated latency and loss sampling.

Device-hosted probes can be considered later only on specifically validated
platforms through typed symbolic operations. There must be no generic remote
shell or arbitrary probe-command interface.

### Service objectives

Allow operators to define service classes such as:

- real-time collaboration;
- business-critical SaaS;
- bulk transfer;
- management;
- best effort.

Each class can have measurable expectations for latency, jitter, loss,
availability, or response time. These classes are local SwitchOps intent and do
not need SRv6 colors, SD-WAN policy, or NBAR to be useful.

### Application evidence

Where supported by a device and licensing model, SwitchOps may later ingest
read-only application classification or flow metadata such as NBAR/NetFlow. It
must preserve source confidence and must never decrypt payloads to identify
applications.

### Closed-loop incident timeline

Correlate:

- synthetic-test failures;
- interface state;
- error counters;
- utilization changes;
- path changes;
- neighbor loss;
- Meraki events;
- bounded change sessions;
- recovery.

The result should show temporal correlation without claiming causation unless
there is direct evidence.

### Exit criteria

- Service health is independently measurable from configuration state.
- Test results retain target, method, timestamp, and source.
- Correlation is clearly distinguished from proven cause.

---

## v1.2 - Lifecycle & Readiness

### Objective

Extend the evidence-before-change model from interface operations to software and
maintenance planning.

Cisco Software Image Management uses a desired "golden" image, inventory
compliance, readiness checks, scheduled activation, and post-change verification.
SwitchOps can apply the same lifecycle discipline without becoming a firmware
orchestrator immediately.

### Software inventory

Collect and retain non-secret facts such as:

- platform/model;
- software family and version;
- boot variables where safely available;
- uptime and reload reason where supported;
- stack/member software consistency;
- image filename and storage evidence where supported.

### Desired software baseline

Allow an operator to define a local approved or preferred software baseline per
validated device family. The baseline is advisory unless a future release adds a
separately reviewed software operation workflow.

Possible states:

- `MATCHES_BASELINE`
- `UPDATE_AVAILABLE_BY_POLICY`
- `BASELINE_UNKNOWN`
- `PLATFORM_UNVALIDATED`
- `INSUFFICIENT_EVIDENCE`

SwitchOps must not claim Cisco-recommended software merely because a version is
newer.

### Readiness report

Before a future software change is even considered, assess evidence for:

- platform compatibility;
- available storage;
- boot configuration;
- stack consistency;
- current topology redundancy;
- management-path survival;
- configuration backup availability;
- running/startup divergence;
- active warnings or degraded paths;
- maintenance-window intent.

### Configuration lifecycle

Expand existing configuration history into explicit drift views:

- running vs startup;
- current vs accepted local baseline;
- declared intent vs current configuration-derived evidence;
- changes correlated with an audited SwitchOps session vs changes from outside
  SwitchOps.

External changes should be recorded as observed drift, not attributed to an
actor SwitchOps cannot identify.

### Safety boundary

Initial v1.2 scope is inventory, readiness, and verification. Firmware transfer,
activation, reload, and automated scheduling require a separate future design
review because their blast radius is substantially larger than existing bounded
interface operations.

---

## v1.3 - Multi-Domain Context

### Objective

Generalize the v0.7 provider-neutral model beyond Catalyst and Meraki.

Cisco's Common Policy architecture uses a hub to learn context, normalize it,
and share it across different enforcement domains. SwitchOps should borrow the
normalization principle while preserving its own evidence-first rules.

### Candidate read-only providers

Subject to API availability, licensing, and safe local credential handling:

- Cisco ISE;
- Catalyst Center;
- Catalyst SD-WAN Manager;
- additional Meraki APIs;
- ACI/APIC;
- Secure Firewall/FMC;
- ThousandEyes.

No provider is required for the core application.

### Context graph

Normalize useful claims such as:

- endpoint identity;
- IP/MAC bindings;
- device role;
- site;
- security group or policy tag;
- segment/VRF;
- application or flow classification;
- WAN transport;
- path/experience telemetry;
- policy enforcement point.

### Preserve semantics, do not flatten them

An ISE identity claim, a Meraki client record, a Catalyst MAC-table entry, and an
operator-authored expected device are different types of evidence. They may
refer to the same entity without becoming interchangeable facts.

### Cross-domain contradictions

Surface cases such as:

- ISE classifies an endpoint differently from local operator intent;
- Meraki believes a client is attached through one device while Catalyst
  evidence suggests another path;
- a security tag exists in one domain but is missing at an expected enforcement
  point;
- WAN path health is degraded while LAN interfaces remain healthy.

### Safety boundary

v1.3 is read-only federation. External context can enrich, warn, or block local
plans, but it cannot automatically grant local write authority.

---

## v1.x - Advanced Fabric Awareness

### Objective

Make SwitchOps useful on newer Cisco platforms without abandoning older Catalyst
support or pretending unsupported features exist.

### Capability discovery

Rather than showing fixed protocol pages, discover platform capabilities and
surface only what can be evidenced. Candidate modules include:

- STP/MST/RSTP topology and root consistency;
- EtherChannel and stack redundancy;
- OSPF, IS-IS, and BGP adjacency health;
- FHRP state;
- BFD liveness;
- IP SLA and tracked-object state;
- VRF and segmentation inventory;
- SD-WAN transport and path state;
- NBAR/application visibility;
- SRv6 locators and policies;
- Flex-Algo and affinity constraints;
- TI-LFA readiness/state;
- MACsec/IPsec capability and operational posture;
- VXLAN/EVPN control-plane and VTEP awareness.

### Protocol verification cards

Each supported feature should answer four questions:

1. **Capability:** Does the platform support or expose the feature?
2. **Configuration:** Is relevant configuration present?
3. **Operation:** Is there evidence that the feature is actually active?
4. **Assurance:** Is observed behavior satisfying local intent?

Configuration alone must never be treated as operational proof.

### SRv6-specific direction

The mission-critical SRv6 CVD provides useful concepts even before SwitchOps has
SRv6 hardware:

- path constraints and link affinities;
- performance measurement;
- fast-reroute awareness;
- secure vs untrusted transport classification;
- application-specific SLA intent;
- cryptographic posture.

Initial SRv6 support should therefore be read-only evidence and verification.
SwitchOps should not become an SRv6 policy authoring tool until there is a
separate safety design, modern validated hardware, and exhaustive lab coverage.

---

## Long-term - Assisted Operations

### Objective

Use automation or AI to reduce operator effort without converting probabilistic
reasoning into device authority.

Cisco's current AgenticOps direction combines cross-domain telemetry, reasoning,
troubleshooting, and validation. A SwitchOps interpretation should remain much
more constrained:

- summarize evidence;
- explain contradictions;
- rank deterministic troubleshooting checks;
- construct an incident evidence pack;
- suggest a bounded change plan from the existing symbolic catalog;
- explain why a plan is blocked;
- compare before/after evidence;
- draft a human-readable incident or change report.

### Non-negotiable AI boundary

An AI component must never:

- emit arbitrary IOS into an execution path;
- create new command authority;
- change interface policy;
- unlock writes;
- skip preflight;
- bypass operator confirmation;
- turn low-confidence discovery into write authorization;
- auto-save startup configuration.

If AI is introduced, deterministic backend policy remains the authority and AI
remains an advisory consumer of typed evidence and typed tools.

---

# Cisco public design research used for inspiration

Reviewed 24 August 2026. These sources are architectural references, not product
requirements.

## Cisco Validated program

**Source:** [Cisco Validated: Design, Deploy, and Operate](https://www.cisco.com/site/us/en/solutions/cisco-validated/index.html)

Useful idea for SwitchOps: validated designs consistently separate architecture,
deployment, and operation. SwitchOps should do the same internally: model intent,
collect operational proof, and validate outcomes instead of equating successful
configuration with a healthy network.

## Cisco Secure Networking Reference Architecture

**Source:** [Introduction to the Cisco Secure Networking Architecture](https://www.cisco.com/c/en/us/td/docs/solutions/CVD/Campus/Intro-Cisco-Secure-Network-Architecture.html)

Useful ideas:

- common context across networking and security;
- identity-aware policy;
- segmentation;
- telemetry and observability as architectural primitives;
- unified management and lifecycle operations;
- cloud-managed, on-premises, and programmable operating models sharing the
  same underlying intent.

SwitchOps adaptation: keep identity, topology, segmentation, policy, telemetry,
and assurance as related but separate evidence domains.

## Cisco Unified Branch and Branch as Code

**Sources:**

- [Cisco Unified Branch Design Guide](https://www.cisco.com/c/en/us/td/docs/solutions/CVD/Campus/cisco_unified_branch_design_guide.html)
- [Unified Branch Overview - Network as Code](https://netascode.cisco.com/docs/guides/branch/01_overview/)
- [Branch as Code explained](https://netascode.cisco.com/docs/guides/branch/04_bac-explained/)

Useful ideas:

- full-stack topology instead of isolated device management;
- small, medium, and large reference architectures;
- declarative desired state;
- reusable validated templates;
- version control, testing, validation, and rollback;
- lifecycle automation built from design intent rather than hand-entered CLI.

SwitchOps adaptation: CVD-inspired local intent profiles and repeatable assurance
checks, while keeping writes bounded and separately authorized.

## Application-Aware Path Selection in SRv6 Networks

**Source:** [Application-Aware Path Selection in SRv6 Networks](https://www.cisco.com/c/en/us/solutions/collateral/design-zone/cisco-validated-profiles/CVD-Application-Aware-Routing-in-SRv6.html)

Useful ideas:

- distinguish traffic identity from QoS markings;
- bind services to measurable SLA requirements;
- evaluate paths using loss, latency, and jitter;
- validate the result independently with synthetic tests and path visibility;
- treat assurance as a closed loop from intent to observed outcome.

SwitchOps adaptation: path health, service objectives, synthetic testing, and an
incident timeline that can show whether a service objective was actually met.

## Quantum-Safe SRv6 Fabric for Mission-Critical Networks

**Source:** [Quantum-Safe SRv6 Fabric for Mission-Critical Networks](https://www.cisco.com/c/en/us/td/docs/solutions/CVD/Campus/SRv6_Fabric-Mission-Critical_Networks.html)

Useful ideas:

- simple core with richer edge intelligence;
- hard failure vs soft performance degradation;
- path constraints and affinities;
- performance measurement;
- precomputed repair-path thinking;
- transport trust classification;
- explicit cryptographic posture.

SwitchOps adaptation: Path Intelligence, Resilience Lab, failure-domain
reasoning, and later read-only SRv6/security capability verification.

## Cisco Catalyst SD-WAN design guidance

**Source:** [Cisco Catalyst SD-WAN Design Guide](https://www.cisco.com/c/en/us/td/docs/solutions/CVD/SDWAN/cisco-sdwan-design-guide.html)

Useful ideas:

- application-aware routing based on measured SLA characteristics;
- separate blackout detection from performance-quality measurements;
- real-time path metrics;
- policy intent separated from transport implementation;
- analytics used for troubleshooting and capacity understanding.

SwitchOps adaptation: treat "reachable" and "suitable" as different path states
and make SLA eligibility explainable.

## Cisco SD-WAN Starlink LEO Satellite CVD

**Source:** [Cisco SD-WAN Starlink LEO Satellite CVD](https://www.cisco.com/c/en/us/td/docs/solutions/CVD/Campus/Cisco_SDWAN_Starlink_LEO_Satellite_CVD.html)

Useful ideas:

- establish site-specific performance baselines;
- define path eligibility from measured latency, jitter, and loss;
- validate primary, secondary, dual-active, failover, and failback behavior;
- test physical loss, upstream impairment, and policy failure independently;
- use a layered troubleshooting sequence before changing policy.

SwitchOps adaptation: baseline-driven path health, explicit failure scenarios,
failback verification, and deterministic troubleshooting order.

## Cisco Common Policy Integration Guide

**Source:** [Cisco Common Policy Integration Guide](https://www.cisco.com/c/en/us/td/docs/cloud-systems-management/network-automation-and-management/catalyst-center/cisco-validated-solution-profiles/common-policy-integration-guide.html)

Useful ideas:

- learn contextual data from multiple domains;
- normalize it into a common model;
- retain domain-specific enforcement points;
- validate both context exchange and policy enforcement.

SwitchOps adaptation: a multi-domain context graph where Catalyst, Meraki, ISE,
SD-WAN, ACI, and other sources can contribute claims without losing provenance.

## Campus Software Image Management

**Source:** [Campus Software Image Management Using Cisco DNA Center Deployment Guide](https://www.cisco.com/c/en/us/td/docs/solutions/CVD/Campus/dnac-swim-deployment-guide.html)

Useful ideas:

- desired or golden software baselines;
- inventory compliance checks;
- readiness before activation;
- maintenance-window scheduling;
- explicit post-change verification.

SwitchOps adaptation: software posture and readiness reporting first, with no
automatic firmware deployment until a separate high-blast-radius safety design
exists.

## Cisco Cloud Campus LAN design guidance

**Source:** [Cisco Cloud Campus LAN Design Guide](https://www.cisco.com/c/en/us/solutions/collateral/enterprise/design-zone-campus/cloud-campus-lan-design-guide.html)

Useful ideas:

- monitor-only onboarding can provide useful topology before configuration
  control is granted;
- topology correctness and protocol consistency matter independently from link
  status;
- operational design choices such as STP mode and redundancy need validation
  across multiple devices.

SwitchOps adaptation: continue allowing read-only evidence to become richer than
write authority, and add cross-device topology/protocol consistency checks.

## VXLAN BGP EVPN design guidance

**Source:** [Cisco Nexus 9000 VXLAN BGP EVPN Data Center Fabrics Fundamental Design and Implementation Guide](https://www.cisco.com/c/en/us/td/docs/dcn/whitepapers/cisco-vxlan-bgp-evpn-design-and-implementation-guide.html)

Useful ideas:

- Day-N operations require telemetry, analytics, backups, patching, and security
  checks rather than configuration alone;
- backup paths must be reasoned about in terms of fault isolation and congestion,
  not merely physical existence;
- failure detection and rerouting behavior should be validated explicitly.

SwitchOps adaptation: Resilience Lab should understand failure domains and
alternate-path evidence rather than declaring any second link "redundant".

---

# What SwitchOps should not become

Even as the roadmap grows, SwitchOps should not become:

- an arbitrary Cisco CLI launcher;
- a cloud-mandatory network controller;
- a replacement for Catalyst Center, Meraki Dashboard, SD-WAN Manager,
  ThousandEyes, ISE, or ACI;
- a tool that automatically changes a network because an inferred profile says
  the design is wrong;
- a product that claims a network is Cisco Validated merely because it resembles
  a public CVD;
- an AI agent with independent configuration authority.

The durable differentiator should remain **evidence-backed local network
understanding with conservative, auditable change control**.
