"""Create and destroy a disposable DHCP-controlled Windows interface.

The DHCP-coexistence question can only be answered on an interface whose primary
IPv4 address really is controlled by the Windows DHCP client. Building one from
VirtualBox host-only networking gives an adapter that is on the Windows host IP
stack -- which is where recovery would run -- while carrying no default route,
no Internet path, and no relationship to the production LAN.

Everything created here is recorded, and teardown removes only what the registry
says we created. The pre-existing host-only adapter is never touched.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Sequence

from .environment import (
    DisposableEnvironment,
    EnvironmentRegistry,
    new_environment_id,
    normalise_guid,
    now_iso,
)

VBOXMANAGE = Path(r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe")

#: Inside VirtualBox's default permitted host-only range (192.168.56.0/21) and
#: distinct from the pre-existing 192.168.56.0/24 segment.
DEFAULT_NETWORK = "192.168.57.0/24"
DEFAULT_SERVER_IP = "192.168.57.100"
DEFAULT_LOWER = "192.168.57.101"
DEFAULT_UPPER = "192.168.57.200"
DEFAULT_NETMASK = "255.255.255.0"

#: Networks this tool refuses to build on, whatever spelling it is given.
FORBIDDEN_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("192.168.254.0/24", "192.168.0.0/24", "192.168.56.0/24")
)

ProvisionOutcome = Literal[
    "PROVISIONED",
    "VBOXMANAGE_UNAVAILABLE",
    "ELEVATION_UNAVAILABLE",
    "NETWORK_FORBIDDEN",
    "ADAPTER_CREATE_FAILED",
    "IDENTITY_NOT_RESOLVED",
    "DHCP_SERVER_FAILED",
    "DHCP_MODE_FAILED",
    "DHCP_LEASE_NOT_OBTAINED",
]

TeardownOutcome = Literal[
    "REMOVED",
    "NOT_OURS",
    "VBOXMANAGE_UNAVAILABLE",
    "ELEVATION_UNAVAILABLE",
    "IDENTITY_NOT_RESOLVED",
    "IDENTITY_AMBIGUOUS",
    "ADAPTER_CHANGED",
    "REMOVE_FAILED",
]


@dataclass
class ProvisionResult:
    outcome: ProvisionOutcome
    environment: DisposableEnvironment | None = None
    steps: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class TeardownResult:
    outcome: TeardownOutcome
    steps: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass(frozen=True)
class LeaseObservation:
    """One Windows address row that may prove DHCP provisioning succeeded."""

    interface_index: int
    address: str
    alias: str
    prefix_origin: str
    suffix_origin: str
    dad_state: str
    valid_lifetime: int

    @property
    def is_usable_dhcp_lease(self) -> bool:
        try:
            address = ipaddress.ip_address(self.address)
        except ValueError:
            return False
        return (
            isinstance(address, ipaddress.IPv4Address)
            and not address.is_link_local
            and self.prefix_origin == "DHCP"
            and self.suffix_origin == "DHCP"
            and self.dad_state == "PREFERRED"
            and self.valid_lifetime != 0xFFFFFFFF
        )


def _run(args: Sequence[str], timeout: int = 120) -> tuple[int, str]:
    completed = subprocess.run(
        list(args), capture_output=True, text=True, timeout=timeout
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def network_is_permitted(cidr: str) -> bool:
    """Refuse to build a test network that could shadow real addressing."""
    try:
        candidate = ipaddress.ip_network(cidr, strict=True)
    except ValueError:
        return False
    return (
        isinstance(candidate, ipaddress.IPv4Network)
        and not any(candidate.overlaps(forbidden) for forbidden in FORBIDDEN_NETWORKS)
    )


def dhcp_configuration_is_permitted(
    *,
    network_cidr: str,
    server_ip: str,
    lower_ip: str,
    upper_ip: str,
    netmask: str,
) -> bool:
    """Require every DHCP value to describe the same permitted network."""
    try:
        network = ipaddress.ip_network(network_cidr, strict=True)
        server = ipaddress.ip_address(server_ip)
        lower = ipaddress.ip_address(lower_ip)
        upper = ipaddress.ip_address(upper_ip)
        mask_prefix = ipaddress.ip_network(f"0.0.0.0/{netmask}").prefixlen
    except ValueError:
        return False
    if not isinstance(network, ipaddress.IPv4Network):
        return False
    if not network_is_permitted(network_cidr):
        return False

    def usable(address: ipaddress._BaseAddress) -> bool:
        return (
            isinstance(address, ipaddress.IPv4Address)
            and address in network
            and address not in (network.network_address, network.broadcast_address)
        )

    return (
        mask_prefix == network.prefixlen
        and usable(server)
        and usable(lower)
        and usable(upper)
        and lower <= upper
        and not lower <= server <= upper
    )


def parse_created_interface(output: str) -> str | None:
    """Extract the adapter name VirtualBox reports after `hostonlyif create`."""
    match = re.search(r"Interface '([^']+)' was successfully created", output)
    return match.group(1) if match else None


def provision_dhcp_environment(
    *,
    registry: EnvironmentRegistry,
    network_cidr: str = DEFAULT_NETWORK,
    server_ip: str = DEFAULT_SERVER_IP,
    lower_ip: str = DEFAULT_LOWER,
    upper_ip: str = DEFAULT_UPPER,
    netmask: str = DEFAULT_NETMASK,
    elevated: bool = False,
    vboxmanage: Path = VBOXMANAGE,
    run: Callable[[Sequence[str]], tuple[int, str]] | None = None,
    lease_probe: Callable[[str], LeaseObservation | None] | None = None,
    lease_timeout: float = 45.0,
    sleep: Callable[[float], None] | None = None,
    hostonly_guids: dict[str, str] | None = None,
    read_hostonly_guids: Callable[[], dict[str, str]] | None = None,
) -> ProvisionResult:
    """Create a host-only adapter whose Windows side obtains DHCP.

    Reports ``DHCP_LEASE_NOT_OBTAINED`` rather than proceeding when no lease
    arrives: an adapter that fell back to APIPA would not answer the question
    this environment exists to answer, and pretending otherwise would produce a
    confident but meaningless coexistence result.
    """
    run = run or _run
    sleep = sleep or time.sleep
    steps: list[str] = []

    if not dhcp_configuration_is_permitted(
        network_cidr=network_cidr,
        server_ip=server_ip,
        lower_ip=lower_ip,
        upper_ip=upper_ip,
        netmask=netmask,
    ):
        return ProvisionResult(
            outcome="NETWORK_FORBIDDEN",
            detail=(
                f"{network_cidr} overlaps production, the Catalyst management "
                "prefix, or the pre-existing host-only network."
            ),
        )
    if not elevated:
        return ProvisionResult(
            outcome="ELEVATION_UNAVAILABLE",
            detail="Creating a host-only adapter requires Administrators.",
        )
    if not vboxmanage.exists():
        return ProvisionResult(
            outcome="VBOXMANAGE_UNAVAILABLE",
            detail=f"VBoxManage was not found at {vboxmanage.name}.",
        )

    code, output = run([str(vboxmanage), "hostonlyif", "create"])
    hostonly_name = parse_created_interface(output)
    if code != 0 or not hostonly_name:
        return ProvisionResult(
            outcome="ADAPTER_CREATE_FAILED",
            steps=steps,
            detail=output.strip()[:400],
        )
    steps.append(f"created host-only interface {hostonly_name!r}")

    if hostonly_guids is None and read_hostonly_guids is not None:
        hostonly_guids = read_hostonly_guids()

    # Record before configuring: if the next step fails, teardown must still
    # know this adapter is ours to remove. The GUID is captured here because it
    # is the only value that links "the interface we created" to "the adapter
    # Windows shows"; the name is the Windows description, never its alias.
    environment = DisposableEnvironment(
        environment_id=new_environment_id(),
        hostonly_name=hostonly_name,
        network_cidr=network_cidr,
        created_at=now_iso(),
        interface_guid=normalise_guid(
            (hostonly_guids or {}).get(hostonly_name) if hostonly_guids else None
        ),
    )
    registry.record(environment)
    steps.append(f"recorded {environment.environment_id}")

    if not environment.has_stable_identity:
        return ProvisionResult(
            outcome="IDENTITY_NOT_RESOLVED",
            environment=environment,
            steps=steps,
            detail=(
                "VirtualBox did not report the GUID of the interface it just "
                "created. The unresolved record is retained for safe "
                "reconciliation and teardown; no DHCP configuration was attempted."
            ),
        )

    code, output = run(
        [
            str(vboxmanage), "dhcpserver", "add",
            f"--interface={hostonly_name}",
            f"--server-ip={server_ip}",
            f"--netmask={netmask}",
            f"--lower-ip={lower_ip}",
            f"--upper-ip={upper_ip}",
            "--enable",
        ]
    )
    if code != 0:
        return ProvisionResult(
            outcome="DHCP_SERVER_FAILED",
            environment=environment,
            steps=steps,
            detail=output.strip()[:400],
        )
    steps.append(f"enabled DHCP server {lower_ip}-{upper_ip}")

    code, output = run(
        [str(vboxmanage), "hostonlyif", "ipconfig", hostonly_name, "--dhcp"]
    )
    if code != 0:
        return ProvisionResult(
            outcome="DHCP_MODE_FAILED",
            environment=environment,
            steps=steps,
            detail=output.strip()[:400],
        )
    steps.append("set the host side of the adapter to DHCP")

    if lease_probe is None:
        return ProvisionResult(
            outcome="DHCP_LEASE_NOT_OBTAINED",
            environment=environment,
            steps=steps,
            detail="No DHCP lease probe was available, so success cannot be proven.",
        )

    waited = 0.0
    while waited <= lease_timeout:
        observed = lease_probe(environment.interface_guid)
        if observed is not None and observed.is_usable_dhcp_lease:
            environment.interface_index = observed.interface_index
            environment.observed_alias = observed.alias
            environment.observed_at = now_iso()
            registry.update(environment)
            steps.append(
                f"obtained DHCP lease {observed.address} on "
                f"{observed.alias!r} (ifIndex {observed.interface_index})"
            )
            return ProvisionResult(
                outcome="PROVISIONED", environment=environment, steps=steps
            )
        sleep(2.0)
        waited += 2.0

    return ProvisionResult(
        outcome="DHCP_LEASE_NOT_OBTAINED",
        environment=environment,
        steps=steps,
        detail=(
            "The adapter did not obtain a DHCP address, so it cannot answer the "
            "coexistence question and is not treated as provisioned. "
            "Observed on this machine: configuring and enabling the DHCP server "
            "is not sufficient on its own -- VBoxNetDHCP did not run, and nothing "
            "was bound to UDP/67, until a VM was attached to the host-only "
            "network. Attaching one started the daemon and the lease arrived. "
            "This may not hold for every VirtualBox version, so treat it as a "
            "likely cause rather than a rule: attach an active consumer to the "
            "network and retry, or tear the environment down."
        ),
    )


def teardown_dhcp_environment(
    *,
    registry: EnvironmentRegistry,
    adapter_alias: str,
    elevated: bool = False,
    vboxmanage: Path = VBOXMANAGE,
    run: Callable[[Sequence[str]], tuple[int, str]] | None = None,
    hostonly_guids: dict[str, str] | None = None,
    read_hostonly_guids: Callable[[], dict[str, str]] | None = None,
) -> TeardownResult:
    """Remove only a live VirtualBox adapter whose recorded GUID still matches."""
    run = run or _run
    steps: list[str] = []

    # Accept either the VirtualBox interface name we recorded or the Windows
    # alias it later became; both resolve to the same owned record.
    candidates = [
        item
        for item in registry.all()
        if item.hostonly_name == adapter_alias or item.observed_alias == adapter_alias
    ]
    if not candidates:
        # The pre-existing host-only adapter lands here, which is the point.
        return TeardownResult(
            outcome="NOT_OURS",
            detail=(
                f"{adapter_alias!r} has no provisioning record. Only adapters this "
                "harness created are removed."
            ),
        )
    if len(candidates) > 1:
        return TeardownResult(
            outcome="IDENTITY_AMBIGUOUS",
            detail=(
                f"{adapter_alias!r} matches {len(candidates)} provisioning records. "
                "Resolve the registry manually; nothing was removed."
            ),
        )
    environment = candidates[0]

    recorded_guid = normalise_guid(environment.interface_guid)
    if recorded_guid is None:
        return TeardownResult(
            outcome="IDENTITY_NOT_RESOLVED",
            detail=(
                "The provisioning record has no resolved interface GUID. Run "
                "reconcile before teardown; nothing was removed."
            ),
        )

    if hostonly_guids is None and read_hostonly_guids is not None:
        hostonly_guids = read_hostonly_guids()
    live_guid = normalise_guid(
        (hostonly_guids or {}).get(environment.hostonly_name)
    )
    if live_guid != recorded_guid:
        return TeardownResult(
            outcome="ADAPTER_CHANGED",
            detail=(
                f"VirtualBox no longer maps {environment.hostonly_name!r} to the "
                "GUID recorded when it was created. Nothing was removed."
            ),
        )
    if not elevated:
        return TeardownResult(
            outcome="ELEVATION_UNAVAILABLE",
            detail="Removing a host-only adapter requires Administrators.",
        )
    if not vboxmanage.exists():
        return TeardownResult(
            outcome="VBOXMANAGE_UNAVAILABLE",
            detail=f"VBoxManage was not found at {vboxmanage.name}.",
        )

    # A missing DHCP server is not a failure: it may never have been created.
    code, output = run(
        [str(vboxmanage), "dhcpserver", "remove",
         f"--interface={environment.hostonly_name}"]
    )
    steps.append(
        "removed DHCP server" if code == 0 else f"no DHCP server to remove ({code})"
    )

    code, output = run(
        [str(vboxmanage), "hostonlyif", "remove", environment.hostonly_name]
    )
    if code != 0:
        return TeardownResult(
            outcome="REMOVE_FAILED", steps=steps, detail=output.strip()[:400]
        )
    steps.append(f"removed host-only interface {environment.hostonly_name!r}")

    registry.remove(environment.environment_id)
    steps.append(f"cleared {environment.environment_id}")
    return TeardownResult(outcome="REMOVED", steps=steps)
