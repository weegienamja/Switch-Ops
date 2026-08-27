# SwitchOps Resilience Lab

The Resilience Lab is a development- and test-only scenario runner. It supplies
immutable synthetic evidence to the same management-path, Meraki refinement,
recovery-planning, local-endpoint correlation, topology construction, and
discovery-history code used by SwitchOps. It does not contain a second diagnosis
engine.

## Runtime boundary

The package has a separate `python -m backend.resilience_lab` entrypoint and is
not imported by `backend.app`, its API routes, or its normal collectors. There is
no environment-variable switch that can activate fixtures in production. Each
run creates private temporary SQLite histories, constructs production services
with explicit evidence providers, and discards them afterward. Scenario data can
therefore enter only a caller that deliberately imports this development package.

The runner has no recovery executor, IOS command path, Meraki write client, host
network mutator, shell adapter, or API endpoint. Every phase asserts that its
write probe reports zero operations. A nonzero report fails the
`unsafe-action-prevention` dimension.

Run the complete catalogue from the repository root:

```powershell
.\.venv\Scripts\python.exe -m backend.resilience_lab
```

Run one scenario:

```powershell
.\.venv\Scripts\python.exe -m backend.resilience_lab ENDPOINT_PORT_MOVE
```

Each output line is one JSON result. The process exits nonzero if any scenario
fails.

## Scenario and result contracts

`scenarios/catalog.json` is validated against versioned Pydantic contracts before
execution. A scenario declares a stable ID, description, purpose, and a strictly
ordered list of timezone-aware phases. Each phase contains a transition label,
normalized evidence, and expectations. The first phase is the initial state;
later phases change evidence while the production history stores preserve time.

Expectations can cover:

- diagnosis and confidence;
- recovery-plan status and stale binding validity;
- Meraki state and freshness;
- identity retention and duplicate entity IDs;
- current and previous attachment;
- timestamped historical continuity;
- `MUST CLAIM`, `MAY CLAIM`, and `MUST NOT CLAIM` sets;
- the invariant `writesPerformed = 0`.

Results use named pass/fail dimensions instead of an arbitrary numerical score:
classification, identity retention, topology reconciliation, recovery planning,
unsafe-action prevention, and historical continuity. A failed assertion reports
the exact expected and actual values.

Only RFC 5737 documentation IPv4 networks and the RFC 7042 documentation MAC
block are accepted in repository fixtures. Credential-like fields are rejected.

## Identity and attachment

A confirmed local endpoint now derives its internal stable identity from its
hardware identity evidence, not its switch port. The raw MAC is not serialized.
`NetworkDevice` tracks identity confidence independently from attachment state
and attachment confidence. Discovery history can therefore retain an endpoint
ID while changing `currentInterface`, preserving `previousInterface`, and
emitting `ENDPOINT_MOVED`.

A different stable identity on an occupied port emits `DEVICE_REPLACED` and
retains the prior node as historical. A MAC simultaneously learned through
multiple ports emits `ATTACHMENT_CONFLICT`; it creates no duplicate stable node
and does not assert a move or replacement.

## Implemented catalogue

- Host/interface: `DHCP_SUBNET_CHANGE`, `DHCP_RENEW_SAME_NETWORK`,
  `ROUTE_REMOVED`, `VPN_ROUTE_TAKEOVER`, `WIFI_BECOMES_PREFERRED`.
- Session: `SSH_HALF_OPEN_SESSION`, `SSH_AUTH_FAILURE`,
  `SSH_HOST_KEY_CHANGE`.
- Identity/topology: `ENDPOINT_PORT_MOVE`,
  `NEW_DEVICE_REPLACES_OLD_DEVICE`, `SAME_MAC_VISIBLE_MULTIPLE_PORTS`.
- Meraki: `MX_API_UNAVAILABLE`,
  `MERAKI_CURRENT_STATE_CONFLICTS_WITH_HISTORY`.
- Runtime/safety: `BACKEND_RESTART`,
  `RECOVERY_PLAN_STALE_AFTER_DHCP_CHANGE`,
  `CONFLICTING_EVIDENCE_PRODUCES_INDETERMINATE`.

## Deferred catalogue

The typed model is extensible to the remaining investigated families. Deferred
host cases are `STALE_DHCP_LEASE`, `DHCP_SERVER_CHANGE`, `DHCP_UNAVAILABLE`,
`APIPA_ADDRESS`, `DEFAULT_GATEWAY_CHANGE`, `PREFIX_CHANGE`,
`MULTIPLE_IPV4_ADDRESSES`, and `DUPLICATE_IP_EVIDENCE`.

Deferred Windows path cases are `ROUTE_RESTORED`,
`MORE_SPECIFIC_ROUTE_ADDED`, `DEFAULT_ROUTE_TAKEOVER`,
`ETHERNET_BECOMES_PREFERRED`, `MULTIPLE_DEFAULT_ROUTES`,
`INTERFACE_METRIC_CHANGE`, `NIC_DISABLE_ENABLE`,
`NIC_DISAPPEAR_REAPPEAR`, and `VIRTUAL_ADAPTER_PREFIX_OVERLAP`.

Deferred device cases are `SSH_TIMEOUT`, `SSH_REFUSED`,
`SSH_NEGOTIATION_FAILURE`, `DEVICE_RELOAD`,
`DEVICE_UNREACHABLE_VALID_HOST_PATH`, and `MANAGEMENT_SVI_UNREACHABLE`.
Deferred topology cases are `ENDPOINT_DISCONNECT_RECONNECT_SAME_PORT`,
`ENDPOINT_DISCONNECT_RECONNECT_NEW_PORT`, `MAC_AGING_ONLY`,
`IP_CHANGE_SAME_ENDPOINT`, `HOSTNAME_CHANGE_SAME_ENDPOINT`,
`MULTIPLE_IDENTITY_SIGNALS_CONFLICT`, and `UPLINK_PORT_CHANGE`.

All VLAN/L2 cases remain deferred: `ACCESS_VLAN_CHANGE`,
`TRUNK_NATIVE_VLAN_CHANGE`, `TRUNK_ALLOWED_VLAN_REMOVED`,
`TRUNK_ALLOWED_VLAN_RESTORED`, `MANAGEMENT_VLAN_MISMATCH`,
`PORT_ERR_DISABLED`, `STP_BLOCKING`, and `PHYSICAL_LINK_DOWN`.

Deferred upstream cases are `MERAKI_CREDENTIALS_UNAVAILABLE`,
`MERAKI_NETWORK_SELECTION_MISSING`, `MX_REBOOT`, `MX_CLIENT_SUBNET_CHANGE`,
`MX_DHCP_CHANGE`, `MX_PORT_ACCESS_TO_TRUNK`, `MX_PORT_TRUNK_TO_ACCESS`, and
`STALE_MERAKI_EVIDENCE`. Deferred runtime cases are
`FRONTEND_BACKEND_VERSION_MISMATCH`, `PORT_8765_OCCUPIED`,
`DUPLICATE_SWITCHOPS_INSTANCE`, `TELEMETRY_STORE_UNAVAILABLE`, `SQLITE_LOCK`,
`PERSISTED_HISTORY_STALE`, `PERSISTED_HISTORY_CORRUPT_OR_INVALID`,
`COLLECTOR_PARTIAL_FAILURE`, and `COLLECTOR_TOTAL_FAILURE`.

Deferred binding cases are `RECOVERY_PLAN_STALE_AFTER_ROUTE_CHANGE`,
`RECOVERY_PLAN_STALE_AFTER_TARGET_CHANGE`,
`RECOVERY_PLAN_STALE_AFTER_ADAPTER_CHANGE`, and
`BLOCKED_PLAN_REMAINS_NON_EXECUTABLE`.

## Privacy-safe incident replay design

A later export tool should operate only on normalized evidence, never collector
secrets or raw command output. It should deterministically pseudonymize entity
IDs and hostnames, map IPv4 addresses into RFC 5737 networks while preserving
same-prefix and route relationships, map MACs into the documentation EUI block,
shift all timestamps by one constant offset, and reject usernames, credentials,
tokens, and unrecognized fields. The generated fixture must pass the same schema
and privacy validator before review. This preserves topology, timing, confidence,
and causal relationships without retaining lab identity.

## Physical acceptance design (opt-in and observation-only)

Physical acceptance is deliberately not part of CI and performs no action. For
`ENDPOINT_PORT_MOVE`, an operator first records a healthy baseline and selects a
destination only after manually confirming it is unused, enabled, an access port
in the same intended VLAN, and not trunking, protected, err-disabled, reserved,
or operationally unknown. If any property is unknown or unsafe, abort; SwitchOps
must not suggest that port. The operator moves the cable, requests normal
read-only discovery, and scores identity retention, new attachment, absence of
duplicates, preserved history, and management connectivity. The operator can
move the cable back independently if needed. SwitchOps changes no configuration.

Other future runbooks may observe a brief disconnect/reconnect, an operator-run
DHCP renewal, Wi-Fi enable/disable, a SwitchOps restart, a planned Catalyst
reload, or a planned MX restart. Every runbook requires an explicit maintenance
window and operator action, captures before/after evidence, defines an abort and
out-of-band recovery path, and never automates the disruptive step.

A developer UI is deferred. The CLI and CI result contract provide the required
architecture and deterministic evidence first; a future UI can render the same
scenario and result models without gaining collector or write authority.

## Degradation-to-recovery scenarios

Failure alone does not prove SwitchOps recovers its own understanding, so the
catalogue includes arcs that carry the environment back to a working state:

| Scenario | Arc |
| --- | --- |
| `ROUTE_RESTORED` | healthy -> route removed -> route restored |
| `ENDPOINT_DISCONNECT_RECONNECT_SAME_PORT` | attached -> absent -> same port |
| `ENDPOINT_DISCONNECT_RECONNECT_NEW_PORT` | Gi0/2 -> absent -> Gi0/5 |
| `DHCP_UNAVAILABLE_APIPA_RECOVERY` | leased -> APIPA -> lease restored |
| `DEVICE_RELOAD_RECONNECT` | live -> device silent -> live again |

Each ends with the model reconciled rather than merely degraded, and a plan
generated while degraded must fail state-binding once the environment changes.
`test_recovery_scenarios_return_to_a_healthy_or_reconciled_final_phase` pins
that property so a future scenario cannot quietly stop at the failure.

An endpoint that returns to the port it left produces no transition, which is
the correct reconciliation. The runner still reports that endpoint's current
attachment so a scenario can assert where it ended up, not merely that nothing
was claimed about it.

## Evidence exporter

`python -m backend.resilience_lab.exporter incident.json --id REAL_DHCP_MOVE`
converts a saved `/api/management-path` response into a scenario that keeps the
structure a diagnosis depends on and none of the real addressing:

```
REAL INCIDENT -> NORMALIZED EVIDENCE -> PRIVACY TRANSFORM -> FIXTURE -> REPLAY
```

Real prefixes are mapped onto RFC 5737 documentation networks, one synthetic
network per real network, so "same subnet" and "different subnet" survive. The
host octet is preserved (`A.B.C.95` -> `192.0.2.95`), as are gateway
relationships, route kind, evidence freshness, and the intervals between
observations; absolute wall-clock time is rebased. Local adapter identifiers
become opaque labels that stay consistent across phases, because the diagnosis
depends on it being one adapter rather than two. Credential-shaped keys are
dropped. RFC 3927 link-local is preserved unchanged, since it is identical on
every host everywhere and identifies nothing.

If an incident spans more distinct networks than there are documentation
networks, the export is refused rather than collapsing two real prefixes onto
one synthetic prefix, which would invent a same-subnet relationship that never
existed.

Output is re-validated with the same privacy checker that guards the committed
catalogue, so an unsafe fixture cannot be produced silently.
`REAL_DHCP_SUBNET_MOVE` is the committed anonymized replay of the observed
host-network-change incident.

## Physical acceptance coordinator

`backend/resilience_lab/physical_acceptance.py` sequences an observation-only
test: capture a baseline, assess preconditions, describe the manual action,
wait for real evidence, run ordinary reconciliation, and report PASS/FAIL. It
has no device write path, no host mutation, and no API route; a nonzero write
probe fails the run.

`assess_destination_port` refuses any port that is occupied, trunking, uplink
described, administratively down, the management path, the source port, or not
positively established as a free access port. Absence of evidence is treated as
unsafe rather than as permission -- an operator unplugging a live uplink because
a description was blank is not a recoverable mistake. When nothing is safe,
every assessment is returned so the operator sees why.

A MAC that disappears is never reported as a move, a different MAC appearing is
never reported as movement of the original, and the same MAC seen in two places
is reported as `ATTACHMENT_CONFLICT` rather than resolved by guessing.

## CLI

```powershell
python -m backend.resilience_lab --list            # ids, phase counts, recovery marker
python -m backend.resilience_lab --recovery-only   # degradation-to-recovery arcs
python -m backend.resilience_lab --summary         # human-readable failures
python -m backend.resilience_lab ROUTE_RESTORED    # one scenario
```

JSON remains the default because CI consumes it. `--summary` names the
scenario, phase, dimension, expectation, and actual value, because a failing
assertion buried in a JSON line is hard to act on. Recovery scenarios are
detected from their phases rather than a hand-maintained list, so a new arc is
recognised without anyone remembering to register it.

## Topology export

```powershell
python -m backend.resilience_lab.exporter --topology `
  phase1.json phase2.json phase3.json `
  --endpoint-mac <mac> --id REAL_PORT_MOVE
```

Each input is an ordered read-only dashboard response. The transform keeps port
names verbatim -- an interface label identifies hardware, not a person -- and
replaces the hardware address with one stable documentation-block pseudonym
carried across every phase, because correlation breaks if the identity changes
between observations. Hostnames become synthetic labels, interface descriptions
are dropped entirely (they routinely name a person or a room), and learned
addresses belonging to any other endpoint are excluded rather than anonymized.

Expectations are derived from what the evidence shows, so an exported incident
replays to the conclusion the product actually reached rather than one asserted
up front.

## Physical acceptance readiness

```powershell
python -m backend.resilience_lab.physical_acceptance --endpoint-mac <mac>
```

Evaluates whether `ENDPOINT_PORT_MOVE` can run **without performing any part of
it**. Endpoint-to-port attachment is only observable from the Catalyst's own MAC
address table, so the test requires a working read-only management path. Meraki
evidence describes MX LAN and port configuration, not endpoints on Catalyst
access ports, and cannot substitute. Durable history can describe where an
endpoint *used* to be but cannot observe where it is now.

The verdict is therefore `BLOCKED` with
`LIVE_CATALYST_TOPOLOGY_UNAVAILABLE` whenever management is degraded, and no
operator action is emitted. Evidence older than `MAX_EVIDENCE_AGE` is refused
as well: attachment changes the instant a cable moves, so a stale observation
cannot authorise a physical test.

Candidate destination ports are rejected unless positively established as free
access ports. Descriptions are treated as operator intent, never as proof: an
"Access Port" label can add a blocker but never clears one. Infrastructure
attachments are identified with the production topology classifier rather than
a second opinion inside the test tooling.
