"""Topology reconciliation.

SwitchOps holds several claims about the same interface at once and compares
them, rather than letting one overwrite the others:

    observed    what current telemetry proves
    expected    what the operator says should be there
    historical  what an earlier observation showed
    inferred    what the evidence supports without proving

The rule that shapes everything here is:

    **An interface description is intent, not observation.**

A description is a label somebody typed into the switch, possibly years ago.
It may name a device that has been replaced, moved, or never existed. It is
therefore only ever allowed to produce an *expected* assertion. The observed
assertion for the same interface carries whatever the wire actually proved,
which on a switch with no CDP neighbour is presence without identity.

Evidence is contributed by providers so that a future source - a Meraki
controller, an LLDP-enabled switch - can be added without touching the
reconciler.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable, Mapping, Optional, Protocol, Sequence

from .models import (
    ArpEntry,
    CdpNeighbor,
    Confidence,
    DeviceType,
    EvidenceSource,
    ExpectedRelationship,
    ExternalSighting,
    InterfaceReconciliation,
    InterfaceStatus,
    MacTableEntry,
    NetworkEvent,
    ReconciliationStatus,
    ReconciliationSummary,
    TopologyAssertion,
)
from .topology import classify_device, interface_oper_state

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .intent_store import TopologyIntentStore


UNIDENTIFIED = "Unidentified device"

# Words that carry no identifying information in an interface description.
# "Uplink to Test Gateway" is about the *link*; only "Test ISP" names anything.
_STOPWORDS = frozenset({
    "uplink", "link", "to", "the", "a", "an", "port", "spare", "unused",
    "access", "trunk", "connection", "conn", "cable", "switch", "sw",
    "reserved", "temp", "test", "lab", "new", "old", "main", "and",
})

_MIN_TOKEN = 3


def _tokens(text: str) -> set[str]:
    """Significant lowercase tokens from a free-text label."""
    raw = re.split(r"[^a-z0-9]+", (text or "").lower())
    return {
        token for token in raw
        if len(token) >= _MIN_TOKEN and token not in _STOPWORDS
    }


def _tokens_match(left: set[str], right: set[str]) -> bool:
    """Conservative overlap test between two label token sets."""
    if not left or not right:
        return False
    if left & right:
        return True
    # Allow containment for tokens long enough to be meaningful, so
    # "home server" still matches "homeserver-01".
    for a in left:
        for b in right:
            if len(a) >= 4 and a in b:
                return True
            if len(b) >= 4 and b in a:
                return True
    return False


def is_locally_administered(mac: str) -> bool:
    """True when bit 1 of the first octet is set.

    A locally administered address is assigned by software - a randomised
    client address, a virtual NIC, a container bridge. It has no manufacturer
    prefix, so no vendor can be inferred from it. This is arithmetic on the
    address itself, not a database lookup.
    """
    digits = re.sub(r"[^0-9a-fA-F]", "", mac or "")
    if len(digits) < 2:
        return False
    return bool(int(digits[:2], 16) & 0b10)


# --- providers -------------------------------------------------------------


class EvidenceProvider(Protocol):
    """A source of topology assertions.

    Implementations normalise their own vendor/protocol detail into
    ``TopologyAssertion`` so the reconciler never learns about a specific API.
    A Meraki controller provider would satisfy this same interface.
    """

    name: str

    def assertions(self) -> list[TopologyAssertion]:  # pragma: no cover - protocol
        ...


class CiscoIosEvidenceProvider:
    """Observed and inferred assertions from one Cisco IOS observation."""

    name = "cisco-ios"

    def __init__(
        self,
        *,
        interfaces: Sequence[InterfaceStatus],
        mac_entries: Sequence[MacTableEntry],
        cdp_neighbors: Sequence[CdpNeighbor] = (),
        arp_entries: Sequence[ArpEntry] = (),
        default_gateway: Optional[str] = None,
        observed_at: Optional[datetime] = None,
    ) -> None:
        self.interfaces = list(interfaces)
        self.cdp_by_port: dict[str, list[CdpNeighbor]] = {}
        for neighbor in cdp_neighbors:
            if neighbor.local_interface:
                self.cdp_by_port.setdefault(neighbor.local_interface, []).append(neighbor)
        self.macs_by_port: dict[str, list[MacTableEntry]] = {}
        for entry in mac_entries:
            if entry.port.upper() == "CPU" or entry.vlan.lower() == "all":
                continue
            self.macs_by_port.setdefault(entry.port, []).append(entry)
        self.arp_entries = list(arp_entries)
        self.default_gateway = (default_gateway or "").strip()
        self.observed_at = observed_at or datetime.now(timezone.utc)

    # -- observed ---------------------------------------------------------

    def _observed_for(self, interface: InterfaceStatus) -> Optional[TopologyAssertion]:
        oper_up = interface_oper_state(interface.status) == "up"
        if not oper_up:
            # A down port observes nothing. Stale MAC entries are not evidence
            # of a present device.
            return None

        learned = self.macs_by_port.get(interface.port, [])
        neighbors = self.cdp_by_port.get(interface.port, [])

        if neighbors:
            neighbor = neighbors[0]
            category, vendor, model = _identity_from_cdp(neighbor)
            detail = f"{neighbor.remote_name} announced itself over CDP on {interface.port}."
            if neighbor.platform:
                detail += f" It reports its platform as {neighbor.platform}."
            return TopologyAssertion(
                subject=interface.port,
                relationship="direct-neighbour",
                objectLabel=neighbor.remote_name,
                objectIdentified=True,
                evidenceClass="observed",
                source="cdp",
                confidence="high",
                detail=detail,
                observedAt=self.observed_at,
                deviceType=category,
                vendor=vendor,
                model=model,
            )

        if learned:
            count = len(learned)
            detail = (
                f"The link on {interface.port} is up and {count} address"
                f"{'es are' if count != 1 else ' is'} being learned through it, "
                "so a device is attached. Nothing identifies it: no neighbour "
                "announced itself on this interface."
            )
            return TopologyAssertion(
                subject=interface.port,
                relationship="attached-endpoint",
                objectLabel=UNIDENTIFIED,
                objectIdentified=False,
                evidenceClass="observed",
                source="mac-table",
                confidence="high",  # presence is certain; identity is absent
                detail=detail,
                observedAt=self.observed_at,
            )

        return TopologyAssertion(
            subject=interface.port,
            relationship="attached-endpoint",
            objectLabel=UNIDENTIFIED,
            objectIdentified=False,
            evidenceClass="observed",
            source="interface-telemetry",
            confidence="medium",
            detail=(
                f"{interface.port} reports an active link, but no addresses have "
                "been learned through it yet and no neighbour announced itself."
            ),
            observedAt=self.observed_at,
        )

    # -- inferred ---------------------------------------------------------

    def _inferred_for(self, interface: InterfaceStatus) -> list[TopologyAssertion]:
        inferred: list[TopologyAssertion] = []
        learned = self.macs_by_port.get(interface.port, [])

        # Which way is the default gateway? ARP maps the gateway IP to a
        # hardware address; the MAC table maps that address to a port. When
        # both halves exist the path is evidenced. When they do not, say
        # nothing - the gateway routinely ages out of a switch's ARP cache.
        if self.default_gateway and learned:
            gateway_macs = {
                entry.mac.lower()
                for entry in self.arp_entries
                if entry.ip == self.default_gateway
            }
            if gateway_macs and any(entry.mac.lower() in gateway_macs for entry in learned):
                inferred.append(TopologyAssertion(
                    subject=interface.port,
                    relationship="gateway-path",
                    objectLabel=f"Default gateway {self.default_gateway}",
                    objectIdentified=False,
                    evidenceClass="inferred",
                    source="arp",
                    confidence="medium",
                    detail=(
                        f"The hardware address this switch has cached for its default "
                        f"gateway {self.default_gateway} is currently learned through "
                        f"{interface.port}. That places the gateway in this direction; "
                        "it does not identify the device."
                    ),
                    observedAt=self.observed_at,
                ))

        # Addresses beyond the first sit behind whatever is on the port.
        if len(learned) > 1:
            inferred.append(TopologyAssertion(
                subject=interface.port,
                relationship="learned-behind",
                objectLabel=f"{len(learned)} addresses",
                objectIdentified=False,
                evidenceClass="observed",
                source="mac-table",
                confidence="high",
                detail=(
                    f"{len(learned)} addresses are reachable through {interface.port}. "
                    "They sit behind the device on this port, not on the port itself."
                ),
                observedAt=self.observed_at,
            ))

        # Randomised addresses are worth flagging because they close off the
        # only remaining identity route - a manufacturer prefix.
        randomised = [entry for entry in learned if is_locally_administered(entry.mac)]
        if randomised:
            inferred.append(TopologyAssertion(
                subject=interface.port,
                relationship="learned-behind",
                objectLabel=f"{len(randomised)} randomised address(es)",
                objectIdentified=False,
                evidenceClass="inferred",
                source="mac-address-form",
                confidence="high",
                detail=(
                    f"{len(randomised)} of the addresses learned through {interface.port} "
                    "are locally administered, meaning they were assigned by software "
                    "rather than a manufacturer. No vendor can be inferred from them."
                ),
                observedAt=self.observed_at,
            ))

        return inferred

    def assertions(self) -> list[TopologyAssertion]:
        result: list[TopologyAssertion] = []
        for interface in self.interfaces:
            observed = self._observed_for(interface)
            if observed:
                result.append(observed)
            result.extend(self._inferred_for(interface))
        return result

    def observed_by_interface(self) -> dict[str, TopologyAssertion]:
        return {
            interface.port: assertion
            for interface in self.interfaces
            if (assertion := self._observed_for(interface)) is not None
        }

    def inferred_by_interface(self) -> dict[str, list[TopologyAssertion]]:
        return {interface.port: self._inferred_for(interface) for interface in self.interfaces}


class IntentProvider:
    """Expected assertions, ranked by how authoritative the source is.

    A statement the operator made in SwitchOps outranks an accepted plan,
    which outranks a description typed into the switch.
    """

    name = "intent"

    _AUTHORITY: dict[str, int] = {
        "user-intent": 0,
        "accepted-plan": 1,
        "interface-description": 2,
    }

    def __init__(
        self,
        *,
        interfaces: Sequence[InterfaceStatus],
        stored: Iterable[ExpectedRelationship] = (),
    ) -> None:
        self.interfaces = list(interfaces)
        self.stored: dict[str, ExpectedRelationship] = {}
        for relationship in stored:
            existing = self.stored.get(relationship.interface)
            if existing is None or self._AUTHORITY.get(relationship.source, 9) < self._AUTHORITY.get(
                existing.source, 9
            ):
                self.stored[relationship.interface] = relationship

    def _expected_for(self, interface: InterfaceStatus) -> Optional[TopologyAssertion]:
        stored = self.stored.get(interface.port)
        if stored is not None:
            label = stored.expected_name
            source: EvidenceSource = (
                "user-intent" if stored.source == "user-intent" else "accepted-plan"
            )
            detail = (
                f"You recorded that {label} should be on {interface.port}."
                if stored.source == "user-intent"
                else f"An accepted plan expects {label} on {interface.port}."
            )
            return TopologyAssertion(
                subject=interface.port,
                relationship="expected-neighbour",
                objectLabel=label,
                objectIdentified=False,  # intent is never an observation
                evidenceClass="expected",
                source=source,
                confidence="high" if stored.source == "user-intent" else "medium",
                detail=detail,
                observedAt=stored.updated_at or stored.created_at,
                deviceType=stored.expected_device_type,
                vendor=stored.expected_vendor,
                model=stored.expected_model,
            )

        description = (interface.name or "").strip()
        if not _meaningful_description(description):
            return None
        category, vendor, model, _stage, _evidence = classify_device(description)
        return TopologyAssertion(
            subject=interface.port,
            relationship="expected-neighbour",
            objectLabel=description,
            objectIdentified=False,
            evidenceClass="expected",
            source="interface-description",
            # A description is the weakest intent SwitchOps accepts.
            confidence="low",
            detail=(
                f"The switch's own description for {interface.port} reads "
                f"{description!r}. That is documentation somebody configured, "
                "not something SwitchOps observed."
            ),
            deviceType=category,
            vendor=vendor,
            model=model,
        )

    def assertions(self) -> list[TopologyAssertion]:
        return [
            assertion
            for interface in self.interfaces
            if (assertion := self._expected_for(interface)) is not None
        ]

    def suppressed_interfaces(self) -> set[str]:
        return {
            interface for interface, relationship in self.stored.items()
            if relationship.suppressed
        }

    def expected_by_interface(self) -> dict[str, TopologyAssertion]:
        return {
            interface.port: assertion
            for interface in self.interfaces
            if (assertion := self._expected_for(interface)) is not None
        }


class HistoryProvider:
    """Historical assertions from the immediately preceding observation."""

    name = "history"

    def __init__(self, previous: Mapping[str, "PreviousInterfaceState"] | None = None) -> None:
        self.previous = dict(previous or {})

    def _historical_for(self, port: str) -> Optional[TopologyAssertion]:
        state = self.previous.get(port)
        if state is None:
            return None
        if state.identity:
            label, identified, detail = (
                state.identity,
                True,
                f"{state.identity} was the announced neighbour on {port} at the previous observation.",
            )
        elif state.connected:
            label, identified = UNIDENTIFIED, False
            detail = (
                f"{port} had an active link at the previous observation"
                + (f" ({state.learned_count} address(es) learned)." if state.learned_count else ".")
            )
        else:
            label, identified = "No link", False
            detail = f"{port} had no link at the previous observation."
        return TopologyAssertion(
            subject=port,
            relationship="expected-neighbour" if not state.connected else "attached-endpoint",
            objectLabel=label,
            objectIdentified=identified,
            evidenceClass="historical",
            source="prior-observation",
            confidence="medium",
            detail=detail,
            observedAt=state.observed_at,
        )

    def assertions(self) -> list[TopologyAssertion]:
        return [
            assertion
            for port in self.previous
            if (assertion := self._historical_for(port)) is not None
        ]

    def historical_by_interface(self) -> dict[str, TopologyAssertion]:
        return {
            port: assertion
            for port in self.previous
            if (assertion := self._historical_for(port)) is not None
        }


class PreviousInterfaceState:
    """Minimal previous-observation facts the reconciler needs."""

    __slots__ = ("connected", "identity", "learned_count", "observed_at")

    def __init__(
        self,
        *,
        connected: bool,
        identity: Optional[str] = None,
        learned_count: int = 0,
        observed_at: Optional[datetime] = None,
    ) -> None:
        self.connected = connected
        self.identity = identity
        self.learned_count = learned_count
        self.observed_at = observed_at


# --- helpers ---------------------------------------------------------------


def _meaningful_description(description: str) -> bool:
    value = (description or "").strip().lower()
    if not value:
        return False
    return not any(token in value for token in ("spare", "unused", "access port", "reserved"))


def _identity_from_cdp(neighbor: CdpNeighbor) -> tuple[DeviceType, Optional[str], Optional[str]]:
    platform = (neighbor.platform or "").strip()
    lowered = f" {platform.lower()} {neighbor.remote_name.lower()} "
    category, vendor, model, _stage, _evidence = classify_device(
        f"{neighbor.remote_name} {platform}"
    )
    if "meraki" in lowered:
        vendor = "Cisco Meraki"
    elif "cisco" in lowered and not vendor:
        vendor = "Cisco"
    match = re.search(r"\b(M[XRSV]\d{2,3}[A-Z]*|WS-[A-Z0-9-]+|C\d{4}[A-Z0-9-]*)\b", platform, re.IGNORECASE)
    if match:
        model = match.group(1).upper()
    elif platform and not model:
        model = platform
    return category, vendor, model


def _matching_sighting(
    expected: Optional[TopologyAssertion],
    sightings: Sequence[ExternalSighting],
) -> Optional[ExternalSighting]:
    """Find an external sighting of the expected device, if any source has one."""
    if expected is None or not sightings:
        return None
    expected_tokens = _assertion_label_tokens(expected)
    for sighting in sightings:
        if _tokens_match(expected_tokens, _tokens(sighting.label)):
            return sighting
    return None


def _assertion_label_tokens(assertion: TopologyAssertion) -> set[str]:
    parts = [assertion.object_label or ""]
    if assertion.vendor:
        parts.append(assertion.vendor)
    if assertion.model:
        parts.append(assertion.model)
    return _tokens(" ".join(parts))


# --- reconciler ------------------------------------------------------------


def reconcile_interface(
    *,
    interface: InterfaceStatus,
    observed: Optional[TopologyAssertion],
    expected: Optional[TopologyAssertion],
    historical: Optional[TopologyAssertion],
    inferred: Sequence[TopologyAssertion] = (),
    external_sightings: Sequence[ExternalSighting] = (),
    suppressed: bool = False,
) -> InterfaceReconciliation:
    """Compare the claims held about one interface. Deterministic."""
    status: ReconciliationStatus
    drift_kind = "none"
    headline: str
    explanation: str

    admin_down = interface.status.strip().lower() == "disabled"

    if suppressed:
        # The operator has muted this interface. The evidence is still gathered
        # and still shown; it just stops asking for a decision.
        status = "not-applicable"
        headline = "Muted"
        explanation = (
            f"You have muted reconciliation for {interface.port}. SwitchOps still "
            "records what it observes here, but will not raise it for attention."
        )
    elif observed is None and expected is None:
        status = "not-applicable"
        headline = "Nothing expected, nothing observed"
        explanation = (
            f"{interface.port} carries no recorded intent and nothing is attached to it."
        )
    elif observed is None:
        sighting = _matching_sighting(expected, external_sightings)
        if sighting is not None:
            # Another source can see the expected device somewhere else, so it
            # is not missing - it has moved.
            status = "drift"
            drift_kind = "location"
            headline = "Location drift"
            explanation = (
                f"{expected.object_label} is expected on {interface.port} but is not "
                f"attached there. {sighting.source} reports it at "
                f"{sighting.observed_location}, so the device is present on the "
                "network in a different place."
            )
        else:
            status = "expected-not-observed"
            headline = "Expected device not observed"
            explanation = (
                f"{expected.object_label} is expected on {interface.port}, but this switch "
                f"currently detects {'no link (the port is administratively disabled)' if admin_down else 'no link on that port'}. "
                "SwitchOps cannot conclude the device is offline - it is only absent from "
                "the place it was expected."
            )
    elif expected is None:
        status = "unexpected"
        headline = "Observed without recorded intent"
        explanation = (
            f"Something is attached to {interface.port}, but no intent records what "
            "should be there. Recording an expectation lets SwitchOps tell you when "
            "it changes."
        )
    elif not observed.object_identified:
        status = "uncertain"
        headline = "Present, identity unconfirmed"
        explanation = (
            f"{interface.port} has a healthy link, so a device is attached, but nothing "
            f"identifies it. The name {expected.object_label!r} comes from "
            f"{'your recorded intent' if expected.source == 'user-intent' else 'the interface description'}, "
            "so SwitchOps can neither confirm nor contradict it."
        )
    else:
        observed_tokens = _assertion_label_tokens(observed)
        expected_tokens = _assertion_label_tokens(expected)
        if _tokens_match(observed_tokens, expected_tokens):
            status = "aligned"
            headline = "Observed matches intent"
            explanation = (
                f"{observed.object_label} announced itself on {interface.port}, which "
                f"matches the expected {expected.object_label}."
            )
        else:
            status = "drift"
            drift_kind = "identity"
            headline = "Topology drift"
            explanation = (
                f"{interface.port} has a healthy link, but the device that announced "
                f"itself ({observed.object_label}) is not the expected "
                f"{expected.object_label}. The link itself is fine; the documented "
                "topology is out of date."
            )

    # "Changed" is orthogonal to alignment: a link can match intent and still
    # differ from the previous observation.
    changed = False
    change_summary: Optional[str] = None
    if historical is not None:
        was_connected = historical.object_label != "No link"
        is_connected = observed is not None
        if was_connected != is_connected:
            changed = True
            change_summary = (
                f"{interface.port} came up since the previous observation."
                if is_connected
                else f"{interface.port} lost its link since the previous observation."
            )
        elif (
            historical.object_identified
            and observed is not None
            and observed.object_identified
            and historical.object_label != observed.object_label
        ):
            changed = True
            change_summary = (
                f"The announced neighbour on {interface.port} changed from "
                f"{historical.object_label} to {observed.object_label}."
            )

    # The switch's own description is stale when active intent came from
    # somewhere more authoritative and disagrees with it.
    documentation_stale = False
    if expected is not None and expected.source in {"user-intent", "accepted-plan"}:
        description = (interface.name or "").strip()
        if _meaningful_description(description) and not _tokens_match(
            _tokens(description), _assertion_label_tokens(expected)
        ):
            documentation_stale = True

    assertions = [item for item in (observed, expected, historical) if item is not None]
    assertions.extend(inferred)
    if drift_kind == "location" and expected is not None:
        sighting = _matching_sighting(expected, external_sightings)
        if sighting is not None:
            assertions.append(TopologyAssertion(
                subject=interface.port,
                relationship="expected-neighbour",
                objectLabel=f"{sighting.label} at {sighting.observed_location}",
                objectIdentified=True,
                evidenceClass="observed",
                source=sighting.source,
                confidence=sighting.confidence,
                detail=sighting.detail or (
                    f"{sighting.source} observed {sighting.label} at "
                    f"{sighting.observed_location}."
                ),
                observedAt=sighting.observed_at,
            ))

    return InterfaceReconciliation(
        interface=interface.port,
        status=status,
        driftKind=drift_kind,  # type: ignore[arg-type]
        headline=headline,
        explanation=explanation,
        observed=observed,
        expected=expected,
        historical=historical,
        inferred=list(inferred),
        changedSincePrevious=changed,
        changeSummary=change_summary,
        assertions=assertions,
        documentationStale=documentation_stale,
    )


def reconcile(
    *,
    device_id: str,
    interfaces: Sequence[InterfaceStatus],
    ios: CiscoIosEvidenceProvider,
    intent: IntentProvider,
    history: HistoryProvider,
    external_sightings: Sequence[ExternalSighting] = (),
    evaluated_at: Optional[datetime] = None,
) -> ReconciliationSummary:
    """Reconcile every interface and summarise the result."""
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    observed = ios.observed_by_interface()
    inferred = ios.inferred_by_interface()
    expected = intent.expected_by_interface()
    historical = history.historical_by_interface()
    suppressed_ports = intent.suppressed_interfaces()

    results: list[InterfaceReconciliation] = []
    for interface in interfaces:
        results.append(reconcile_interface(
            interface=interface,
            observed=observed.get(interface.port),
            expected=expected.get(interface.port),
            historical=historical.get(interface.port),
            inferred=inferred.get(interface.port, []),
            external_sightings=external_sightings,
            suppressed=interface.port in suppressed_ports,
        ))

    counts = {
        "aligned": 0, "drift": 0, "expected-not-observed": 0,
        "unexpected": 0, "uncertain": 0,
    }
    changed = 0
    for result in results:
        if result.status in counts:
            counts[result.status] += 1
        if result.changed_since_previous:
            changed += 1

    attention = bool(counts["drift"] or counts["expected-not-observed"] or counts["unexpected"])
    if attention:
        parts: list[str] = []
        if counts["drift"]:
            parts.append(f"{counts['drift']} topology drift")
        if counts["expected-not-observed"]:
            parts.append(f"{counts['expected-not-observed']} expected but not observed")
        if counts["unexpected"]:
            parts.append(f"{counts['unexpected']} observed without intent")
        headline = ", ".join(parts)
    elif counts["uncertain"]:
        headline = f"{counts['uncertain']} interface(s) present but unidentified"
    elif counts["aligned"]:
        headline = "Observed topology matches recorded intent"
    else:
        headline = "No topology intent recorded yet"

    return ReconciliationSummary(
        evaluatedAt=evaluated_at,
        deviceId=device_id,
        aligned=counts["aligned"],
        drift=counts["drift"],
        expectedNotObserved=counts["expected-not-observed"],
        unexpected=counts["unexpected"],
        uncertain=counts["uncertain"],
        changed=changed,
        attention=attention,
        headline=headline,
        interfaces=results,
    )


# --- events ----------------------------------------------------------------
#
# A discrepancy that is still true on the twentieth refresh is one situation,
# not twenty events. Every reconciliation event is therefore raised from a
# *change of signature*, never from the mere presence of a discrepancy.

# Statuses worth telling the user about. "uncertain" is deliberately absent:
# an unidentifiable neighbour is a standing condition of the evidence
# available, not something that just happened.
_EVENTFUL: frozenset[str] = frozenset({"drift", "expected-not-observed", "unexpected"})

_EVENT_TYPE: dict[str, str] = {
    "drift": "topology_drift_detected",
    "expected-not-observed": "expected_device_missing",
    "unexpected": "unexpected_device_observed",
}

_EVENT_TITLE: dict[str, str] = {
    "drift": "Topology drift on {interface}",
    "expected-not-observed": "Expected device not observed on {interface}",
    "unexpected": "Unrecorded device observed on {interface}",
}


def reconciliation_signature(result: InterfaceReconciliation) -> str:
    """Stable identity for "this exact situation on this interface"."""
    observed = result.observed.object_label if result.observed else "-"
    expected = result.expected.object_label if result.expected else "-"
    return f"{result.status}|{result.drift_kind}|{observed}|{expected}"


def reconciliation_events(
    *,
    device_id: str,
    summary: ReconciliationSummary,
    store: "TopologyIntentStore",
    observed_at: Optional[datetime] = None,
) -> list[NetworkEvent]:
    """Turn reconciliation changes into user-facing events.

    The store holds the previous signature per interface. An unchanged
    discrepancy produces nothing; only appearing, changing shape, or clearing
    produces an event.
    """
    observed_at = observed_at or datetime.now(timezone.utc)
    events: list[NetworkEvent] = []

    for result in summary.interfaces:
        signature = reconciliation_signature(result)
        changed, previous_signature, _first_seen = store.observe_reconciliation(
            device_id=device_id,
            interface=result.interface,
            signature=signature,
            status=result.status,
            observed_label=result.observed.object_label if result.observed else None,
            observed_identified=bool(result.observed and result.observed.object_identified),
            now=observed_at,
        )

        # A neighbour swapping identity between two observations is news in
        # its own right, independent of whether it matches intent.
        if (
            result.changed_since_previous
            and result.change_summary
            and result.historical is not None
            and result.historical.object_identified
            and result.observed is not None
            and result.observed.object_identified
        ):
            events.append(NetworkEvent(
                timestamp=observed_at,
                deviceId=device_id,
                interface=result.interface,
                eventType="direct_neighbor_changed",
                severity="NOTICE",
                title=f"Announced neighbour changed on {result.interface}",
                detail=result.change_summary,
                metadata={
                    "previous": result.historical.object_label,
                    "current": result.observed.object_label,
                },
            ))

        if not changed:
            continue

        was_eventful = bool(
            previous_signature and previous_signature.split("|", 1)[0] in _EVENTFUL
        )

        if result.status in _EVENTFUL:
            events.append(NetworkEvent(
                timestamp=observed_at,
                deviceId=device_id,
                interface=result.interface,
                eventType=_EVENT_TYPE[result.status],
                severity="NOTICE",
                title=_EVENT_TITLE[result.status].format(interface=result.interface),
                detail=result.explanation,
                metadata={
                    "status": result.status,
                    "observed": result.observed.object_label if result.observed else None,
                    "expected": result.expected.object_label if result.expected else None,
                },
            ))
        elif was_eventful:
            events.append(NetworkEvent(
                timestamp=observed_at,
                deviceId=device_id,
                interface=result.interface,
                eventType="topology_reconciliation_resolved",
                severity="HEALTHY",
                title=f"Topology reconciled on {result.interface}",
                detail=(
                    f"{result.interface} no longer shows a topology discrepancy. "
                    f"{result.explanation}"
                ),
                metadata={"status": result.status},
            ))

    return events
