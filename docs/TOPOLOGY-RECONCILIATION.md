# Topology reconciliation

SwitchOps v0.3.0 stops treating the topology diagram as the answer and starts
treating it as one view of a comparison.

The question it now answers is not *what is the network?* but:

> Does what SwitchOps can currently observe match what the network is supposed
> to be — and if not, what changed, and how sure are we?

---

## 1. Why this was needed

A real change to the lab exposed a modelling error rather than a rendering one.

The network used to be:

```
Internet -> ISP hub (router, NAT, DHCP) -> Catalyst -> endpoints
```

It is now:

```
Internet -> ISP hub in MODEM MODE -> edge gateway -> Catalyst -> endpoints
                                          `-------> access point (moved off the Catalyst)
```

The Catalyst's configuration did not change. Its interface descriptions still
describe the old network: `Gi0/1` still says it uplinks to the old router, and
`Gi0/4` still says an access point lives there. Both statements are now wrong,
and the switch has no idea.

SwitchOps rendered `Gi0/1` as an **observed** device, typed *router*, named
from that description. A label somebody typed into a switch years ago was
being presented as a discovered device on a network diagram.

That is the failure this release fixes.

---

## 2. The previous model failure, precisely

`build_topology` built the endpoint for a linked port like this:

```python
category, vendor, model, stage, evidence = classify_device(interface.name)
device = NetworkDevice(
    name=interface.name.strip(),      # <- the description
    type=category,                    # <- inferred from the description
    source="observed",                # <- but marked observed
    identitySource="interface-description",
    ...
)
```

`identitySource` recorded the truth, but every part of the model a human
actually reads — name, type, icon, `source="observed"` — had been populated
from intent. The honest field was the one nobody looked at.

Two claims were being collapsed into one object:

| Claim | Evidence | Strength |
| --- | --- | --- |
| Something is attached to Gi0/1 | link up, addresses learned | strong |
| That something is the old ISP router | a description | none |

The fix is not to delete the description. It is to stop merging the two.

---

## 3. Architecture

### Assertions

Every claim is a `TopologyAssertion`: one source, about one interface, at one
time. Assertions are additive and are allowed to disagree.

```python
class TopologyAssertion(BaseModel):
    subject: str                 # "Gi0/1"
    relationship: RelationshipKind
    object_label: str            # "MX-EDGE-01" | "Unidentified device"
    object_identified: bool      # False when only presence is proven
    evidence_class: EvidenceClass
    source: EvidenceSource
    confidence: Confidence
    detail: str
    observed_at: datetime | None
```

`object_identified` is the field that matters. When it is `False`,
`object_label` is a placeholder and must never be rendered as a name.

### Providers

Evidence arrives through providers, so the reconciler never learns about a
vendor API:

| Provider | Produces |
| --- | --- |
| `CiscoIosEvidenceProvider` | observed + inferred, from interfaces, CDP, MAC table, ARP |
| `IntentProvider` | expected, from stored intent or the interface description |
| `HistoryProvider` | historical, from the previous observation |
| *(future)* `MerakiEvidenceProvider` | observed, from a controller that can see elsewhere |

### Reconciler

`reconcile()` compares the claims per interface and produces a deterministic
`InterfaceReconciliation`. No heuristics beyond conservative label matching,
and no AI: the same evidence always yields the same answer.

---

## 4. Evidence classes

These are the implemented values of `EvidenceClass`.

| Class | Means | Example on this platform |
| --- | --- | --- |
| `observed` | Proven by telemetry read from a device just now. | link state, negotiated speed, PoE draw, a CDP neighbour, learned addresses |
| `expected` | Believed to be intended. Never a sighting. | an interface description; an expectation recorded in SwitchOps |
| `historical` | Observed in an earlier snapshot; may no longer hold. | the neighbour that announced itself last time |
| `inferred` | Supported by evidence, not directly proven. | the default gateway lies through this port; this address is randomised |
| `unknown` | Not enough evidence. | the default when nothing applies |

`EvidenceSource` records *which* source: `cdp`, `lldp`, `mac-table`, `arp`,
`interface-telemetry`, `interface-description`, `user-intent`,
`accepted-plan`, `prior-observation`, `mac-address-form`, `meraki-api`,
`none`.

### Relationship kinds

The distinction between attachment and reachability is preserved from v0.2.1
and strengthened:

| Kind | Means |
| --- | --- |
| `direct-neighbour` | The neighbour announced itself on the wire. Proves direct attachment. |
| `attached-endpoint` | Link up and addresses learned, so something is attached — but it may itself be a switch, router or AP. |
| `learned-behind` | Reachable through the interface, possibly several hops away. Never proof of attachment. |
| `gateway-path` | This switch's default gateway is reachable through this interface. A direction, not an identity. |
| `expected-neighbour` | Intent only. |

---

## 5. Device identity versus device role

Identity and role are different questions, and a device's product category
does not determine its current role. The ISP hub in the motivating example is
still a router, a DHCP server and an access point by *capability* — but its
current *role* is a modem bridge, because that is how it has been configured.

SwitchOps therefore never derives role from category. Role is carried on the
interface (`InterfaceRole`: `uplink` / `access` / `unknown`) and is inferred
from the interface's own description or trunk status. It drives layout and
interpretation — an uplink is where many addresses legitimately appear behind
one neighbour — and never changes how many device nodes are produced.

---

## 6. Health versus reconciliation

**These are independent, and the product now says so in as many words.**

| | Health | Reconciliation |
| --- | --- | --- |
| Question | Is the switch and are its links working? | Does reality match intent? |
| Inputs | counters, temperature, CPU, memory, PoE, link deltas | observed / expected / historical / inferred assertions |
| Green means | nothing is failing | nothing is unaccounted for |

The real lab today is the exact case that motivates the split:

```
NETWORK HEALTH          HEALTHY
TOPOLOGY RECONCILIATION Attention — 2 expected but not observed
```

Nothing is broken. Two documented devices are not where the documentation says
they are. Both statements are true, and neither belongs inside the other.
`test_health_stays_healthy_while_reconciliation_reports_drift` pins it.

---

## 7. Reconciliation statuses

| Status | Means |
| --- | --- |
| `aligned` | Expected and observed agree. |
| `drift` | Both exist and disagree. `driftKind` is `identity` or `location`. |
| `expected-not-observed` | Intent exists; nothing is observed there now. |
| `unexpected` | Something is observed that no intent accounts for. |
| `uncertain` | A device is attached but nothing identifies it, so the expectation can be neither confirmed nor contradicted. |
| `not-applicable` | No intent and nothing observed, or the operator muted the interface. |

`changedSincePrevious` is deliberately **not** a status. It is orthogonal: an
interface can match intent perfectly and still have changed since the last
observation, and conflating the two would hide one of them.

### Why "uncertain" is the honest answer more often than you would like

`drift` requires knowing what is actually there. On a switch where nothing
announces itself, presence can be proven and identity cannot. Forcing that
into `aligned` or `drift` would be a guess, so it is neither.

---

## 8. Topology drift versus configuration drift

Two independent concepts that are easy to confuse:

| | Configuration drift | Topology drift |
| --- | --- | --- |
| Compares | known-good config vs current config | expected relationship vs observed relationship |
| Example | a description was edited on the switch | the device on Gi0/1 is not the documented one |
| Can occur with | no physical change at all | no configuration change at all |

The motivating migration produced **topology drift with zero configuration
drift**: not one line of the Catalyst's configuration changed, and everything
it documents is now wrong.

There is a third case the model handles explicitly. When local intent is
updated to match reality, the switch's own description is left untouched —
configuration writes are disabled — so intent and device documentation now
disagree. That is reported as `documentationStale`:

> The switch's own interface description still reflects the older topology.
> SwitchOps has not changed it — configuration writes are disabled.

---

## 9. Intent management

Interface descriptions are weak intent: unversioned, unattributed, and only
editable by changing the device. SwitchOps stores its own, ranked by
authority:

1. `user-intent` — recorded in SwitchOps by the operator
2. `accepted-plan` — from an accepted change plan
3. `interface-description` — the switch's own documentation

Stored in `topology-intent.sqlite` beside the other local databases. The API
is `GET/PUT/DELETE /api/topology/intent`.

**Recording intent never touches the switch.** No configuration is generated,
no session is opened, and a test asserts that recording intent while
`switch_session` is booby-trapped still succeeds.

An interface can also be *muted*: the evidence is still gathered and still
shown, it simply stops asking for a decision and stops raising events.

---

## 10. Events

A discrepancy that is still true on the twentieth refresh is one situation,
not twenty events. Each interface's reconciliation is reduced to a signature:

```
status | driftKind | observedLabel | expectedLabel
```

persisted in `reconciliation_state`. An event is raised only when that
signature **changes**:

| Event | Raised when |
| --- | --- |
| `topology_drift_detected` | an interface enters drift |
| `expected_device_missing` | an expected device stops being observed |
| `unexpected_device_observed` | something appears with no intent |
| `direct_neighbor_changed` | the announced neighbour differs from last time |
| `topology_reconciliation_resolved` | an interface leaves a discrepancy state |

`uncertain` deliberately raises nothing. An unidentifiable neighbour is a
standing property of the evidence available, not something that happened.

Separately, per-address `device_appeared` / `device_disappeared` events were
collapsed into one `learned_addresses_changed` per interface. MAC-table
membership churns constantly behind an uplink; the old behaviour produced six
events in a single observation on the real lab and buried everything else.

---

## 11. Why an OUI database was *not* added

Vendor lookup from a hardware address prefix was considered and rejected for
this release. The point of this work is to stop asserting things that cannot
be verified, and a vendor table that cannot be checked against the authoritative
registry at build time is exactly that risk in a new place.

What *is* implemented is arithmetic rather than a lookup: the
locally-administered bit of an address. When set, the address was assigned by
software — a randomised client address, a virtual NIC — and no vendor exists
to infer. SwitchOps reports that as `inferred` evidence:

> N of the addresses learned through Gi0/3 are locally administered, meaning
> they were assigned by software rather than a manufacturer. No vendor can be
> inferred from them.

This is a real condition on the live lab, not a hypothetical.

If an OUI table is added later it must produce `inferred` evidence with a
`mac-oui` identity source, and must never be allowed to satisfy an
`aligned` reconciliation on its own. A prefix identifies a manufacturer, never
a model.

---

## 12. What ARP does and does not prove

`show ip arp` was added because it is the only table on this switch that ties
an IP address to a hardware address. With the MAC table it can answer *which
port is the default gateway reachable through?*

It proves a **direction**, never an identity, and the entry only exists while
the switch has had reason to talk to that address. On the live lab the gateway
is routinely absent from the cache, and SwitchOps then claims nothing at all —
absence of an ARP entry is not evidence of anything.

---

## 13. Meraki readiness

A controller that can see the rest of the network would be a
`MerakiEvidenceProvider` producing normalised `TopologyAssertion` values and
`ExternalSighting` records. Nothing in the reconciler would change.

The seam already exists and is tested. When an expected device is absent from
its expected interface **and** an external source can see it elsewhere, the
status becomes `drift` with `driftKind: "location"` rather than
`expected-not-observed`:

> TEST-AP-01 is expected on Gi0/4 but is not attached there. meraki-api
> reports it at TEST-GATEWAY-01 port 11, so the device is present on the network in a
> different place.

With no such provider configured, the sighting set is empty and the same case
correctly reports only "expected but not observed" —
`test_without_an_external_source_the_same_case_is_only_missing` pins that
SwitchOps does not invent a location it cannot see.

SwitchOps should not become "Meraki Dashboard plus a Catalyst page". It should
be the reconciliation layer above several evidence sources, only one of which
is a Meraki controller.

---

## 14. What a single observation point can and cannot show

The Catalyst can see its own interfaces and whatever announces itself on them.
It cannot see past its directly connected neighbour.

So SwitchOps will draw:

```
[upstream device on Gi0/1]        <- what the Catalyst can prove
        |
    Catalyst
     /  |  \
   endpoints on Gi0/2, Gi0/3 ...
```

and will **not** draw the modem behind the gateway, or the access point now
attached to the gateway, because no configured source has observed either.
Those relationships can be recorded as intent and clearly marked as expected;
they may never be drawn as observed on Catalyst evidence alone.
