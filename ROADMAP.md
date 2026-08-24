# SwitchOps Roadmap

SwitchOps is a local, evidence-first network operations and assurance
workstation. It is intended to make partial network evidence understandable and
bounded changes safer; it is not a replacement for Catalyst Center, Meraki
Dashboard, SD-WAN Manager, ThousandEyes, ISE, ACI, or a general-purpose CLI.

This roadmap distinguishes implementation status from release validation. A
feature can be implemented and tested in source while still waiting for real
hardware, interactive acceptance, packaging, or public release gates. Cisco
design guidance may inform the architecture, but SwitchOps does not claim that
it or an observed network is Cisco Validated, Cisco certified, or compliant
with a particular design guide.

## Permanent engineering principles

1. **Evidence before inference.** Observation, inference, operator intent, and
   history remain separate; `UNKNOWN` is a valid result.
2. **Read-only first.** New providers, protocols, topology analysis, and
   assurance capabilities begin as bounded observation paths.
3. **No arbitrary CLI.** Device interaction uses fixed symbolic commands and
   typed parsers. New coverage must not create a generic command channel.
4. **Discovery never grants authority.** Evidence may inform, warn, or block a
   plan, but only explicit local policy can authorize a bounded write.
5. **Independent verification.** Configuration presence is not operational
   proof. Outcomes are verified through separate observations where possible.
6. **Visible uncertainty.** Identity, topology, path, policy, and health claims
   retain source, freshness, confidence, and contradictions.
7. **Local control by default.** Optional cloud APIs are evidence providers;
   SwitchOps has no mandatory hosted control plane.
8. **Safety survives automation.** Workflows and assisted operations cannot
   bypass interface policy, the write lock, preflight, backup, verification,
   audit, rollback, or the separate startup-save boundary.

## Milestones at a glance

| Milestone | Theme | Status |
| --- | --- | --- |
| v0.5 | Discovery & Identity | Complete |
| v0.6 | Change Assurance | Released as `v0.6.0` |
| v0.7 | Unified Lab | Implementation complete; real Meraki validation externally blocked |
| v0.8 | Lab Assurance | Implementation complete; release acceptance remains open |
| v0.9 | Desired State & NetDevOps | Planned |
| v1.0 | Production Hardening / Stable | Planned |
| v1.1 | Experience Assurance | Directional |
| v1.2 | Lifecycle & Readiness | Directional |
| v1.3 | Multi-Domain Context | Directional |
| v1.x | Advanced Fabric Assurance | Long-range |
| Long term | Assisted Operations | Long-range |

---

## v0.5 - Discovery & Identity

**Status: complete.**

v0.5 established the evidence model used by every later milestone:

- observed, inferred, expected, and historical knowledge remain distinct;
- existence, identity, role, relationship, confidence, and freshness are
  separate claims;
- CDP/LLDP can identify a direct neighbour while MAC learning proves only
  reachability behind a port;
- unknown endpoints and contradictory evidence remain first-class;
- stable local entity identifiers preserve continuity without exposing clear
  network identifiers;
- Observed, Reconciled, and Expected views give intent the correct authority.

See [Discovery & Identity](docs/V0.5-DISCOVERY-IDENTITY.md) for the detailed
contract.

## v0.6 - Change Assurance

**Status: released as `v0.6.0`.**

v0.6 wrapped the existing bounded interface operations in durable assurance:

- typed plans target one interface and one allowlisted operation;
- read-only preflight explains topology, attachments, control-path risk,
  rollback availability, and running/startup divergence;
- explicit interface policy and a process-local write lock remain the only
  sources of write authority;
- before/after snapshots separate declared effects from unrelated changes;
- execution performs backup, IOS response classification, direct verification,
  audit, and bounded rollback;
- indeterminate outcomes remain indeterminate;
- saving startup configuration remains separate and explicit.

## v0.7 - Unified Lab

**Status: implementation complete; real Meraki validation externally blocked.**

The source implementation combines Catalyst and Meraki evidence without
turning either provider into unquestioned truth:

- provider records and normalized claims retain source provenance and
  freshness;
- identity, attachment, relationship, availability, name, model, VLAN, and
  port context reconcile independently;
- strong exact identifiers can confirm identity, weaker evidence remains a
  candidate, and strong disagreement merges nothing;
- contradictions remain `AMBIGUOUS` or `CONFLICT` rather than being silently
  overwritten;
- Meraki collection is a fixed read-only API boundary;
- Meraki credentials are accepted only through Windows Credential Manager;
- persistence protects infrastructure identities and minimizes recent-client
  evidence;
- Meraki evidence cannot grant IOS write authority.

The implementation is archived on `develop/v0.7-unified-lab`. It is not a
released milestone: no legitimate Dashboard API key was available for the
required real MX/MR acceptance pass, so onboarding, topology reconciliation,
failure independence, and evidence aging against a real Meraki organization
remain externally blocked. This status must not be represented as a passed
real-Meraki gate.

See [Unified Lab](docs/V0.7-UNIFIED-LAB.md) for the provider and reconciliation
contract.

## v0.8 - Lab Assurance

**Status: implementation complete; release acceptance remains open.**

v0.8 consolidates the previously separate Path Intelligence, Resilience Lab,
and initial capability/fabric-awareness directions into one evidence-backed
Lab Assurance implementation.

### Implemented source scope

- **Capability-driven IOS/IOS-XE collection.** The primary Catalyst and
  explicitly configured secondary devices use a fixed read-only command
  catalog. Support, configuration, observation, unavailable syntax,
  authorization refusal, command failure, transport failure, parser failure,
  successful empty output, and explicit unsupported responses remain distinct.
- **Conservative capability truth.** Capabilities are `SUPPORTED`,
  `UNSUPPORTED`, or `UNKNOWN`; only an explicit feature-unsupported response
  can prove `UNSUPPORTED`. Acceptance records can separately report `PASS`,
  `FAIL`, `NOT CONFIGURED`, `NOT SUPPORTED`, or `NOT EXERCISED`.
- **Evidence-backed topology.** Reciprocal CDP/LLDP can confirm direct physical
  relationships. One-sided discovery stays one-sided. MAC learning remains an
  inferred L2 relationship, ARP/gateway correlation remains bounded, routing
  adjacency remains logical, and port-channel membership is not presented as a
  second physical neighbour.
- **Logical segmentation.** VLANs, access membership, trunks, SVIs, gateway
  nodes, and VRF context form the logical projection. Different VLANs prove
  broadcast-domain separation, not security isolation; without policy evidence
  the result remains `POLICY_UNKNOWN`.
- **Design findings.** Evidence-backed findings cover observable resiliency,
  STP placement, trunk consistency, EtherChannel health, unused ports,
  access-edge protections, DHCP snooping, DAI, AAA, PoE headroom, interface
  errors, and management-service exposure. There is no numeric design score.
- **Failure simulation.** In-memory scenarios remove an observed interface,
  relationship, switch, gateway, or port-channel member and recompute graph
  consequences. Proven, inferred, and unknown consequences remain distinct;
  the model does not claim that a routing protocol will fail to reconverge.
- **Path Explorer.** Paths show every evidence-backed hop as `PROVEN`,
  `INFERRED`, `EXPECTED`, `AMBIGUOUS`, or `UNKNOWN`. An incomplete path stops at
  `UNKNOWN` instead of inventing the missing hop.
- **Bounded performance probing.** Fixed local ping and route-trace operations
  distinguish healthy, degraded, unreachable, and insufficient-evidence
  results while minimizing retained target and hop identities.
- **Advanced capability awareness.** The catalog can observe or classify VRF,
  EtherChannel, OSPF, EIGRP, BGP, BFD, IP SLA, EVPN, VXLAN/NVE, Segment Routing
  MPLS, and SRv6 evidence when a platform exposes it. Awareness is not a claim
  that every feature is configured, supported, or validated on real hardware.

### Acceptance status

The existing primary Catalyst has completed a focused real-device pass. The
automated backend, frontend, parser, privacy, and browser state suites exercise
the implementation, including every Lab Assurance view across empty, partial,
failed-source, unknown, long-content, and responsive states.

Two release gates remain open:

1. a legitimately accessible secondary IOS/IOS-XE instance must complete the
   prepared onboarding, independent collection, discovery/reconciliation,
   failure isolation, removal, re-addition, and recovery procedure;
2. a human must complete interactive UI acceptance.

A legitimately licensed virtual IOS/IOS-XE instance may satisfy the platform
and multi-device behavior gate, but it must not be described as physical
hardware acceptance. Advanced features that are not present in the available
lab are recorded honestly rather than made mandatory. Until both outstanding
gates pass, there is no `v0.8.0` tag, installer set, or GitHub Release.

See [Lab Assurance](docs/V0.8-LAB-ASSURANCE.md) and the current
[acceptance record](docs/V0.8-ACCEPTANCE-STATUS.md).

---

## v0.9 - Desired State & NetDevOps

### Objective

Add a local, outcome-oriented desired-state layer and reproducible validation
workflows without turning Git, templates, or automation into device authority.

### Normalized desired state

Desired state describes outcomes rather than raw IOS snippets. Initial intent
can cover:

- device role and expected neighbours;
- VLAN, segment, access, trunk, SVI, and gateway expectations;
- minimum observed uplink redundancy and management-path expectations;
- required or prohibited services and security capabilities;
- PoE and interface-role expectations;
- software-family policy and maintenance constraints;
- measurable path or service objectives where evidence exists.

Intent remains separate from observations. Provider-specific configuration is
normalized into comparable claims before reconciliation.

### Immutable snapshots and known-good references

- Save immutable desired-state snapshots with stable identifiers, schema
  version, creation time, provenance, and a content digest.
- Let an operator select an active snapshot without mutating historical
  snapshots.
- Retain explicit known-good configuration and desired-state references.
- Compare current evidence with the active snapshot and the selected known-good
  reference without rewriting either baseline.
- Migrations are additive and preserve the ability to explain older results.

### Categorized drift

Report drift by meaning, not merely as a text diff. Categories should include:

- missing, unexpected, or changed topology relationships;
- configuration or policy mismatch;
- operational-state mismatch;
- running/startup divergence;
- current configuration versus known-good reference;
- stale or insufficient evidence;
- changes associated with an audited SwitchOps session versus external or
  unattributed changes.

Assurance outcomes remain explicit: `ALIGNED`, `DRIFT`, `VIOLATED`,
`INSUFFICIENT_EVIDENCE`, `NOT_APPLICABLE`, and `STALE`. Missing evidence never
becomes compliance, and external changes are not attributed to an actor that
SwitchOps cannot prove.

### Existing Change Assurance integration

Desired-state differences may generate a proposed plan only when every action
maps exactly to the existing bounded Change Assurance operation catalog. Plan
generation does not authorize or execute anything. Existing interface policy,
write lock, fresh preflight, backup, verification, audit, rollback, and explicit
startup-save rules remain unchanged. A desired-state document cannot make an
`UNMANAGED` interface operable or introduce arbitrary IOS.

### Scheduled backups

Add local scheduling for read-only configuration capture and validation:

- schedules are explicit, inspectable, bounded, and disabled by default;
- each run records its result independently;
- missed, failed, and partial runs remain visible;
- retention is local and privacy-aware;
- scheduling a backup does not schedule a configuration change or startup save.

### Validation-only headless and Git workflows

Support reproducible, non-mutating validation for source-controlled desired
state:

- schema and semantic validation with deterministic exit codes;
- comparison against supplied privacy-safe evidence snapshots;
- generated drift and plan-review artifacts that contain no credentials or raw
  secrets;
- pull-request or CI checks that validate intent but cannot connect to or
  modify a device;
- optional Git storage for desired-state documents and immutable metadata, not
  credentials, runtime databases, raw backups, or clear private identifiers.

Headless validation is not a hidden controller. Any future live plan still
enters the existing local, interactive Change Assurance boundary.

### Exit criteria

- Desired state is outcome-oriented, versioned, immutable, and separate from
  observation.
- Every drift result names its category, evidence, freshness, and uncertainty.
- Known-good references are explicit and auditable.
- Generated plans use only the existing bounded operation model.
- Scheduled work is read-only and local.
- Git and headless workflows validate only and hold no device authority.

## v1.0 - Production Hardening / Stable

### Objective

Make the implemented product dependable, supportable, and predictable enough
for a stable release line. v1.0 is a hardening milestone, not another broad
feature expansion.

Primary work:

- define and publish a tested platform/support matrix;
- complete outstanding real-device, interactive UI, upgrade, clean-install,
  uninstall, and recovery acceptance;
- harden process supervision, reconnect behavior, timeouts, cancellation,
  partial-state recovery, database migration, and corruption handling;
- establish performance and retention bounds for long-running local use;
- complete accessibility, responsive-layout, diagnostics, operator guidance,
  and failure-message review;
- expand threat modelling, dependency review, secret/privacy scanning, and
  local data lifecycle documentation;
- make builds reproducible and releases traceable to an exact tested commit;
- formalize signed artifact, checksum, rollback, compatibility, and support
  policies.

The v1.0 exit gate is measured stability and release discipline, not the number
of new protocols or integrations.

## v1.1 - Experience Assurance

Extend Lab Assurance from network-path evidence toward service experience:

- bounded ICMP, TCP, DNS, and HTTP/HTTPS tests originated locally;
- operator-defined latency, jitter, loss, availability, and response-time
  objectives;
- service classes that remain local intent rather than hidden QoS policy;
- optional read-only application or flow metadata where platform and licensing
  permit it;
- an incident timeline correlating probes, interface state, counters, path
  changes, provider events, bounded changes, and recovery.

Correlation does not prove causation. Device-hosted probes or new active
operations require separate platform validation and typed safety review.

## v1.2 - Lifecycle & Readiness

Extend evidence-before-change into maintenance planning:

- software family/version, boot, uptime, reload, stack consistency, image, and
  storage inventory where safely available;
- operator-authored approved software baselines without claiming Cisco
  recommendation merely because a version is newer;
- readiness checks covering compatibility, storage, topology redundancy,
  management-path survival, backups, running/startup divergence, active
  warnings, and maintenance intent;
- richer configuration lifecycle views for current, startup, known-good,
  desired, and externally changed state.

Initial scope is inventory, readiness, and post-change verification. Image
transfer, activation, reload, and automated maintenance scheduling require a
separate high-blast-radius design and acceptance program.

## v1.3 - Multi-Domain Context

Generalize the provider-neutral evidence model beyond Catalyst and Meraki.
Possible read-only providers include ISE, Catalyst Center, Catalyst SD-WAN
Manager, additional Meraki services, ACI/APIC, Secure Firewall/FMC, and
ThousandEyes, subject to API access and licensing.

The context graph may normalize endpoint identity, IP/MAC bindings, role, site,
security group, segment/VRF, application class, transport, path/experience
telemetry, and enforcement point. Provider claims remain distinct: an ISE
identity, Meraki client record, Catalyst MAC entry, and operator-authored
expectation may refer to one entity without becoming interchangeable facts.

v1.3 remains read-only federation. External context may enrich, warn, or block
a local plan but cannot grant local write authority.

## v1.x - Advanced Fabric Assurance

Deepen the capability awareness introduced in v0.8 only when suitable licensed
hardware and repeatable fixtures are available. Candidate read-only assurance
modules include:

- STP/MST/RSTP consistency, stack and EtherChannel redundancy;
- OSPF, IS-IS, BGP, FHRP, BFD, IP SLA, and tracked-object operation;
- VRF and segmentation consistency;
- VXLAN/EVPN control-plane and VTEP relationships;
- SD-WAN transport and path state;
- SR-MPLS/SRv6 locators and policies, Flex-Algo, affinities, and fast-reroute
  evidence;
- MACsec/IPsec posture and platform-exposed application visibility.

Every protocol card should answer four separate questions: capability,
configuration, operation, and assurance against local intent. Configuration is
never operational proof. Unsupported syntax, missing privilege, and parser
failure remain unknown rather than becoming unsupported platform verdicts.

Advanced fabric work begins as observation and verification. Policy authoring,
traffic steering, routing-metric changes, or fabric automation require separate
safety architecture and exhaustive real-platform validation.

## Long term - Assisted Operations

Automation or AI may reduce operator effort by summarizing typed evidence,
explaining contradictions, ranking deterministic checks, producing incident
evidence packs, suggesting plans from the existing bounded catalog, explaining
blocked plans, and drafting change or incident reports.

Assistance remains advisory. It cannot emit arbitrary IOS into an execution
path, create command authority, change interface policy, unlock writes, skip
preflight, bypass confirmation, promote weak discovery into authority, or save
startup configuration. Deterministic backend policy remains authoritative.

---

## Directional design references

Public Cisco design material informs several architectural ideas without
becoming a product claim:

- [Cisco Validated](https://www.cisco.com/site/us/en/solutions/cisco-validated/index.html): separate architecture, deployment, and operational validation.
- [Cisco Unified Branch and Network as Code](https://netascode.cisco.com/docs/guides/branch/01_overview/): outcome-oriented intent, repeatable validation, and lifecycle workflows.
- [Application-Aware Path Selection in SRv6 Networks](https://www.cisco.com/c/en/us/solutions/collateral/design-zone/cisco-validated-profiles/CVD-Application-Aware-Routing-in-SRv6.html): measure path suitability separately from reachability.
- [Cisco Catalyst SD-WAN Design Guide](https://www.cisco.com/c/en/us/td/docs/solutions/CVD/SDWAN/cisco-sdwan-design-guide.html): keep policy intent separate from transport evidence and distinguish failure from degradation.
- [Cisco Common Policy Integration Guide](https://www.cisco.com/c/en/us/td/docs/cloud-systems-management/network-automation-and-management/catalyst-center/cisco-validated-solution-profiles/common-policy-integration-guide.html): normalize cross-domain context without losing enforcement boundaries.
- [Campus Software Image Management](https://www.cisco.com/c/en/us/td/docs/solutions/CVD/Campus/dnac-swim-deployment-guide.html): baseline, readiness, scheduling, and post-change verification as separate lifecycle stages.
- [VXLAN BGP EVPN Design and Implementation Guide](https://www.cisco.com/c/en/us/td/docs/dcn/whitepapers/cisco-vxlan-bgp-evpn-design-and-implementation-guide.html): reason about fault isolation, backup paths, and Day-N operations rather than configuration alone.

## What SwitchOps should not become

SwitchOps should not become:

- an arbitrary Cisco CLI launcher;
- a cloud-mandatory controller;
- a replacement for established Cisco controllers and assurance platforms;
- a tool that changes a network because an inferred profile says it is wrong;
- a certification engine that claims a network is Cisco Validated;
- an autonomous agent with independent configuration authority.

The durable direction remains **evidence-backed local network understanding,
reproducible validation, and conservative auditable change control**.
