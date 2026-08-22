"""Normalize parsed IOS observations into a device/link model.

The model deliberately separates observations from inference. A MAC address is
strong evidence that a device exists, while an interface description can only
describe an expected or likely device. That distinction prevents the UI from
presenting a labelled port as proof that hardware is attached.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Iterable

from .models import (
    DeviceCapability,
    DeviceType,
    InterfaceStatus,
    MacTableEntry,
    NetworkDevice,
    NetworkInterface,
    NetworkLink,
    PoePort,
    TopologyModel,
)


_CATEGORY_RULES: tuple[tuple[DeviceType, tuple[str, ...]], ...] = (
    ("access-point", ("access point", "wireless", "wifi", "wi-fi", " ap", "mr44")),
    ("router", ("router", "gateway", "firewall", "uplink")),
    ("server", ("server", "nas", "hypervisor")),
    ("desktop", ("desktop", "workstation", " pc")),
    ("laptop", ("laptop", "notebook")),
    ("phone", ("phone", "voip", "iphone", "android")),
    ("tv-media", (" tv", "television", "media", "streaming")),
    ("printer", ("printer",)),
    ("camera", ("camera", "cctv")),
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


def _meaningful_expected_description(description: str) -> bool:
    value = description.strip().lower()
    if not value:
        return False
    return not any(token in value for token in ("spare", "unused", "access port", "reserved"))


def build_topology(
    *,
    hostname: str,
    model: str,
    management_ip: str,
    interfaces: Iterable[InterfaceStatus],
    mac_entries: Iterable[MacTableEntry],
    poe_ports: Iterable[PoePort],
    observed_at: datetime | None = None,
) -> TopologyModel:
    observed_at = observed_at or datetime.now(timezone.utc)
    switch_id = f"switch-{_slug(hostname)}"
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
        )
    ]

    poe_by_port = {port.interface: port for port in poe_ports}
    macs_by_port: dict[str, list[MacTableEntry]] = {}
    for entry in mac_entries:
        if entry.port.upper() == "CPU" or entry.vlan.lower() == "all":
            continue
        macs_by_port.setdefault(entry.port, []).append(entry)

    normalized_interfaces: list[NetworkInterface] = []
    links: list[NetworkLink] = []
    for interface in interfaces:
        poe = poe_by_port.get(interface.port)
        admin_state = interface_admin_state(interface.status)
        oper_state = interface_oper_state(interface.status)
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
            )
        )

        attached = macs_by_port.get(interface.port, [])
        if attached:
            for index, entry in enumerate(attached):
                category, vendor, exact_model, stage, evidence = classify_device(interface.name)
                device_id = _device_id_from_mac(entry.mac)
                device_name = interface.name.strip() or f"Device on {interface.port}"
                device_evidence = ["dynamic MAC learned on switch interface", *evidence]
                device = NetworkDevice(
                    id=device_id,
                    type=category,
                    vendor=vendor,
                    model=exact_model,
                    name=device_name if len(attached) == 1 else f"{device_name} {index + 1}",
                    mac=entry.mac,
                    source="observed",
                    confidence="high" if category != "unknown" else "medium",
                    classificationStage=stage,
                    online=oper_state == "up",
                    connectedInterface=interface.port,
                    visualCategory=category,
                    capabilities=[
                        DeviceCapability(
                            name="poe-powered",
                            available=bool(poe and poe.oper.lower() not in {"", "off", "n/a"}),
                            source="show power inline",
                        )
                    ] if poe else [],
                    lastSeen=observed_at,
                    evidence=device_evidence,
                )
                devices.append(device)
                links.append(
                    NetworkLink(
                        id=f"link-{switch_id}-{_slug(interface.port)}-{device_id}",
                        fromDeviceId=switch_id,
                        fromInterface=interface.port,
                        toDeviceId=device_id,
                        status="up" if oper_state == "up" else "down",
                        speed=interface.speed,
                        poe=bool(poe and poe.oper.lower() not in {"", "off", "n/a"}),
                        confidence="high",
                        evidence=["MAC learned on interface", "interface operational state"],
                    )
                )
        elif _meaningful_expected_description(interface.name):
            category, vendor, exact_model, stage, evidence = classify_device(interface.name)
            expected_id = f"expected-{switch_id}-{_slug(interface.port)}"
            devices.append(
                NetworkDevice(
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
                    evidence=["interface description only; attachment not observed", *evidence],
                )
            )
            links.append(
                NetworkLink(
                    id=f"link-{switch_id}-{_slug(interface.port)}-{expected_id}",
                    fromDeviceId=switch_id,
                    fromInterface=interface.port,
                    toDeviceId=expected_id,
                    status="waiting" if admin_state == "up" else "down",
                    speed=interface.speed,
                    poe=False,
                    confidence="low",
                    evidence=["interface description; no learned MAC"],
                )
            )

    return TopologyModel(
        generatedAt=observed_at,
        rootDeviceId=switch_id,
        devices=devices,
        interfaces=normalized_interfaces,
        links=links,
    )
