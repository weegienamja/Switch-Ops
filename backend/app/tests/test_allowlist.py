import pytest

from app.command_registry import (
    build_write_action,
    is_physical_interface,
    normalize_interface,
    resolve_read_command,
    sanitize_description,
)
from app.errors import (
    CommandNotAllowedError,
)


def test_read_command_accepts_known_symbol():
    assert resolve_read_command("show_version") == "show version"


def test_read_command_rejects_unknown_symbol():
    with pytest.raises(CommandNotAllowedError):
        resolve_read_command("show_anything")


def test_read_command_rejects_raw_command_string():
    with pytest.raises(CommandNotAllowedError):
        resolve_read_command("show version")  # not a symbol


def test_normalize_interface_short_form():
    assert normalize_interface("Gi0/6") == "GigabitEthernet0/6"
    assert normalize_interface("gi0/6") == "GigabitEthernet0/6"
    assert normalize_interface("GigabitEthernet0/6") == "GigabitEthernet0/6"


def test_normalize_interface_supports_general_catalyst_layouts():
    assert normalize_interface("Gi1/0/48") == "GigabitEthernet1/0/48"
    assert normalize_interface("Te2/1/1") == "TenGigabitEthernet2/1/1"
    assert normalize_interface("Twe1/0/24") == "TwentyFiveGigE1/0/24"


def test_normalize_vlan():
    assert normalize_interface("Vlan1") == "Vlan1"


@pytest.mark.parametrize(
    "value",
    ["Gi0/6\nshutdown", "Gi0//6", "Gi0/6;shutdown", "Vlan1junk"],
)
def test_interface_injection_forms_are_rejected(value):
    with pytest.raises(CommandNotAllowedError):
        normalize_interface(value)


def test_vlan_is_readable_but_never_a_physical_write_target():
    assert not is_physical_interface("Vlan1")
    with pytest.raises(CommandNotAllowedError):
        build_write_action("disable_port", interface="Vlan1")


def test_validated_physical_interface_is_accepted_for_action_construction():
    plan = build_write_action("enable_port", interface="Gi0/6")
    assert plan.interface == "GigabitEthernet0/6"
    assert "interface GigabitEthernet0/6" in plan.commands
    assert plan.commands[-1] == "end"
    assert "write memory" not in plan.commands


def test_set_port_description_sanitizes():
    plan = build_write_action(
        "set_port_description", interface="Gi0/6", value="Test Bench PC"
    )
    assert "description Test Bench PC" in plan.commands


def test_set_port_description_rejects_bad_chars():
    with pytest.raises(CommandNotAllowedError):
        sanitize_description("rm -rf /; echo `id`")


def test_set_port_description_rejects_too_long():
    with pytest.raises(CommandNotAllowedError):
        sanitize_description("x" * 65)


def test_unknown_write_action_rejected():
    with pytest.raises(CommandNotAllowedError):
        build_write_action("delete_everything", interface="Gi0/6")


def test_action_construction_has_no_device_specific_port_layout():
    plan = build_write_action("enable_port", interface="Gi7/0/48")
    assert plan.interface == "GigabitEthernet7/0/48"
