"""Normalize parsed IOS observations into a device/link model.

The model deliberately separates *what was observed* from *what it implies*.

The governing networking rule is:

    A MAC address learned through an interface does not prove that the MAC
    belongs to a device physically attached to that interface.

On an access port a learned MAC is usually the attached host. On an uplink or
trunk, dozens of MACs may be learned through a single physical neighbour. The
builder therefore enforces one hard invariant:

    **At most one endpoint device node is created per switch interface.**

Additional learned addresses become ``learned_mac_count`` on the interface, the
link and the endpoint - "N addresses are reachable through this link" - never
extra topology nodes. Interface descriptions can name an *expected* device but
never manufacture an observed one.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Iterable, Sequence

from .models import (
    CdpNeighbor,
    DeviceCapability,
    DeviceType,
    EvidenceLevel,
    IdentitySource,
    InterfaceRole,
    InterfaceStatus,
    LldpNeighbor,
    LocalEndpointStatus,
    MacTableEntry,
    NetworkDevice,
    NetworkInterface,
    NetworkLink,
    PoePort,
    TopologyModel,
)


_CATEGORY_RULES: tuple[tuple[DeviceType, tuple[str, ...]], ...] = (
    ("access-point", ("access point", "wireless", "wifi", "wi-fi", " ap", "mr44")),
    ("router", ("router", "gateway", "firewall", "uplink", "modem", "isp", "wan")),
    ("server", ("server", "nas", "hypervisor")),
    ("desktop", ("desktop", "workstation", " pc")),
    ("laptop", ("laptop", "notebook")),
    ("phone", ("phone", "voip", "iphone", "android")),
    ("tv-media", (" tv", "television", "media", "streaming")),
    ("printer", ("printer",)),
    ("camera", ("camera", "cctv")),
)

# Descriptions that mark a port as facing *upstream* rather than facing a
# single endpoint. An uplink is where many MACs legitimately appear behind one
# physical neighbour, so it drives both layout and evidence handling.
_UPLINK_TOKENS: tuple[str, ...] = (
    "uplink",
    "trunk",
    "wan",
    "isp",
    "modem",
    "router",
    "gateway",
    "upstream",
)

# CDP capability letters that identify what the neighbour says it is.
_CDP_CAPABILITY_CATEGORY: tuple[tuple[str, DeviceType], ...] = (
    ("trans-bridge", "access-point"),
    ("router", "router"),
    ("switch", "switch"),
    ("host", "desktop"),
    ("phone", "phone"),
)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unknown"


def _device_id_from_mac(mac: str) -> str:
    normalized = re.sub(r"[^0-9a-f]", "", mac.lower())
    digest = hashlib.sha256(normalized.encode("ascii", errors="ignore")).hexdigest()[:12]
    return f"device-{digest}"


def interface_admin_state(status: str) -> str:
    value = status.strip().lower()
    if value == "disabled":
        return "down"
    if value:
        return "up"
    return "unknown"


def interface_oper_state(status: str) -> str:
    value = status.strip().lower()
    if value == "connected":
        return "up"
    if value:
        return "down"
    return "unknown"


def classify_device(description: str, *, vendor_hint: str | None = None) -> tuple[
    DeviceType, str | None, str | None, str, list[str]
]:
    """Return category, vendor, model, classification stage, and evidence."""
    text = f" {description.strip().lower()} "
    category: DeviceType = "unknown"
    evidence: list[str] = []
    for candidate, needles in _CATEGORY_RULES:
        if any(needle in text for needle in needles):
            category = candidate
            evidence.append("interface description suggests device category")
            break

    vendor = vendor_hint
    if vendor is None:
        if "meraki" in text:
            vendor = "Cisco Meraki"
        elif "cisco" in text:
            vendor = "Cisco"
    if vendor:
        evidence.append("vendor named by observed metadata")

    model: str | None = None
    model_match = re.search(r"\b(MR\d{2,3})\b", description, re.IGNORECASE)
    if model_match:
        model = model_match.group(1).upper()
        evidence.append("model named by observed metadata")

    if model:
        stage = "model"
    elif vendor:
        stage = "vendor"
    elif category != "unknown":
        stage = "category"
    else:
        stage = "unknown"
    return category, vendor, model, stage, evidence


def classify_interface_role(description: str, vlan: str) -> InterfaceRole:
    """Decide whether an interface faces upstream or faces an endpoint.

    Role is a *layout and interpretation* hint, not a claim about what is
    attached. It never changes how many device nodes are produced.
    """
    if vlan.strip().lower() == "trunk":
        return "uplink"
    text = f" {description.strip().lower()} "
    if not text.strip():
        return "unknown"
    # "Spare Uplink" is an unused port, not an active upstream path.
    if "spare" in text or "unused" in text:
        return "unknown"
    if any(token in text for token in _UPLINK_TOKENS):
        return "uplink"
    return "access"


def _meaningful_expected_description(description: str) -> bool:
    value = description.strip().lower()
    if not value:
        return False
    return not any(token in value for token in ("spare", "unused", "access port", "reserved"))


def _category_from_cdp(neighbor: CdpNeighbor) -> tuple[DeviceType, str | None, str | None]:
    """Derive category/vendor/model from what the neighbour advertised."""
    platform = (neighbor.platform or "").strip()
    capabilities = " ".join(neighbor.capabilities).lower()
    lowered = f" {platform.lower()} {neighbor.remote_name.lower()} "

    category: DeviceType = "unknown"
    for token, candidate in _CDP_CAPABILITY_CATEGORY:
        if token in capabilities:
            category = candidate
            break
    # A platform string is more specific than a capability letter.
    for candidate, needles in _CATEGORY_RULES:
        if any(needle in lowered for needle in needles):
            category = candidate
            break

    vendor: str | None = None
    if "meraki" in lowered:
        vendor = "Cisco Meraki"
    elif "cisco" in lowered:
        vendor = "Cisco"

    model: str | None = None
    model_match = re.search(r"\b(MR\d{2,3}|WS-[A-Z0-9-]+|C\d{4}[A-Z0-9-]*)\b", platform, re.IGNORECASE)
    if model_match:
        model = model_match.group(1).upper()
    elif platform:
        model = platform
    return category, vendor, model


def _category_from_lldp(neighbor: LldpNeighbor) -> tuple[DeviceType, str | None, str | None]:
    advertised = f"{neighbor.remote_name} {neighbor.system_description or ''}".strip()
    category, vendor, model, _stage, _evidence = classify_device(advertised)
    lowered = advertised.lower()
    if "meraki" in lowered:
        vendor = "Cisco Meraki"
    elif "cisco" in lowered and not vendor:
        vendor = "Cisco"
    return category, vendor, model or neighbor.system_description


def _poe_active(poe: PoePort | None) -> bool:
    return bool(poe and poe.oper.strip().lower() not in {"", "off", "n/a", "faulty", "deny"})


def _endpoint_from_cdp(
    *,
    neighbor: CdpNeighbor,
    interface: InterfaceStatus,
    poe: PoePort | None,
    learned_count: int,
    role: InterfaceRole,
    observed_at: datetime,
    switch_id: str,
) -> NetworkDevice:
    category, vendor, model = _category_from_cdp(neighbor)
    evidence = [
        f"{neighbor.remote_name} announced itself through CDP on {interface.port}",
    ]
    if neighbor.platform:
        evidence.append("platform reported by the neighbour")
    if learned_count > 1:
        evidence.append(
            f"{learned_count} addresses are reachable through this link"
        )
    return NetworkDevice(
        id=f"cdp-{switch_id}-{_slug(interface.port)}-{_slug(neighbor.remote_name)}",
        type=category,
        vendor=vendor,
        model=model,
        name=neighbor.remote_name,
        ip=neighbor.ip,
        source="observed",
        confidence="high",
        classificationStage="model" if model else "vendor" if vendor else "category" if category != "unknown" else "unknown",
        online=True,
        connectedInterface=interface.port,
        visualCategory=category,
        capabilities=[
            DeviceCapability(
                name="poe-powered",
                available=_poe_active(poe),
                source="show power inline",
            )
        ] if poe else [],
        lastSeen=observed_at,
        evidence=evidence,
        evidenceLevel="direct",
        identitySource="cdp",
        expectedName=interface.name.strip() or None,
        expectedType=None,
        learnedMacCount=learned_count,
        role=role,
    )


def _endpoint_from_lldp(
    *,
    neighbor: LldpNeighbor,
    interface: InterfaceStatus,
    poe: PoePort | None,
    learned_count: int,
    role: InterfaceRole,
    observed_at: datetime,
    switch_id: str,
) -> NetworkDevice:
    category, vendor, model = _category_from_lldp(neighbor)
    evidence = [f"{neighbor.remote_name} announced itself through LLDP on {interface.port}"]
    if neighbor.system_description:
        evidence.append("system description reported by the neighbour")
    if learned_count > 1:
        evidence.append(f"{learned_count} addresses are reachable through this link")
    return NetworkDevice(
        id=f"lldp-{switch_id}-{_slug(interface.port)}-{_slug(neighbor.remote_name)}",
        type=category,
        vendor=vendor,
        model=model,
        name=neighbor.remote_name,
        ip=neighbor.ip,
        source="observed",
        confidence="high",
        classificationStage="model" if model else "vendor" if vendor else "category" if category != "unknown" else "unknown",
        online=True,
        connectedInterface=interface.port,
        visualCategory=category,
        capabilities=[DeviceCapability(name="poe-powered", available=_poe_active(poe), source="show power inline")] if poe else [],
        lastSeen=observed_at,
        evidence=evidence,
        evidenceLevel="direct",
        identitySource="lldp",
        expectedName=interface.name.strip() or None,
        expectedType=None,
        learnedMacCount=learned_count,
        role=role,
    )


def _endpoint_from_local_host(
    *,
    local_endpoint: LocalEndpointStatus,
    interface: InterfaceStatus,
    poe: PoePort | None,
    role: InterfaceRole,
    observed_at: datetime,
    switch_id: str,
) -> NetworkDevice:
    return NetworkDevice(
        id=f"local-host-{switch_id}-{_slug(interface.port)}",
        type="desktop",
        name=local_endpoint.label,
        ip=local_endpoint.ip,
        source="observed",
        confidence="high",
        classificationStage="category",
        online=True,
        connectedInterface=interface.port,
        visualCategory="desktop",
        capabilities=[DeviceCapability(name="poe-powered", available=_poe_active(poe), source="show power inline")] if poe else [],
        lastSeen=observed_at,
        evidence=[local_endpoint.detail, "interface reports an active access link"],
        evidenceLevel="observed-on-port",
        identitySource="local-host",
        expectedName=interface.name.strip() or None,
        expectedType=None,
        learnedMacCount=1,
        role=role,
    )


def _endpoint_from_learned_macs(
    *,
    interface: InterfaceStatus,
    learned: Sequence[MacTableEntry],
    poe: PoePort | None,
    role: InterfaceRole,
    observed_at: datetime,
    switch_id: str,
) -> NetworkDevice:
    """Build the single endpoint node representing "something is on this port".

    The *existence* of an attached device is well evidenced (the link is up and
    the switch is learning addresses through it). Its *identity* is not, so the
    identity fields degrade honestly and the extra addresses are counted rather
    than duplicated into more nodes.
    """
    learned_count = len(learned)
    described = _meaningful_expected_description(interface.name)
    # The description describes what is *expected*, so it is recorded on the
    # expected facet and never becomes this node's identity. Without a
    # neighbour announcing itself, an attached device is simply unidentified.
    expected_category, _vendor, _model, _stage, _description_evidence = (
        classify_device(interface.name) if described else ("unknown", None, None, "unknown", [])
    )
    category: DeviceType = "unknown"
    vendor = model = None
    stage = "unknown"

    evidence = [
        f"{learned_count} address(es) learned through {interface.port}",
        "interface reports an active link",
    ]
    identity_source: IdentitySource = "none"
    if described:
        evidence.append(
            f"the interface description reads {interface.name.strip()!r}; that is "
            "documentation of what is expected, not an identification of what is attached"
        )
    if learned_count > 1:
        evidence.append(
            "several addresses are reachable through this link, so further devices sit behind it"
        )

    name = "Unidentified device"

    # A single learned address can be attributed to the endpoint. Several
    # cannot: attributing one of many would be a guess.
    attributable_mac = learned[0].mac if learned_count == 1 else None
    device_id = (
        _device_id_from_mac(attributable_mac)
        if attributable_mac
        else f"endpoint-{switch_id}-{_slug(interface.port)}"
    )

    # Identity confidence, not existence confidence. Nothing identifies this
    # device, so identity confidence is low however good the link is.
    confidence = "low"

    return NetworkDevice(
        id=device_id,
        type=category,
        vendor=vendor,
        model=model,
        name=name,
        mac=attributable_mac,
        source="observed",
        confidence=confidence,
        classificationStage=stage,
        online=True,
        connectedInterface=interface.port,
        visualCategory=category,
        capabilities=[
            DeviceCapability(
                name="poe-powered",
                available=_poe_active(poe),
                source="show power inline",
            )
        ] if poe else [],
        lastSeen=observed_at,
        evidence=evidence,
        evidenceLevel="observed-on-port",
        identitySource=identity_source,
        expectedName=interface.name.strip() if described else None,
        expectedType=expected_category if described else None,
        learnedMacCount=learned_count,
        role=role,
    )


def build_topology(
    *,
    hostname: str,
    model: str,
    management_ip: str,
    interfaces: Iterable[InterfaceStatus],
    mac_entries: Iterable[MacTableEntry],
    poe_ports: Iterable[PoePort],
    cdp_neighbors: Iterable[CdpNeighbor] = (),
    lldp_neighbors: Iterable[LldpNeighbor] = (),
    local_endpoint: LocalEndpointStatus | None = None,
    observed_at: datetime | None = None,
    source_namespace: str = "physical",
) -> TopologyModel:
    observed_at = observed_at or datetime.now(timezone.utc)
    # Mock and physical observations must never share a history/config identity.
    switch_id = f"switch-{_slug(source_namespace)}-{_slug(hostname)}"
    devices: list[NetworkDevice] = [
        NetworkDevice(
            id=switch_id,
            type="switch",
            vendor="Cisco" if "cisco" in model.lower() or model.upper().startswith("WS-") else None,
            model=model or None,
            name=hostname,
            ip=management_ip if management_ip != "Unknown" else None,
            source="observed",
            confidence="high",
            classificationStage="model" if model and model != "Unknown" else "category",
            online=True,
            visualCategory="switch",
            capabilities=[
                DeviceCapability(name="managed", source="authenticated IOS session"),
                DeviceCapability(name="poe", source="show power inline"),
            ],
            lastSeen=observed_at,
            evidence=["authenticated IOS telemetry"],
            evidenceLevel="direct",
            identitySource="switch-telemetry",
        )
    ]

    poe_by_port = {port.interface: port for port in poe_ports}
    macs_by_port: dict[str, list[MacTableEntry]] = {}
    for entry in mac_entries:
        if entry.port.upper() == "CPU" or entry.vlan.lower() == "all":
            continue
        macs_by_port.setdefault(entry.port, []).append(entry)

    cdp_by_port: dict[str, list[CdpNeighbor]] = {}
    for neighbor in cdp_neighbors:
        if not neighbor.local_interface:
            continue
        cdp_by_port.setdefault(neighbor.local_interface, []).append(neighbor)

    lldp_by_port: dict[str, list[LldpNeighbor]] = {}
    for neighbor in lldp_neighbors:
        if not neighbor.local_interface:
            continue
        lldp_by_port.setdefault(neighbor.local_interface, []).append(neighbor)

    normalized_interfaces: list[NetworkInterface] = []
    links: list[NetworkLink] = []
    for interface in interfaces:
        poe = poe_by_port.get(interface.port)
        admin_state = interface_admin_state(interface.status)
        oper_state = interface_oper_state(interface.status)
        role = classify_interface_role(interface.name, interface.vlan)
        learned = macs_by_port.get(interface.port, [])
        learned_count = len(learned)
        neighbors = cdp_by_port.get(interface.port, [])
        lldp_neighbors_on_port = lldp_by_port.get(interface.port, [])
        local_match = bool(
            local_endpoint
            and local_endpoint.state == "confirmed"
            and local_endpoint.interface == interface.port
        )

        normalized_interfaces.append(
            NetworkInterface(
                id=f"{switch_id}:{interface.port}",
                deviceId=switch_id,
                port=interface.port,
                description=interface.name,
                adminState=admin_state,
                operState=oper_state,
                speed=interface.speed,
                duplex=interface.duplex,
                vlan=interface.vlan,
                poeCapable=poe is not None,
                poeState=poe.oper if poe else "not-supported",
                poeWatts=poe.power_watts if poe else 0,
                protected=interface.protected,
                role=role,
                learnedMacCount=learned_count,
            )
        )

        endpoint: NetworkDevice | None = None
        link_status: str = "down"
        link_confidence: str = "low"
        link_evidence_level: EvidenceLevel = "unknown"
        link_evidence: list[str] = []

        if neighbors and oper_state == "up":
            # Strongest evidence: the neighbour identified itself on the wire.
            endpoint = _endpoint_from_cdp(
                neighbor=neighbors[0],
                interface=interface,
                poe=poe,
                learned_count=learned_count,
                role=role,
                observed_at=observed_at,
                switch_id=switch_id,
            )
            link_status = "up"
            link_confidence = "high"
            link_evidence_level = "direct"
            link_evidence = ["CDP neighbour announced on this interface", "interface operational state"]
            if len(neighbors) > 1:
                link_evidence.append(
                    f"{len(neighbors)} CDP neighbours are visible through this interface"
                )
        elif lldp_neighbors_on_port and oper_state == "up":
            endpoint = _endpoint_from_lldp(
                neighbor=lldp_neighbors_on_port[0],
                interface=interface,
                poe=poe,
                learned_count=learned_count,
                role=role,
                observed_at=observed_at,
                switch_id=switch_id,
            )
            link_status = "up"
            link_confidence = "high"
            link_evidence_level = "direct"
            link_evidence = ["LLDP neighbour announced on this interface", "interface operational state"]
            if len(lldp_neighbors_on_port) > 1:
                link_evidence.append(
                    f"{len(lldp_neighbors_on_port)} LLDP neighbours are visible through this interface"
                )
        elif local_match and oper_state == "up":
            assert local_endpoint is not None
            endpoint = _endpoint_from_local_host(
                local_endpoint=local_endpoint,
                interface=interface,
                poe=poe,
                role=role,
                observed_at=observed_at,
                switch_id=switch_id,
            )
            link_status = "up"
            link_confidence = "high"
            link_evidence_level = "observed-on-port"
            link_evidence = ["unique local-host and MAC-table correlation", "interface operational state"]
        elif learned and oper_state == "up":
            # Something is attached: link up and addresses are being learned.
            # Exactly one node, regardless of how many addresses appeared.
            endpoint = _endpoint_from_learned_macs(
                interface=interface,
                learned=learned,
                poe=poe,
                role=role,
                observed_at=observed_at,
                switch_id=switch_id,
            )
            link_status = "up"
            link_confidence = "high"  # the link itself is certain
            link_evidence_level = "observed-on-port"
            link_evidence = [
                "interface operational state",
                f"{learned_count} address(es) learned through this interface",
            ]
            if learned_count > 1:
                link_evidence.append(
                    "additional addresses are reachable behind this link, not attached to this port"
                )
        elif _meaningful_expected_description(interface.name):
            # Description only. Never presented as a discovered device.
            category, vendor, exact_model, stage, description_evidence = classify_device(
                interface.name
            )
            expected_id = f"expected-{switch_id}-{_slug(interface.port)}"
            endpoint = NetworkDevice(
                id=expected_id,
                type=category,
                vendor=vendor,
                model=exact_model,
                name=interface.name.strip(),
                source="expected",
                confidence="medium" if category != "unknown" else "low",
                classificationStage=stage,
                online=False,
                connectedInterface=interface.port,
                visualCategory=category,
                capabilities=[],
                evidence=[
                    "interface description only; attachment not observed",
                    *description_evidence,
                ],
                evidenceLevel="expected",
                identitySource="interface-description",
                expectedName=interface.name.strip(),
                expectedType=category,
                learnedMacCount=0,
                role=role,
            )
            link_status = "waiting" if admin_state == "up" else "down"
            link_confidence = "low"
            link_evidence_level = "expected"
            link_evidence = ["interface description; nothing observed on the interface"]

        if endpoint is None:
            continue

        devices.append(endpoint)
        links.append(
            NetworkLink(
                id=f"link-{switch_id}-{_slug(interface.port)}-{endpoint.id}",
                fromDeviceId=switch_id,
                fromInterface=interface.port,
                toDeviceId=endpoint.id,
                toInterface=(
                    neighbors[0].remote_interface
                    if neighbors and link_evidence_level == "direct"
                    else None
                ),
                status=link_status,  # type: ignore[arg-type]
                speed=interface.speed,
                poe=_poe_active(poe),
                confidence=link_confidence,  # type: ignore[arg-type]
                evidence=link_evidence,
                evidenceLevel=link_evidence_level,
                learnedMacCount=learned_count,
            )
        )

    return TopologyModel(
        generatedAt=observed_at,
        rootDeviceId=switch_id,
        devices=devices,
        interfaces=normalized_interfaces,
        links=links,
    )
