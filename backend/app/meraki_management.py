"""Compact Meraki context for management-path assurance.

Provider responses are normalized immediately.  Raw client identities, MAC
addresses, reservations, administrator data, and provider payloads never enter
these models or durable storage.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, Field


MerakiEvidenceState = Literal[
    "not-configured",
    "healthy",
    "partial",
    "unavailable",
]
EvidenceFreshness = Literal["current", "aging", "stale", "historical"]
DhcpMode = Literal["server", "relay", "disabled", "unknown"]
PortMode = Literal["access", "trunk", "unknown"]


class MerakiLanEvidence(BaseModel):
    vlan_id: str | None = Field(default=None, alias="vlanId")
    subnet: str
    appliance_ip: str | None = Field(default=None, alias="applianceIp")
    dhcp_mode: DhcpMode = Field(default="unknown", alias="dhcpMode")
    dhcp_relay_server_count: int = Field(
        default=0, alias="dhcpRelayServerCount", ge=0
    )
    dhcp_lease_time: str | None = Field(default=None, alias="dhcpLeaseTime")
    reserved_range_count: int = Field(default=0, alias="reservedRangeCount", ge=0)
    fixed_assignment_count: int = Field(
        default=0, alias="fixedAssignmentCount", ge=0
    )

    model_config = {"populate_by_name": True}


class MerakiPortEvidence(BaseModel):
    port_id: str = Field(alias="portId")
    enabled: bool | None = None
    mode: PortMode = "unknown"
    access_vlan: str | None = Field(default=None, alias="accessVlan")
    native_vlan: str | None = Field(default=None, alias="nativeVlan")
    allowed_vlans: list[str] = Field(default_factory=list, alias="allowedVlans")
    catalyst_facing: bool | None = Field(default=None, alias="catalystFacing")

    model_config = {"populate_by_name": True}


class MerakiManagementEvidence(BaseModel):
    source: Literal["meraki-dashboard-current-configuration"] = (
        "meraki-dashboard-current-configuration"
    )
    state: MerakiEvidenceState
    checked_at: datetime = Field(alias="checkedAt")
    observed_at: datetime | None = Field(default=None, alias="observedAt")
    freshness: EvidenceFreshness = "historical"
    complete: bool = False
    detail: str
    failed_operations: list[str] = Field(
        default_factory=list, alias="failedOperations"
    )
    vlans_enabled: bool | None = Field(default=None, alias="vlansEnabled")
    lans: list[MerakiLanEvidence] = Field(default_factory=list)
    ports: list[MerakiPortEvidence] = Field(default_factory=list)
    catalyst_port_identified: bool = Field(
        default=False, alias="catalystPortIdentified"
    )

    model_config = {"populate_by_name": True}

    @classmethod
    def unavailable(
        cls,
        *,
        checked_at: datetime,
        state: MerakiEvidenceState,
        detail: str,
        failed_operations: list[str] | None = None,
    ) -> "MerakiManagementEvidence":
        return cls(
            state=state,
            checkedAt=checked_at,
            detail=detail,
            failedOperations=failed_operations or [],
        )

    def with_runtime_health(
        self,
        *,
        state: MerakiEvidenceState,
        checked_at: datetime,
        detail: str,
        complete: bool,
        failed_operations: list[str],
        now: datetime | None = None,
    ) -> "MerakiManagementEvidence":
        reference = now or datetime.now(timezone.utc)
        observed = self.observed_at
        freshness: EvidenceFreshness = "historical"
        if observed is not None:
            age = max(timedelta(), reference - observed)
            if age <= timedelta(minutes=15):
                freshness = "current"
            elif age <= timedelta(hours=6):
                freshness = "aging"
            else:
                freshness = "stale"
        return self.model_copy(
            update={
                "state": state,
                "checked_at": checked_at,
                "freshness": freshness,
                "detail": detail,
                "complete": complete,
                "failed_operations": failed_operations,
            }
        )


def _safe_network(value: object) -> str | None:
    try:
        return str(ipaddress.ip_network(str(value), strict=False))
    except ValueError:
        return None


def _safe_address(value: object) -> str | None:
    try:
        return str(ipaddress.ip_address(str(value)))
    except ValueError:
        return None


def _dhcp_mode(value: object) -> DhcpMode:
    normalized = str(value or "").strip().casefold()
    if normalized == "run a dhcp server":
        return "server"
    if normalized == "relay dhcp to another server":
        return "relay"
    if normalized == "do not respond to dhcp requests":
        return "disabled"
    return "unknown"


def normalize_lans(
    *,
    vlans_enabled: bool | None,
    raw_lans: list[dict],
) -> list[MerakiLanEvidence]:
    """Retain only address-plan and bounded DHCP facts."""
    result: list[MerakiLanEvidence] = []
    for item in raw_lans:
        subnet = _safe_network(item.get("subnet"))
        if subnet is None:
            continue
        vlan_id = str(item.get("id") or "").strip() or None
        if vlans_enabled is False:
            vlan_id = None
        relay = item.get("dhcpRelayServerIps")
        reserved = item.get("reservedIpRanges")
        fixed = item.get("fixedIpAssignments")
        result.append(
            MerakiLanEvidence(
                vlanId=vlan_id,
                subnet=subnet,
                applianceIp=_safe_address(item.get("applianceIp")),
                dhcpMode=_dhcp_mode(item.get("dhcpHandling")),
                dhcpRelayServerCount=len(relay) if isinstance(relay, list) else 0,
                dhcpLeaseTime=(
                    str(item.get("dhcpLeaseTime"))[:40]
                    if item.get("dhcpLeaseTime")
                    else None
                ),
                reservedRangeCount=len(reserved) if isinstance(reserved, list) else 0,
                fixedAssignmentCount=len(fixed) if isinstance(fixed, dict) else 0,
            )
        )
    return result


def _vlan_tokens(value: object) -> list[str]:
    raw = str(value or "").strip().casefold()
    if raw == "all":
        return ["all"]
    result: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if re.fullmatch(r"\d{1,4}(?:-\d{1,4})?", token):
            result.append(token)
    return result[:128]


def normalize_ports(raw_ports: list[dict]) -> list[MerakiPortEvidence]:
    result: list[MerakiPortEvidence] = []
    for item in raw_ports:
        port_id = str(item.get("number") or "").strip()
        if not port_id or len(port_id) > 32:
            continue
        mode_value = str(item.get("type") or "").strip().casefold()
        mode: PortMode = mode_value if mode_value in {"access", "trunk"} else "unknown"  # type: ignore[assignment]
        vlan = str(item.get("vlan") or "").strip() or None
        result.append(
            MerakiPortEvidence(
                portId=port_id,
                enabled=item.get("enabled") if isinstance(item.get("enabled"), bool) else None,
                mode=mode,
                accessVlan=vlan if mode == "access" else None,
                nativeVlan=vlan if mode == "trunk" else None,
                allowedVlans=_vlan_tokens(item.get("allowedVlans")) if mode == "trunk" else [],
            )
        )
    return result


def port_identifier(value: object) -> str:
    """Normalize common Dashboard local-port spellings without guessing links."""
    raw = str(value or "").strip().casefold()
    match = re.fullmatch(r"(?:port|eth|ethernet)?\s*(\d{1,3})", raw)
    return match.group(1) if match else raw
