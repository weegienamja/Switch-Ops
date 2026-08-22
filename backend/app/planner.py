"""Pure dry-run planning. This module has no execution capability."""
from __future__ import annotations

import hashlib
import json
from typing import Iterable

from .command_registry import (
    assert_interface_readable,
    assert_interface_writable,
    resolve_read_command,
)
from .errors import SwitchOpsError
from .models import (
    AccessPointPlanRequest,
    DeploymentPlan,
    InterfaceStatus,
    PlanCheck,
    PoePort,
)


def _short_interface(canonical: str) -> str:
    return canonical.replace("GigabitEthernet", "Gi")


def build_access_point_plan(
    request: AccessPointPlanRequest,
    *,
    interfaces: Iterable[InterfaceStatus],
    poe_ports: Iterable[PoePort],
    vlan_ids: set[int],
) -> DeploymentPlan:
    """Validate and render a non-executable access-point port plan."""
    canonical = assert_interface_readable(request.interface)
    short = _short_interface(canonical)
    interface_by_port = {item.port.lower(): item for item in interfaces}
    poe_by_port = {item.interface.lower(): item for item in poe_ports}
    checks: list[PlanCheck] = []

    interface_exists = short.lower() in interface_by_port
    checks.append(PlanCheck(
        name="interface_exists",
        passed=interface_exists,
        detail=f"{short} was returned by show interfaces status." if interface_exists else f"{short} was not returned by the switch.",
    ))

    try:
        assert_interface_writable(canonical)
        safe_target = True
        safe_detail = f"{canonical} is an allowlisted lab interface and is not protected."
    except SwitchOpsError as exc:
        safe_target = False
        safe_detail = exc.message
    checks.append(PlanCheck(name="target_is_safe", passed=safe_target, detail=safe_detail))

    poe_supported = request.poe == "never" or short.lower() in poe_by_port
    checks.append(PlanCheck(
        name="poe_supported",
        passed=poe_supported,
        detail=(
            f"{short} is present in show power inline."
            if short.lower() in poe_by_port
            else "PoE is not required by this desired state."
            if request.poe == "never"
            else f"{short} was not reported as PoE-capable."
        ),
    ))

    vlan_exists = request.vlan in vlan_ids
    checks.append(PlanCheck(
        name="vlan_exists",
        passed=vlan_exists,
        detail=f"VLAN {request.vlan} exists in show vlan brief." if vlan_exists else f"VLAN {request.vlan} was not returned by show vlan brief.",
    ))

    valid = all(check.passed for check in checks)
    desired_state = {
        "role": request.role,
        "enabled": request.enabled,
        "mode": "access",
        "vlan": request.vlan,
        "poe": request.poe,
        "portfast": request.portfast,
    }
    fingerprint = hashlib.sha256(
        json.dumps({"interface": canonical, **desired_state}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    proposed: list[str] = []
    if valid:
        proposed = [
            "configure terminal",
            f"interface {canonical}",
            "description Wireless access point",
            "switchport mode access",
            f"switchport access vlan {request.vlan}",
        ]
        if request.portfast:
            proposed.append("spanning-tree portfast")
        proposed.append("power inline auto" if request.poe == "auto" else "power inline never")
        proposed.extend(["no shutdown" if request.enabled else "shutdown", "end"])

    return DeploymentPlan(
        planId=f"ap-{fingerprint}",
        status="VALID" if valid else "INVALID",
        targetInterface=canonical,
        desiredState=desired_state,
        checks=checks,
        impact=(
            "Selected access port only. Protected management interfaces are unaffected."
            if safe_target
            else "Plan is blocked because the target is protected or outside the lab allowlist."
        ),
        proposedIos=proposed,
        backupRequired=True,
        verificationCommands=[
            resolve_read_command("show_interfaces_status"),
            resolve_read_command("show_power_inline"),
            resolve_read_command("show_spanning_tree"),
        ],
        applyAvailable=False,
    )
