"""Turn a real SwitchOps incident into a privacy-safe Resilience Lab scenario.

    REAL INCIDENT -> NORMALIZED EVIDENCE -> PRIVACY TRANSFORM -> FIXTURE -> REPLAY

This is development tooling. It is not imported by ``backend.app``, exposes no
API route, and never contacts a device. It reads a normalized
``/api/management-path`` response that an operator has already saved to disk::

    curl -s http://127.0.0.1:8765/api/management-path > incident.json
    python -m backend.resilience_lab.exporter incident.json --id REAL_DHCP_MOVE

The transform is structural, not cosmetic. It preserves the relationships a
diagnosis actually depends on -- whether two addresses share a prefix, whether
the target was on-link, which gateway was selected, how far apart two
observations were -- while discarding the literal values that identify a real
network. Output is re-validated with the same privacy checker that guards the
committed catalogue, so an unsafe fixture cannot be produced silently.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .catalog import _validate_fixture_privacy
from .models import ResilienceScenario

# RFC 5737 documentation networks. A real prefix is mapped onto one of these,
# so "same subnet" and "different subnet" survive the transform while the real
# addressing does not.
_SYNTHETIC_NETWORKS = ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")

# RFC 3927 link-local. Already identical on every host in the world, so an
# APIPA address discloses nothing and is preserved to keep the semantics.
_LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")

# RFC 7042 documentation EUI-48 block 00-00-5E-00-53-00/40.
_SYNTHETIC_MAC_PREFIX = "00005e0053"

_IP_PATTERN = re.compile(r"(?<![0-9])(?:\d{1,3}\.){3}\d{1,3}(?![0-9])")
_MAC_PATTERN = re.compile(
    r"(?i)(?:[0-9a-f]{2}[-:]){5}[0-9a-f]{2}|[0-9a-f]{4}(?:\.[0-9a-f]{4}){2}"
)

# Keys whose values are dropped outright rather than transformed. Matching the
# committed catalogue's forbidden set keeps the two definitions aligned.
_DROP_KEYS = {"apikey", "credential", "password", "secret", "token", "username"}

# Fixed epoch for rebased timestamps. Only the intervals between observations
# carry diagnostic meaning; the absolute wall-clock time does not.
_EPOCH = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class PrivacyBudgetExceeded(ValueError):
    """More distinct real networks appeared than can be represented safely."""


class PrivacyTransform:
    """Deterministic, structure-preserving anonymization of one incident.

    Deterministic within a single incident: the same real value always maps to
    the same synthetic value, so identity relationships are retained. Not
    deterministic across incidents, which is intentional -- a stable global
    mapping would be reversible given enough exports.
    """

    def __init__(self) -> None:
        self._networks: dict[ipaddress.IPv4Network, ipaddress.IPv4Network] = {}
        self._macs: dict[str, str] = {}
        self._adapters: dict[str, str] = {}
        self._hostnames: dict[str, str] = {}
        self._earliest: datetime | None = None

    # -- addresses ---------------------------------------------------------
    def _synthetic_network(
        self, real: ipaddress.IPv4Network
    ) -> ipaddress.IPv4Network:
        known = self._networks.get(real)
        if known is not None:
            return known
        if len(self._networks) >= len(_SYNTHETIC_NETWORKS):
            raise PrivacyBudgetExceeded(
                f"This incident spans {len(self._networks) + 1} distinct IPv4 "
                f"networks but only {len(_SYNTHETIC_NETWORKS)} documentation "
                "networks exist. Export a narrower window instead of reusing "
                "one synthetic prefix for two real prefixes, which would "
                "destroy the same-subnet relationship the scenario depends on."
            )
        assigned = ipaddress.ip_network(_SYNTHETIC_NETWORKS[len(self._networks)])
        self._networks[real] = assigned
        return assigned

    def address(self, value: str, prefix_length: int = 24) -> str:
        """Map one address, preserving its host position within the subnet."""
        real = ipaddress.ip_address(value)
        if not isinstance(real, ipaddress.IPv4Address):
            return value
        if real in _LINK_LOCAL or real.is_loopback or real.is_unspecified:
            return value
        real_network = ipaddress.ip_network(
            f"{value}/{prefix_length}", strict=False
        )
        synthetic = self._synthetic_network(real_network)
        # Keep the host octet so "the .95 host" stays recognisable and two
        # observations of the same host stay comparable.
        host_octet = int(real.packed[-1])
        if host_octet in (0, 255):
            host_octet = 1
        return str(ipaddress.IPv4Address(int(synthetic.network_address) + host_octet))

    def prefix(self, value: str) -> str:
        """Map a CIDR, keeping its prefix length."""
        real = ipaddress.ip_network(value, strict=False)
        if not isinstance(real, ipaddress.IPv4Network):
            return value
        if real.subnet_of(_LINK_LOCAL) or real == _LINK_LOCAL:
            return value
        synthetic = self._synthetic_network(real)
        return f"{synthetic.network_address}/{real.prefixlen}"

    # -- other identifiers -------------------------------------------------
    def mac(self, value: str) -> str:
        normalized = re.sub(r"[^0-9a-f]", "", value.casefold())
        known = self._macs.get(normalized)
        if known is None:
            index = len(self._macs)
            if index > 0xFF:
                raise PrivacyBudgetExceeded("Too many distinct MAC addresses.")
            known = f"{_SYNTHETIC_MAC_PREFIX}{index:02x}"
            self._macs[normalized] = known
        # Emit Cisco dotted-quad form, matching the committed fixtures.
        return f"{known[0:4]}.{known[4:8]}.{known[8:12]}"

    def hostname(self, value: str) -> str:
        """Replace a device hostname with a stable synthetic label.

        Hostnames routinely encode a site, a room, or a person.
        """
        known = self._hostnames.get(value)
        if known is None:
            known = f"switch-doc-{len(self._hostnames) + 1:02d}"
            self._hostnames[value] = known
        return known

    def adapter_id(self, value: str) -> str:
        """Replace a locally derived adapter identifier with an opaque label."""
        known = self._adapters.get(value)
        if known is None:
            known = f"adapter-{len(self._adapters) + 1:04d}"
            self._adapters[value] = known
        return known

    # -- time --------------------------------------------------------------
    def note_time(self, value: datetime) -> None:
        if self._earliest is None or value < self._earliest:
            self._earliest = value

    def time(self, value: datetime) -> datetime:
        """Rebase onto a fixed epoch, preserving the interval from the start."""
        if self._earliest is None:
            return _EPOCH
        return _EPOCH + (value - self._earliest)

    # -- free text ---------------------------------------------------------
    def text(self, value: str) -> str:
        """Scrub addresses and MACs out of human-readable evidence strings."""
        def replace_ip(match: re.Match[str]) -> str:
            try:
                return self.address(match.group(0))
            except ValueError:
                return "redacted-address"

        def replace_mac(match: re.Match[str]) -> str:
            return self.mac(match.group(0))

        scrubbed = _IP_PATTERN.sub(replace_ip, value)
        return _MAC_PATTERN.sub(replace_mac, scrubbed)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _collect_times(payload: dict[str, Any], transform: PrivacyTransform) -> None:
    """Seed the time rebase from every timestamp the incident carries."""
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, str):
            parsed = _parse_time(node)
            if parsed is not None:
                transform.note_time(parsed)

    walk(payload)


def _management_evidence(
    observation: dict[str, Any],
    transform: PrivacyTransform,
    *,
    target: str,
) -> dict[str, Any]:
    """Project a normalized observation onto the fixture evidence contract."""
    prefix_length = observation.get("prefixLength") or 24
    source_ip = observation.get("sourceIp")
    connected_prefix = observation.get("connectedPrefix")
    default_gateway = observation.get("defaultGateway")
    dhcp_server = observation.get("dhcpServer")
    adapter_id = observation.get("adapterId")
    route = observation.get("route") or {}
    next_hop = route.get("nextHop")
    destination_prefix = route.get("destinationPrefix")

    evidence: dict[str, Any] = {
        "target": target,
        "supported": observation.get("supported", True),
        "adapterName": observation.get("adapterName"),
        "interfaceIndex": observation.get("interfaceIndex"),
        "interfaceMetric": observation.get("interfaceMetric"),
        "adapterState": observation.get("adapterState"),
        "prefixLength": observation.get("prefixLength"),
        "targetOnConnectedPrefix": observation.get("targetOnConnectedPrefix"),
        "dhcpEnabled": observation.get("dhcpEnabled"),
        "dhcpStaticCoexistence": observation.get("dhcpStaticCoexistence"),
        "windowsConnectivity": observation.get("windowsConnectivity"),
        "tcp22": observation.get("tcp22") or "unavailable",
        "icmpReachable": observation.get("icmpReachable"),
        "route": {
            "kind": route.get("kind") or "unknown",
            "routeMetric": route.get("routeMetric"),
            "protocol": route.get("protocol"),
            "destinationPrefix": (
                transform.prefix(destination_prefix)
                if destination_prefix and destination_prefix != "0.0.0.0/0"
                else destination_prefix
            ),
            "nextHop": (
                transform.address(next_hop, prefix_length) if next_hop else None
            ),
        },
    }
    if adapter_id:
        evidence["adapterId"] = transform.adapter_id(adapter_id)
    if source_ip:
        evidence["sourceIp"] = transform.address(source_ip, prefix_length)
    if connected_prefix:
        evidence["connectedPrefix"] = transform.prefix(connected_prefix)
    if default_gateway:
        evidence["defaultGateway"] = transform.address(default_gateway, prefix_length)
    if dhcp_server:
        evidence["dhcpServer"] = transform.address(dhcp_server, prefix_length)

    lease = _parse_time(observation.get("dhcpLeaseObtained"))
    if lease is not None:
        evidence["dhcpLeaseObtained"] = _iso(transform.time(lease))
    return evidence


def build_scenario(
    payload: dict[str, Any],
    *,
    scenario_id: str,
    description: str | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    """Build a two-phase scenario: the last known good path, then the incident.

    Phase 1 is reconstructed from the durable ``lastKnownGood`` evidence rather
    than invented, so the replay exercises the same historical-continuity
    reasoning the real diagnosis used.
    """
    current = payload.get("current")
    if not isinstance(current, dict):
        raise ValueError(
            "Expected a normalized /api/management-path response with a "
            "'current' observation."
        )
    diagnosis = payload.get("diagnosis") or {}
    last_known_good = payload.get("lastKnownGood")

    transform = PrivacyTransform()
    _collect_times(payload, transform)

    # The real target address anchors every on-link comparison, so it is mapped
    # into the historical prefix that the last known good path belonged to.
    historical_prefix = None
    if isinstance(last_known_good, dict):
        historical_prefix = (
            last_known_good.get("connectedPrefix")
            or last_known_good.get("managementPrefix")
        )
    target_source = historical_prefix or current.get("connectedPrefix")
    if target_source:
        transform.prefix(target_source)
    target = "192.0.2.10"

    phases: list[dict[str, Any]] = []

    if isinstance(last_known_good, dict) and last_known_good.get("sourceIp"):
        observed = _parse_time(last_known_good.get("observedAt")) or _EPOCH
        healthy = {
            "target": target,
            "adapterName": last_known_good.get("adapterName"),
            "sourceIp": transform.address(
                last_known_good["sourceIp"], last_known_good.get("prefixLength") or 24
            ),
            "prefixLength": last_known_good.get("prefixLength") or 24,
            "connectedPrefix": (
                transform.prefix(historical_prefix) if historical_prefix else None
            ),
            "targetOnConnectedPrefix": True,
            "route": {"kind": "connected", "destinationPrefix": None, "nextHop": None},
            "tcp22": "reachable",
            "icmpReachable": True,
            "sessionState": "live",
            "windowsConnectivity": "Internet",
        }
        gateway = last_known_good.get("catalystGateway") or last_known_good.get(
            "defaultGateway"
        )
        if gateway:
            healthy["defaultGateway"] = transform.address(gateway)
        if last_known_good.get("adapterId"):
            healthy["adapterId"] = transform.adapter_id(last_known_good["adapterId"])
        phases.append(
            {
                "id": "last-known-good",
                "at": _iso(transform.time(observed)),
                "transition": "The recorded last-known-good management path.",
                "evidence": {"management": healthy},
                "expected": {
                    "managementDiagnosis": "MANAGEMENT_PATH_HEALTHY",
                    "recoveryPlanStatus": "NOT_NEEDED",
                },
            }
        )

    observed_at = _parse_time(current.get("observedAt")) or _EPOCH
    incident_at = transform.time(observed_at)
    if phases:
        earlier = datetime.fromisoformat(phases[-1]["at"].replace("Z", "+00:00"))
        if incident_at <= earlier:
            incident_at = earlier + timedelta(minutes=1)

    incident = _management_evidence(current, transform, target=target)
    incident["sessionState"] = payload.get("sessionState") or "offline"

    expected: dict[str, Any] = {
        "managementDiagnosis": diagnosis.get("conclusion"),
        "confidence": diagnosis.get("confidence"),
        "recoveryPlanStatus": (payload.get("recoveryPlan") or {}).get("status"),
        # An exported incident must never encode a claim that recovery ran.
        "mustNotClaim": ["RECOVERY_EXECUTED", "DEVICE_OFFLINE"],
    }
    phases.append(
        {
            "id": "incident",
            "at": _iso(incident_at),
            "transition": "The observed incident state.",
            "evidence": {"management": incident},
            "expected": {k: v for k, v in expected.items() if v is not None},
        }
    )

    scenario = {
        "id": scenario_id,
        "description": description
        or "Anonymized replay of an observed SwitchOps management-path incident.",
        "purpose": purpose
        or (
            "Preserve a real diagnosis as a regression scenario without "
            "retaining any real addressing."
        ),
        "phases": phases,
    }
    return _strip_forbidden(scenario)


def build_topology_scenario(
    phases: list[dict[str, Any]],
    *,
    scenario_id: str,
    endpoint_mac: str,
    description: str | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    """Turn an ordered series of real topology observations into a fixture.

        Host-A on Gi0/2  ->  Host-A absent  ->  same Host-A on Gi0/5

    Each input is a normal read-only dashboard response. Port names are kept
    verbatim because an interface label identifies hardware, not a person or a
    network; hardware addresses, hostnames and addressing are all replaced.

    Expectations are derived from what the evidence actually shows rather than
    asserted up front, so an exported incident replays to the conclusion the
    product really reached.
    """
    if not phases:
        raise ValueError("At least one topology observation is required.")

    transform = PrivacyTransform()
    for payload in phases:
        _collect_times(payload, transform)

    target_mac = re.sub(r"[^0-9a-f]", "", endpoint_mac.casefold())
    synthetic_mac = transform.mac(endpoint_mac)

    built: list[dict[str, Any]] = []
    previous_port: str | None = None

    for index, payload in enumerate(phases):
        topology = payload.get("topology") or {}
        observed_at = _parse_time(topology.get("generatedAt")) or _EPOCH
        at = transform.time(observed_at)
        if built:
            earlier = datetime.fromisoformat(built[-1]["at"].replace("Z", "+00:00"))
            if at <= earlier:
                at = earlier + timedelta(minutes=1)

        interfaces = [
            {
                "port": item.get("port", ""),
                "status": item.get("status", "") or "notconnect",
                "vlan": str(item.get("vlan", "") or "1"),
                # Descriptions are free text written by humans and routinely
                # name a person, a room, or a site.
                "name": "",
            }
            for item in (payload.get("interfaces") or {}).get("interfaces", [])
            if item.get("port")
        ]

        macs: list[dict[str, Any]] = []
        attachment: str | None = None
        for entry in (payload.get("macTable") or {}).get("entries", []):
            raw = str(entry.get("mac", ""))
            normalized = re.sub(r"[^0-9a-f]", "", raw.casefold())
            if normalized != target_mac:
                # Only the endpoint under test is exported. Every other learned
                # address belongs to somebody else's device.
                continue
            attachment = entry.get("port")
            macs.append(
                {
                    "mac": synthetic_mac,
                    "port": attachment,
                    "vlan": str(entry.get("vlan", "") or "1"),
                }
            )

        evidence: dict[str, Any] = {
            "hostname": transform.hostname(
                str((payload.get("summary") or {}).get("hostname", "switch"))
            ),
            "managementIp": "192.0.2.10",
            "interfaces": interfaces,
            "macs": macs,
            "adapters": [{"mac": synthetic_mac}],
        }

        expected: dict[str, Any] = {"duplicateEntityIds": 0}
        if attachment is None:
            transition = "The endpoint is not observed on any port."
            expected["mustNotClaim"] = ["DEVICE_REPLACED", "ENDPOINT_MOVED"]
        elif previous_port is None:
            transition = f"The endpoint is attached to {attachment}."
            expected["mustNotClaim"] = ["ENDPOINT_MOVED", "DEVICE_REPLACED"]
        elif attachment == previous_port:
            transition = f"The endpoint is still attached to {attachment}."
            expected["currentAttachment"] = attachment
            expected["mustNotClaim"] = ["DEVICE_REPLACED"]
        else:
            transition = (
                f"The same hardware identity appears on {attachment}."
            )
            expected["topologyTransition"] = "ENDPOINT_MOVED"
            expected["identityRetained"] = True
            expected["currentAttachment"] = attachment
            expected["previousAttachment"] = previous_port
            expected["mustClaim"] = ["IDENTITY_RETAINED"]
            expected["mustNotClaim"] = ["DEVICE_REPLACED", "NEW_DEVICE"]

        if attachment is not None:
            previous_port = attachment

        built.append(
            {
                "id": f"observation-{index + 1}",
                "at": _iso(at),
                "transition": transition,
                "evidence": {"topology": evidence},
                "expected": expected,
            }
        )

    scenario = {
        "id": scenario_id,
        "description": description
        or "Anonymized replay of an observed endpoint attachment sequence.",
        "purpose": purpose
        or (
            "Preserve real attachment transitions as a regression scenario "
            "without retaining any real hardware address or hostname."
        ),
        "phases": built,
    }
    return _strip_forbidden(scenario)


def _strip_forbidden(node: Any) -> Any:
    """Drop credential-shaped keys anywhere in the produced fixture."""
    if isinstance(node, dict):
        return {
            key: _strip_forbidden(value)
            for key, value in node.items()
            if re.sub(r"[^a-z]", "", str(key).casefold()) not in _DROP_KEYS
        }
    if isinstance(node, list):
        return [_strip_forbidden(item) for item in node]
    return node


def export(
    payload: dict[str, Any],
    *,
    scenario_id: str,
    description: str | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    """Build a scenario and refuse to return one that is unsafe or invalid."""
    scenario = build_scenario(
        payload,
        scenario_id=scenario_id,
        description=description,
        purpose=purpose,
    )
    # The same guard that protects the committed catalogue. An exporter that
    # could emit a fixture the catalogue would reject is not much of a guard.
    _validate_fixture_privacy(scenario, location="exported")
    ResilienceScenario.model_validate(scenario)
    return scenario


def export_topology(
    phases: list[dict[str, Any]],
    *,
    scenario_id: str,
    endpoint_mac: str,
    description: str | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    """Build a topology scenario and refuse to return an unsafe one."""
    scenario = build_topology_scenario(
        phases,
        scenario_id=scenario_id,
        endpoint_mac=endpoint_mac,
        description=description,
        purpose=purpose,
    )
    _validate_fixture_privacy(scenario, location="exported")
    ResilienceScenario.model_validate(scenario)
    return scenario


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.resilience_lab.exporter",
        description=(
            "Convert a saved /api/management-path response into a privacy-safe "
            "Resilience Lab scenario."
        ),
    )
    parser.add_argument(
        "input",
        type=Path,
        nargs="+",
        help=(
            "Saved normalized evidence (JSON). One management-path response, "
            "or an ordered series of dashboard responses with --topology."
        ),
    )
    parser.add_argument(
        "--topology",
        action="store_true",
        help="Export attachment transitions instead of management-path evidence.",
    )
    parser.add_argument(
        "--endpoint-mac",
        help="Endpoint under test; required with --topology. Never retained.",
    )
    parser.add_argument(
        "--id",
        required=True,
        help="Scenario ID (upper snake case, e.g. REAL_DHCP_SUBNET_MOVE).",
    )
    parser.add_argument("--description")
    parser.add_argument("--purpose")
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the scenario here instead of standard output.",
    )
    args = parser.parse_args(argv)

    payloads = [json.loads(item.read_text(encoding="utf-8")) for item in args.input]
    try:
        if args.topology:
            if not args.endpoint_mac:
                raise ValueError("--endpoint-mac is required with --topology.")
            scenario = export_topology(
                payloads,
                scenario_id=args.id,
                endpoint_mac=args.endpoint_mac,
                description=args.description,
                purpose=args.purpose,
            )
        else:
            if len(payloads) != 1:
                raise ValueError(
                    "Management-path export takes exactly one saved response."
                )
            scenario = export(
                payloads[0],
                scenario_id=args.id,
                description=args.description,
                purpose=args.purpose,
            )
    except (PrivacyBudgetExceeded, ValueError) as error:
        print(f"export failed: {error}", file=sys.stderr)
        return 2

    rendered = json.dumps(scenario, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
