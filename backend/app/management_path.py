"""Read-only assurance for the Windows-to-Catalyst management path.

The HTTP API cannot supply a target or a command.  The target always comes
from the existing credential store and the Windows collector executes one
fixed PowerShell program.  This module has no mutation primitive.
"""
from __future__ import annotations

from datetime import datetime, timezone
import errno
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
from threading import Lock
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from .config import DATA_DIR
from .configuration_history import (
    ConfigurationHistoryStore,
    get_configuration_history_store,
)
from .discovery_store import DiscoveryHistoryStore, get_discovery_store
from .file_security import harden_private_file
from .meraki_management import MerakiManagementEvidence
from .recovery_plan import RecoveryPlan, build_recovery_plan
from .telemetry_store import TelemetryStore, get_telemetry_store


logger = logging.getLogger(__name__)


PathConclusion = Literal[
    "MANAGEMENT_PATH_HEALTHY",
    "HOST_NETWORK_CHANGED",
    "HOST_ROUTE_MISSING",
    "HOST_PATH_DEGRADED",
    "SSH_SERVICE_UNAVAILABLE",
    "DEVICE_OR_PATH_UNREACHABLE",
    "AUTHENTICATION_FAILED",
    "HOST_KEY_CHANGED",
    "SSH_NEGOTIATION_FAILED",
    "INDETERMINATE",
]
Confidence = Literal["HIGH", "MEDIUM", "LOW", "INDETERMINATE"]
TcpState = Literal["reachable", "refused", "timed_out", "unreachable", "unavailable"]
RouteKind = Literal["connected", "scoped", "default", "none", "unknown"]


class ManagementRoute(BaseModel):
    destination_prefix: str | None = Field(default=None, alias="destinationPrefix")
    next_hop: str | None = Field(default=None, alias="nextHop")
    kind: RouteKind = "unknown"
    route_metric: int | None = Field(default=None, alias="routeMetric")
    protocol: str | None = None

    model_config = {"populate_by_name": True}


class HostAddressObservation(BaseModel):
    address: str
    prefix_length: int = Field(alias="prefixLength", ge=0, le=32)
    prefix_origin: str | None = Field(default=None, alias="prefixOrigin")
    address_state: str | None = Field(default=None, alias="addressState")
    skip_as_source: bool | None = Field(default=None, alias="skipAsSource")

    model_config = {"populate_by_name": True}


class ManagementPathObservation(BaseModel):
    observed_at: datetime = Field(alias="observedAt")
    supported: bool = True
    collection_error: str | None = Field(default=None, alias="collectionError")
    adapter_id: str | None = Field(default=None, alias="adapterId")
    adapter_name: str | None = Field(default=None, alias="adapterName")
    interface_index: int | None = Field(default=None, alias="interfaceIndex")
    interface_metric: int | None = Field(default=None, alias="interfaceMetric")
    adapter_state: str | None = Field(default=None, alias="adapterState")
    source_ip: str | None = Field(default=None, alias="sourceIp")
    prefix_length: int | None = Field(default=None, alias="prefixLength")
    connected_prefix: str | None = Field(default=None, alias="connectedPrefix")
    target_on_connected_prefix: bool | None = Field(
        default=None, alias="targetOnConnectedPrefix"
    )
    dhcp_enabled: bool | None = Field(default=None, alias="dhcpEnabled")
    dhcp_static_coexistence: bool | None = Field(
        default=None, alias="dhcpStaticCoexistence"
    )
    adapter_addresses: list[HostAddressObservation] = Field(
        default_factory=list, alias="adapterAddresses"
    )
    dhcp_server: str | None = Field(default=None, alias="dhcpServer")
    dhcp_lease_obtained: datetime | None = Field(
        default=None, alias="dhcpLeaseObtained"
    )
    dhcp_lease_expires: datetime | None = Field(
        default=None, alias="dhcpLeaseExpires"
    )
    default_gateway: str | None = Field(default=None, alias="defaultGateway")
    route: ManagementRoute
    target_neighbor_state: str | None = Field(
        default=None, alias="targetNeighborState"
    )
    gateway_neighbor_state: str | None = Field(
        default=None, alias="gatewayNeighborState"
    )
    windows_connectivity: str | None = Field(
        default=None, alias="windowsConnectivity"
    )
    tcp22: TcpState = "unavailable"
    icmp_reachable: bool | None = Field(default=None, alias="icmpReachable")

    model_config = {"populate_by_name": True}


class LastKnownManagementPath(BaseModel):
    observed_at: datetime = Field(alias="observedAt")
    last_device_success_at: datetime | None = Field(
        default=None, alias="lastDeviceSuccessAt"
    )
    adapter_id: str | None = Field(default=None, alias="adapterId")
    adapter_name: str | None = Field(default=None, alias="adapterName")
    source_ip: str | None = Field(default=None, alias="sourceIp")
    prefix_length: int | None = Field(default=None, alias="prefixLength")
    connected_prefix: str | None = Field(default=None, alias="connectedPrefix")
    management_prefix: str | None = Field(default=None, alias="managementPrefix")
    default_gateway: str | None = Field(default=None, alias="defaultGateway")
    catalyst_gateway: str | None = Field(default=None, alias="catalystGateway")
    dhcp_server: str | None = Field(default=None, alias="dhcpServer")
    catalyst_interface: str | None = Field(default=None, alias="catalystInterface")
    same_adapter_as_current: bool | None = Field(
        default=None, alias="sameAdapterAsCurrent"
    )
    provenance: list[str] = Field(default_factory=list)
    freshness: Literal["current", "aging", "stale", "historical"] = "historical"

    model_config = {"populate_by_name": True}


class ManagementPathDiagnosis(BaseModel):
    conclusion: PathConclusion
    confidence: Confidence
    headline: str
    summary: str
    evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list, alias="missingEvidence")

    model_config = {"populate_by_name": True}


class ManagementPathResponse(BaseModel):
    target_label: str = Field(default="Configured Catalyst", alias="targetLabel")
    current: ManagementPathObservation
    last_known_good: LastKnownManagementPath | None = Field(
        default=None, alias="lastKnownGood"
    )
    diagnosis: ManagementPathDiagnosis
    meraki_evidence: MerakiManagementEvidence = Field(alias="merakiEvidence")
    recovery_plan: RecoveryPlan = Field(alias="recoveryPlan")
    remediation_available: bool = Field(default=False, alias="remediationAvailable")

    model_config = {"populate_by_name": True}


class _ObservedPath:
    def __init__(self, public: ManagementPathObservation, *, adapter_mac: str | None) -> None:
        self.public = public
        self.adapter_mac = _normalize_mac(adapter_mac)


_WINDOWS_OBSERVATION_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$target = $env:SWITCHOPS_MANAGEMENT_TARGET
$selection = @(Find-NetRoute -RemoteIPAddress $target -ErrorAction Stop)
$source = $selection | Where-Object { $_.PSObject.Properties.Name -contains 'IPAddress' } | Select-Object -First 1
$route = $selection | Where-Object { $_.PSObject.Properties.Name -contains 'DestinationPrefix' } | Select-Object -First 1
if (-not $source) {
  [pscustomobject]@{
    interfaceIndex = $null
    adapterName = $null
    adapterGuid = $null
    adapterMac = $null
    adapterState = $null
    interfaceMetric = $null
    sourceIp = $null
    prefixLength = $null
    adapterAddresses = @()
    routePrefix = if ($route) { [string]$route.DestinationPrefix } else { $null }
    nextHop = if ($route) { [string]$route.NextHop } else { $null }
    routeMetric = if ($route) { [int]$route.RouteMetric } else { $null }
    routeProtocol = if ($route) { [string]$route.Protocol } else { $null }
    dhcpEnabled = $null
    dhcpStaticCoexistence = $null
    dhcpServer = $null
    leaseObtained = $null
    leaseExpires = $null
    defaultGateway = $null
    targetNeighborState = $null
    gatewayNeighborState = $null
    windowsConnectivity = $null
  } | ConvertTo-Json -Compress
  exit 0
}
$index = [int]$source.InterfaceIndex
$adapter = Get-NetAdapter -InterfaceIndex $index -ErrorAction Stop
$configuration = Get-NetIPConfiguration -InterfaceIndex $index -ErrorAction Stop
$ipInterface = Get-NetIPInterface -AddressFamily IPv4 -InterfaceIndex $index -ErrorAction Stop
$allAddresses = @(
  Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $index -ErrorAction Stop |
    ForEach-Object {
      [pscustomobject]@{
        address = [string]$_.IPAddress
        prefixLength = [int]$_.PrefixLength
        prefixOrigin = [string]$_.PrefixOrigin
        addressState = [string]$_.AddressState
        skipAsSource = [bool]$_.SkipAsSource
      }
    }
)
$dhcp = Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "InterfaceIndex=$index" -ErrorAction SilentlyContinue
$profile = Get-NetConnectionProfile -InterfaceIndex $index -ErrorAction SilentlyContinue
$targetNeighbor = Get-NetNeighbor -AddressFamily IPv4 -InterfaceIndex $index -IPAddress $target -ErrorAction SilentlyContinue | Select-Object -First 1
$gateway = [string]($configuration.IPv4DefaultGateway.NextHop | Select-Object -First 1)
$gatewayNeighbor = if ($gateway) { Get-NetNeighbor -AddressFamily IPv4 -InterfaceIndex $index -IPAddress $gateway -ErrorAction SilentlyContinue | Select-Object -First 1 } else { $null }
$leaseObtained = if ($dhcp.DHCPLeaseObtained) { $dhcp.DHCPLeaseObtained.ToUniversalTime().ToString('o') } else { $null }
$leaseExpires = if ($dhcp.DHCPLeaseExpires) { $dhcp.DHCPLeaseExpires.ToUniversalTime().ToString('o') } else { $null }
$coexistence = $null
$interfaceDetail = netsh.exe interface ipv4 show interface interface=$index level=verbose 2>$null | Out-String
if ($interfaceDetail -match 'DHCP/Static IP coexistence\s*:\s*(enabled|disabled)') {
  $coexistence = $Matches[1] -ieq 'enabled'
}
[pscustomobject]@{
  interfaceIndex = $index
  adapterName = [string]$adapter.Name
  adapterGuid = [string]$adapter.InterfaceGuid
  adapterMac = [string]$adapter.MacAddress
  adapterState = [string]$adapter.Status
  interfaceMetric = [int]$ipInterface.InterfaceMetric
  sourceIp = [string]$source.IPAddress
  prefixLength = [int]$source.PrefixLength
  adapterAddresses = $allAddresses
  routePrefix = if ($route) { [string]$route.DestinationPrefix } else { $null }
  nextHop = if ($route) { [string]$route.NextHop } else { $null }
  routeMetric = if ($route) { [int]$route.RouteMetric } else { $null }
  routeProtocol = if ($route) { [string]$route.Protocol } else { $null }
  dhcpEnabled = if ($dhcp) { [bool]$dhcp.DHCPEnabled } else { $null }
  dhcpStaticCoexistence = $coexistence
  dhcpServer = if ($dhcp) { [string]$dhcp.DHCPServer } else { $null }
  leaseObtained = $leaseObtained
  leaseExpires = $leaseExpires
  defaultGateway = $gateway
  targetNeighborState = if ($targetNeighbor) { [string]$targetNeighbor.State } else { $null }
  gatewayNeighborState = if ($gatewayNeighbor) { [string]$gatewayNeighbor.State } else { $null }
  windowsConnectivity = if ($profile) { [string]$profile.IPv4Connectivity } else { $null }
} | ConvertTo-Json -Compress
"""


def _normalize_mac(value: str | None) -> str | None:
    normalized = "".join(char for char in (value or "").lower() if char in "0123456789abcdef")
    return normalized if len(normalized) == 12 else None


def _parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _adapter_addresses(value: Any) -> list[HostAddressObservation]:
    items = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    result: list[HostAddressObservation] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        address = str(item.get("address") or "")
        prefix_length = _optional_int(item.get("prefixLength"))
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if not isinstance(parsed, ipaddress.IPv4Address) or prefix_length is None:
            continue
        try:
            result.append(
                HostAddressObservation(
                    address=str(parsed),
                    prefixLength=prefix_length,
                    prefixOrigin=str(item.get("prefixOrigin") or "") or None,
                    addressState=str(item.get("addressState") or "") or None,
                    skipAsSource=(
                        item.get("skipAsSource")
                        if isinstance(item.get("skipAsSource"), bool)
                        else None
                    ),
                )
            )
        except ValueError:
            continue
    return result


def _route_kind(prefix: str | None, next_hop: str | None) -> RouteKind:
    if not prefix:
        return "none"
    if prefix == "0.0.0.0/0":
        return "default"
    if not next_hop or next_hop == "0.0.0.0":
        return "connected"
    return "scoped"


def _tcp_probe(target: str, port: int, source_ip: str | None) -> TcpState:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1.5)
    try:
        if source_ip:
            probe.bind((source_ip, 0))
        probe.connect((target, port))
        return "reachable"
    except ConnectionRefusedError:
        return "refused"
    except TimeoutError:
        return "timed_out"
    except OSError as exc:
        if exc.errno in {errno.ECONNREFUSED, 10061}:
            return "refused"
        if exc.errno in {errno.ETIMEDOUT, 10060}:
            return "timed_out"
        if exc.errno in {
            errno.ENETUNREACH,
            errno.EHOSTUNREACH,
            errno.ENETDOWN,
            10050,
            10051,
            10065,
        }:
            return "unreachable"
        return "unavailable"
    finally:
        probe.close()


def _icmp_probe(target: str, source_ip: str | None) -> bool | None:
    if os.name != "nt":
        return None
    args = ["ping.exe", "-n", "1", "-w", "900"]
    if source_ip:
        args.extend(["-S", source_ip])
    args.append(target)
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            timeout=2.5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return None


class WindowsManagementPathObserver:
    """Collect the one route Windows selected for the configured device."""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _target_ip(target: str) -> str:
        try:
            return str(ipaddress.ip_address(target))
        except ValueError:
            addresses = socket.getaddrinfo(target, 22, socket.AF_INET, socket.SOCK_STREAM)
            if not addresses:
                raise OSError("The configured target did not resolve to IPv4.")
            return str(ipaddress.ip_address(addresses[0][4][0]))

    def observe(self, target: str) -> _ObservedPath:
        observed_at = self._now()
        if os.name != "nt":
            return _ObservedPath(
                ManagementPathObservation(
                    observedAt=observed_at,
                    supported=False,
                    route=ManagementRoute(kind="unknown"),
                ),
                adapter_mac=None,
            )
        try:
            target_ip = self._target_ip(target)
        except (OSError, ValueError):
            return _ObservedPath(
                ManagementPathObservation(
                    observedAt=observed_at,
                    supported=True,
                    collectionError="target_resolution_failed",
                    route=ManagementRoute(kind="unknown"),
                ),
                adapter_mac=None,
            )
        environment = os.environ.copy()
        environment["SWITCHOPS_MANAGEMENT_TARGET"] = target_ip
        try:
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    _WINDOWS_OBSERVATION_SCRIPT,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8.0,
                env=environment,
                shell=False,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0:
                raise OSError("Windows route observation failed.")
            raw = json.loads(completed.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return _ObservedPath(
                ManagementPathObservation(
                    observedAt=observed_at,
                    supported=True,
                    collectionError="windows_observation_failed",
                    route=ManagementRoute(kind="unknown"),
                ),
                adapter_mac=None,
            )

        source_ip = str(raw.get("sourceIp") or "") or None
        prefix_length = _optional_int(raw.get("prefixLength"))
        connected_prefix: str | None = None
        target_on_prefix: bool | None = None
        if source_ip and prefix_length is not None:
            try:
                connected = ipaddress.ip_network(f"{source_ip}/{prefix_length}", strict=False)
                connected_prefix = str(connected)
                target_on_prefix = ipaddress.ip_address(target_ip) in connected
            except ValueError:
                pass
        adapter_seed = str(raw.get("adapterGuid") or raw.get("adapterName") or "")
        adapter_id = (
            f"adapter-{hashlib.sha256(adapter_seed.casefold().encode('utf-8')).hexdigest()[:16]}"
            if adapter_seed
            else None
        )
        route_prefix = str(raw.get("routePrefix") or "") or None
        next_hop = str(raw.get("nextHop") or "") or None
        observation = ManagementPathObservation(
            observedAt=observed_at,
            adapterId=adapter_id,
            adapterName=str(raw.get("adapterName") or "") or None,
            interfaceIndex=_optional_int(raw.get("interfaceIndex")),
            interfaceMetric=_optional_int(raw.get("interfaceMetric")),
            adapterState=str(raw.get("adapterState") or "") or None,
            sourceIp=source_ip,
            prefixLength=prefix_length,
            adapterAddresses=_adapter_addresses(raw.get("adapterAddresses")),
            connectedPrefix=connected_prefix,
            targetOnConnectedPrefix=target_on_prefix,
            dhcpEnabled=raw.get("dhcpEnabled"),
            dhcpStaticCoexistence=(
                raw.get("dhcpStaticCoexistence")
                if isinstance(raw.get("dhcpStaticCoexistence"), bool)
                else None
            ),
            dhcpServer=str(raw.get("dhcpServer") or "") or None,
            dhcpLeaseObtained=_parse_datetime(raw.get("leaseObtained")),
            dhcpLeaseExpires=_parse_datetime(raw.get("leaseExpires")),
            defaultGateway=str(raw.get("defaultGateway") or "") or None,
            route=ManagementRoute(
                destinationPrefix=route_prefix,
                nextHop=next_hop,
                kind=_route_kind(route_prefix, next_hop),
                routeMetric=_optional_int(raw.get("routeMetric")),
                protocol=str(raw.get("routeProtocol") or "") or None,
            ),
            targetNeighborState=str(raw.get("targetNeighborState") or "") or None,
            gatewayNeighborState=str(raw.get("gatewayNeighborState") or "") or None,
            windowsConnectivity=str(raw.get("windowsConnectivity") or "") or None,
            tcp22=_tcp_probe(target_ip, 22, source_ip),
            icmpReachable=_icmp_probe(target_ip, source_ip),
        )
        return _ObservedPath(observation, adapter_mac=str(raw.get("adapterMac") or ""))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS management_path_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_key TEXT NOT NULL,
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    known_good INTEGER NOT NULL,
    signature TEXT NOT NULL,
    observation_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_management_path_device_time
ON management_path_observations(device_key, last_observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_management_path_known_good
ON management_path_observations(device_key, known_good, last_observed_at DESC);
"""


class ManagementPathStore:
    def __init__(self, db_path: Path = DATA_DIR / "management-path.sqlite") -> None:
        self.db_path = db_path
        self._lock = Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        harden_private_file(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def device_key(target: str) -> str:
        return hashlib.sha256(target.casefold().encode("utf-8")).hexdigest()[:24]

    def record(self, target: str, observation: ManagementPathObservation, *, known_good: bool) -> None:
        payload = observation.model_dump(by_alias=True, mode="json")
        signature_payload = dict(payload)
        signature_payload.pop("observedAt", None)
        signature = hashlib.sha256(
            json.dumps(signature_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        device_key = self.device_key(target)
        stamp = observation.observed_at.isoformat()
        with self._lock, self._connect() as conn:
            latest = conn.execute(
                """SELECT id, signature, known_good FROM management_path_observations
                   WHERE device_key = ? ORDER BY id DESC LIMIT 1""",
                (device_key,),
            ).fetchone()
            if latest and latest["signature"] == signature and bool(latest["known_good"]) == known_good:
                conn.execute(
                    """UPDATE management_path_observations
                       SET last_observed_at = ?, observation_json = ? WHERE id = ?""",
                    (stamp, json.dumps(payload, sort_keys=True), latest["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO management_path_observations
                       (device_key, first_observed_at, last_observed_at, known_good,
                        signature, observation_json) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        device_key,
                        stamp,
                        stamp,
                        1 if known_good else 0,
                        signature,
                        json.dumps(payload, sort_keys=True),
                    ),
                )
            conn.commit()

    def last_known_good(self, target: str) -> ManagementPathObservation | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """SELECT observation_json FROM management_path_observations
                   WHERE device_key = ? AND known_good = 1
                   ORDER BY last_observed_at DESC, id DESC LIMIT 1""",
                (self.device_key(target),),
            ).fetchone()
        return ManagementPathObservation.model_validate_json(row[0]) if row else None


def _same_network(address: str | None, target: str, prefix: str | None) -> bool:
    if not address or not prefix:
        return False
    try:
        network = ipaddress.ip_network(prefix, strict=False)
        return ipaddress.ip_address(address) in network and ipaddress.ip_address(target) in network
    except ValueError:
        return False


def diagnose_management_path(
    *,
    target: str,
    current: ManagementPathObservation,
    last_known_good: LastKnownManagementPath | None,
    session_status: dict[str, Any],
) -> ManagementPathDiagnosis:
    """Derive a bounded conclusion from normalized evidence only."""
    error_code = str(session_status.get("errorCode") or "")
    typed = {
        "switch_auth_failed": (
            "AUTHENTICATION_FAILED",
            "Catalyst authentication failed",
            "The network and SSH path reached the Catalyst, but stored credentials were rejected.",
        ),
        "host_key_changed": (
            "HOST_KEY_CHANGED",
            "Catalyst host key changed",
            "SwitchOps refused the changed host key. No automatic recovery is permitted.",
        ),
        "ssh_negotiation_failed": (
            "SSH_NEGOTIATION_FAILED",
            "Catalyst SSH negotiation failed",
            "The peers reached each other but did not complete a compatible SSH handshake.",
        ),
    }
    if error_code in typed:
        conclusion, headline, summary = typed[error_code]
        return ManagementPathDiagnosis(
            conclusion=conclusion,  # type: ignore[arg-type]
            confidence="HIGH",
            headline=headline,
            summary=summary,
            evidence=[f"The device session reported {error_code}."],
        )

    if not current.supported:
        return ManagementPathDiagnosis(
            conclusion="INDETERMINATE",
            confidence="INDETERMINATE",
            headline="Management path evidence unavailable",
            summary="This host platform does not provide the bounded Windows path observer.",
            missingEvidence=["A supported Windows network observation is required."],
        )
    if current.collection_error:
        return ManagementPathDiagnosis(
            conclusion="INDETERMINATE",
            confidence="INDETERMINATE",
            headline="Management path evidence unavailable",
            summary="Windows path collection failed, so SwitchOps cannot classify the current route.",
            evidence=[],
            missingEvidence=["A complete Windows route and source-address observation is required."],
        )

    evidence: list[str] = []
    missing = ["Current Catalyst control-plane health cannot be verified without a working management path."]
    if current.source_ip and current.adapter_name:
        evidence.append(
            f"Windows selected {current.adapter_name} with source {current.source_ip}."
        )
    if current.route.kind == "default" and current.route.next_hop:
        evidence.append(
            f"The configured target currently follows the default gateway {current.route.next_hop}."
        )
    if current.windows_connectivity:
        evidence.append(
            f"Windows reports {current.windows_connectivity.lower()} connectivity on the selected adapter."
        )
    evidence.append(f"The bounded TCP/22 probe was {current.tcp22.replace('_', ' ')}.")

    if current.tcp22 == "reachable" and session_status.get("state") == "live":
        return ManagementPathDiagnosis(
            conclusion="MANAGEMENT_PATH_HEALTHY",
            confidence="HIGH",
            headline="Management path healthy",
            summary="Windows can reach TCP/22 and the serialized Catalyst session is live.",
            evidence=evidence,
            missingEvidence=[],
        )
    if current.tcp22 == "refused":
        return ManagementPathDiagnosis(
            conclusion="SSH_SERVICE_UNAVAILABLE",
            confidence="HIGH",
            headline="Catalyst SSH endpoint refused",
            summary=(
                "An IP endpoint actively refused TCP/22. This proves a routed response, "
                "but not whether IOS SSH is disabled or an intermediate policy rejected it."
            ),
            evidence=evidence,
            missingEvidence=["Current IOS SSH service state is unavailable."],
        )
    if current.route.kind == "none" or not current.source_ip:
        return ManagementPathDiagnosis(
            conclusion="HOST_ROUTE_MISSING",
            confidence="HIGH",
            headline="No Windows route to the Catalyst",
            summary="Windows could not select a source address and route for the configured target.",
            evidence=evidence,
            missingEvidence=missing,
        )

    historical_prefix = (
        (last_known_good.connected_prefix or last_known_good.management_prefix)
        if last_known_good
        else None
    )
    historical_direct = bool(
        last_known_good
        and _same_network(
            last_known_good.source_ip,
            target,
            historical_prefix,
        )
    )
    current_left_historical_prefix = bool(
        last_known_good
        and historical_prefix
        and not _same_network(current.source_ip, target, historical_prefix)
    )
    if (
        historical_direct
        and current_left_historical_prefix
        and current.route.kind == "default"
        and current.tcp22 != "reachable"
    ):
        strong = bool(
            last_known_good
            and last_known_good.same_adapter_as_current
            and last_known_good.last_device_success_at
            and current.dhcp_lease_obtained
            and abs(
                (current.dhcp_lease_obtained - last_known_good.last_device_success_at).total_seconds()
            ) <= 900
        )
        if last_known_good:
            evidence.extend(
                [
                    f"The last known source {last_known_good.source_ip} shared the Catalyst management prefix.",
                    f"The current source no longer belongs to {historical_prefix}.",
                ]
            )
            if last_known_good.same_adapter_as_current:
                evidence.append("The historical and current observations identify the same host adapter.")
            if last_known_good.last_device_success_at and current.dhcp_lease_obtained:
                seconds = int(
                    abs(
                        (
                            current.dhcp_lease_obtained
                            - last_known_good.last_device_success_at
                        ).total_seconds()
                    )
                )
                evidence.append(
                    f"The current DHCP lease began {seconds} seconds after the last successful device operation."
                )
        return ManagementPathDiagnosis(
            conclusion="HOST_NETWORK_CHANGED",
            confidence="HIGH" if strong else "MEDIUM",
            headline="Management path degraded",
            summary=(
                "The host left the last-known Catalyst management prefix and now sends the "
                "target through its default gateway. Internet connectivity can remain healthy "
                "while Catalyst management is unreachable."
            ),
            evidence=evidence,
            missingEvidence=missing,
        )

    if current.target_on_connected_prefix is False and current.route.kind == "default":
        return ManagementPathDiagnosis(
            conclusion="HOST_PATH_DEGRADED",
            confidence="MEDIUM",
            headline="Management target is no longer on-link",
            summary=(
                "Windows sends the configured Catalyst target through the default gateway, "
                "and TCP/22 is unavailable. No complete last-known-good path was retained."
            ),
            evidence=evidence,
            missingEvidence=missing + ["A complete last-known-good host path is unavailable."],
        )

    return ManagementPathDiagnosis(
        conclusion="DEVICE_OR_PATH_UNREACHABLE",
        confidence="INDETERMINATE",
        headline="Catalyst or management path unavailable",
        summary=(
            "The host has a route, but the available evidence cannot distinguish device, "
            "SSH-service, filtering, VLAN, or physical-path failure."
        ),
        evidence=evidence,
        missingEvidence=missing,
    )


def apply_meraki_context(
    *,
    target: str,
    current: ManagementPathObservation,
    last_known_good: LastKnownManagementPath | None,
    diagnosis: ManagementPathDiagnosis,
    meraki: MerakiManagementEvidence,
) -> ManagementPathDiagnosis:
    """Bound how current Dashboard configuration can refine a host diagnosis."""
    if meraki.state not in {"healthy", "partial"} or meraki.freshness != "current":
        reason = (
            "Current Meraki LAN configuration is unavailable."
            if meraki.state in {"not-configured", "unavailable"}
            else "Meraki LAN configuration is not current enough to refine this diagnosis."
        )
        return diagnosis.model_copy(
            update={"missing_evidence": list(dict.fromkeys([*diagnosis.missing_evidence, reason]))}
        )

    try:
        target_address = ipaddress.ip_address(target)
        source_address = ipaddress.ip_address(current.source_ip) if current.source_ip else None
    except ValueError:
        return diagnosis.model_copy(
            update={
                "missing_evidence": list(
                    dict.fromkeys(
                        [
                            *diagnosis.missing_evidence,
                            "The configured target could not be compared with Meraki IPv4 LANs.",
                        ]
                    )
                )
            }
        )
    if not isinstance(target_address, ipaddress.IPv4Address):
        return diagnosis

    source_lan = None
    target_lan = None
    for lan in meraki.lans:
        try:
            network = ipaddress.ip_network(lan.subnet, strict=False)
        except ValueError:
            continue
        if source_address is not None and source_address in network:
            source_lan = lan
        if target_address in network:
            target_lan = lan

    if (
        diagnosis.conclusion == "HOST_NETWORK_CHANGED"
        and current.route.kind == "default"
        and source_lan is not None
        and target_lan is not None
    ):
        return ManagementPathDiagnosis(
            conclusion="DEVICE_OR_PATH_UNREACHABLE",
            confidence="INDETERMINATE",
            headline="Catalyst or routed management path unavailable",
            summary=(
                "The selected Meraki network currently reports both the host LAN and the "
                "Catalyst management LAN. The default-gateway route may therefore be intentional; "
                "the available evidence does not justify host address recovery."
            ),
            evidence=list(
                dict.fromkeys(
                    [
                        *diagnosis.evidence,
                        f"Meraki currently reports the host LAN {source_lan.subnet}.",
                        f"Meraki currently reports the Catalyst management LAN {target_lan.subnet}.",
                    ]
                )
            ),
            missingEvidence=list(
                dict.fromkeys(
                    [
                        *diagnosis.missing_evidence,
                        "Current routing, policy, VLAN forwarding, and Catalyst state remain unverified.",
                    ]
                )
            ),
        )

    if diagnosis.conclusion == "HOST_NETWORK_CHANGED" and source_lan is not None:
        evidence = [
            *diagnosis.evidence,
            f"Meraki currently reports the host LAN {source_lan.subnet} with DHCP mode {source_lan.dhcp_mode}.",
        ]
        missing = list(diagnosis.missing_evidence)
        if target_lan is None and meraki.complete:
            historical_prefix = (
                (last_known_good.connected_prefix or last_known_good.management_prefix)
                if last_known_good
                else None
            )
            evidence.append(
                "The selected Meraki network does not currently report the historical Catalyst management prefix."
            )
            missing.append(
                "Absence from the selected Meraki network does not prove the prefix is absent elsewhere or isolated."
            )
            if historical_prefix:
                evidence[-1] = (
                    f"The selected Meraki network does not currently report the historical "
                    f"Catalyst management prefix {historical_prefix}."
                )
        elif target_lan is None:
            missing.append(
                "The partial Meraki response cannot establish whether the historical management prefix is configured."
            )
        return diagnosis.model_copy(
            update={
                "evidence": list(dict.fromkeys(evidence)),
                "missing_evidence": list(dict.fromkeys(missing)),
            }
        )
    return diagnosis


def _evidence_freshness(
    observed_at: datetime, reference: datetime
) -> Literal["current", "aging", "stale", "historical"]:
    age = max(0.0, (reference - observed_at).total_seconds())
    if age <= 15 * 60:
        return "current"
    if age <= 6 * 60 * 60:
        return "aging"
    if age <= 7 * 24 * 60 * 60:
        return "stale"
    return "historical"


class ManagementPathService:
    def __init__(
        self,
        *,
        observer: WindowsManagementPathObserver | Any | None = None,
        store: ManagementPathStore | None = None,
        telemetry_store: TelemetryStore | Any | None = None,
        configuration_store: ConfigurationHistoryStore | None = None,
        discovery_store: DiscoveryHistoryStore | None = None,
        meraki_provider: Callable[[], MerakiManagementEvidence] | None = None,
    ) -> None:
        self.observer = observer or WindowsManagementPathObserver()
        self.store = store or ManagementPathStore()
        self.telemetry_store = telemetry_store or get_telemetry_store()
        self.configuration_store = configuration_store or get_configuration_history_store()
        self.discovery_store = discovery_store or get_discovery_store()
        self.meraki_provider = meraki_provider or self._default_meraki_evidence

    @staticmethod
    def _default_meraki_evidence() -> MerakiManagementEvidence:
        from .unified_service import get_unified_lab_service

        return get_unified_lab_service().management_path_evidence()

    @staticmethod
    def _from_observation(
        observation: ManagementPathObservation,
        *,
        last_success: datetime | None,
    ) -> LastKnownManagementPath:
        return LastKnownManagementPath(
            observedAt=observation.observed_at,
            lastDeviceSuccessAt=last_success,
            adapterId=observation.adapter_id,
            adapterName=observation.adapter_name,
            sourceIp=observation.source_ip,
            prefixLength=observation.prefix_length,
            connectedPrefix=observation.connected_prefix,
            defaultGateway=observation.default_gateway,
            dhcpServer=observation.dhcp_server,
            sameAdapterAsCurrent=None,
            provenance=["management-path-history"],
        )

    def _legacy_last_known_good(
        self,
        target: str,
        current: _ObservedPath,
    ) -> LastKnownManagementPath | None:
        context = self.configuration_store.management_context_for_target(target)
        if context is None:
            return None
        local_host = self.discovery_store.latest_local_host(str(context["device_id"]))
        if local_host is None or not local_host.get("ip"):
            return None
        prefix: str | None = None
        try:
            network = ipaddress.ip_network(
                f"{target}/{context['management_mask']}", strict=False
            )
            prefix = str(network)
        except (KeyError, ValueError):
            pass
        telemetry_success = self.telemetry_store.latest_successful_observation_at(
            str(context["device_id"])
        )
        last_success = max(
            (stamp for stamp in (local_host["last_seen"], telemetry_success) if stamp),
            default=None,
        )
        historic_mac = _normalize_mac(str(local_host.get("mac") or ""))
        same_adapter = bool(
            historic_mac and current.adapter_mac and historic_mac == current.adapter_mac
        )
        return LastKnownManagementPath(
            observedAt=local_host["last_seen"],
            lastDeviceSuccessAt=last_success,
            adapterId=current.public.adapter_id if same_adapter else None,
            adapterName=current.public.adapter_name if same_adapter else None,
            sourceIp=str(local_host["ip"]),
            # Discovery retained the host address, but not its old Windows
            # prefix or gateway. Keep Catalyst configuration facts distinct.
            managementPrefix=prefix,
            catalystGateway=str(context.get("gateway") or "") or None,
            catalystInterface=str(local_host.get("interface") or "") or None,
            sameAdapterAsCurrent=same_adapter,
            provenance=[
                "configuration-history",
                "discovery-history",
                "device-telemetry",
            ],
        )

    def assess(self, target: str, session_status: dict[str, Any]) -> ManagementPathResponse:
        observed = self.observer.observe(target)
        try:
            durable = self.store.last_known_good(target)
        except Exception as exc:
            logger.warning("Management-path history could not be read (%s)", type(exc).__name__)
            durable = None
        try:
            last_known = (
                self._from_observation(durable, last_success=durable.observed_at)
                if durable is not None
                else self._legacy_last_known_good(target, observed)
            )
        except Exception as exc:
            logger.warning("Legacy management-path evidence could not be read (%s)", type(exc).__name__)
            last_known = None
        if last_known and last_known.adapter_id and observed.public.adapter_id:
            last_known.same_adapter_as_current = (
                last_known.adapter_id == observed.public.adapter_id
            )
        if last_known:
            last_known.freshness = _evidence_freshness(
                last_known.observed_at, observed.public.observed_at
            )
        known_good = session_status.get("state") == "live" and observed.public.tcp22 == "reachable"
        try:
            self.store.record(target, observed.public, known_good=known_good)
        except Exception as exc:
            logger.warning("Management-path observation could not be persisted (%s)", type(exc).__name__)
        diagnosis = diagnose_management_path(
            target=target,
            current=observed.public,
            last_known_good=last_known,
            session_status=session_status,
        )
        try:
            meraki = self.meraki_provider()
        except Exception as exc:
            logger.warning("Meraki management evidence could not be read (%s)", type(exc).__name__)
            meraki = MerakiManagementEvidence.unavailable(
                checked_at=observed.public.observed_at,
                state="unavailable",
                detail="Current Meraki management evidence could not be read.",
            )
        diagnosis = apply_meraki_context(
            target=target,
            current=observed.public,
            last_known_good=last_known,
            diagnosis=diagnosis,
            meraki=meraki,
        )
        recovery_plan = build_recovery_plan(
            target=target,
            current=observed.public,
            last_known_good=last_known,
            diagnosis=diagnosis,
            meraki=meraki,
            now=observed.public.observed_at,
        )
        return ManagementPathResponse(
            current=observed.public,
            lastKnownGood=last_known,
            diagnosis=diagnosis,
            merakiEvidence=meraki,
            recoveryPlan=recovery_plan,
            remediationAvailable=False,
        )


_service: ManagementPathService | None = None
_service_lock = Lock()


def get_management_path_service() -> ManagementPathService:
    global _service
    with _service_lock:
        if _service is None:
            _service = ManagementPathService()
        return _service


def reset_management_path_service() -> None:
    global _service
    with _service_lock:
        _service = None
