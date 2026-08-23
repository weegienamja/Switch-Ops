import pytest

from backend.app.errors import CommandNotAllowedError
from backend.app.models import AccessPointPlanRequest, InterfaceStatus, PoePort
from backend.app.planner import build_access_point_plan


INTERFACES = [
    InterfaceStatus(
        port=f"Gi0/{number}",
        status="notconnect",
        vlan="1",
        policyState=(
            "PROTECTED" if number == 1 else "OPERABLE" if number <= 8 else "UNMANAGED"
        ),
    )
    for number in range(1, 11)
]
POE_PORTS = [PoePort(interface=f"Gi0/{number}", oper="off") for number in range(1, 9)]


def make_plan(interface="Gi0/4", vlan=1):
    return build_access_point_plan(
        AccessPointPlanRequest(interface=interface, vlan=vlan),
        interfaces=INTERFACES,
        poe_ports=POE_PORTS,
        vlan_ids={1, 10},
    )


def test_valid_access_point_plan_is_deterministic_and_non_executable():
    first = make_plan()
    second = make_plan()

    assert first.status == "VALID"
    assert first.plan_id == second.plan_id
    assert first.apply_available is False
    assert first.proposed_ios == [
        "configure terminal",
        "interface GigabitEthernet0/4",
        "description Wireless access point",
        "switchport mode access",
        "switchport access vlan 1",
        "spanning-tree portfast",
        "power inline auto",
        "no shutdown",
        "end",
    ]
    assert "write memory" not in first.proposed_ios


def test_protected_interface_is_blocked_without_a_proposal():
    plan = make_plan(interface="Gi0/1")

    assert plan.status == "INVALID"
    assert plan.proposed_ios == []
    assert plan.apply_available is False
    assert next(check for check in plan.checks if check.name == "target_is_safe").passed is False


def test_missing_vlan_and_non_poe_target_are_blocked():
    plan = make_plan(interface="Gi0/9", vlan=99)

    assert plan.status == "INVALID"
    assert plan.proposed_ios == []
    assert {check.name for check in plan.checks if not check.passed} == {
        "target_is_safe",
        "poe_supported",
        "vlan_exists",
    }


def test_interface_injection_is_rejected_before_plan_generation():
    with pytest.raises(CommandNotAllowedError):
        make_plan(interface="Gi0/4 ; reload")
