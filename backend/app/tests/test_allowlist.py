import pytest

from app.command_registry import (
    ALLOWLISTED_INTERFACES,
    PROTECTED_INTERFACES,
    build_write_action,
    normalize_interface,
    resolve_read_command,
    sanitize_description,
)
from app.errors import (
    CommandNotAllowedError,
    ProtectedInterfaceError,
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


def test_normalize_vlan():
    assert normalize_interface("Vlan1") == "Vlan1"


@pytest.mark.parametrize(
    "value",
    ["Gi0/6\nshutdown", "Gi0//6", "Gi0/6;shutdown", "Vlan1junk"],
)
def test_interface_injection_forms_are_rejected(value):
    with pytest.raises(CommandNotAllowedError):
        normalize_interface(value)


def test_protected_interfaces_rejected():
    for iface in ("Gi0/1", "Gi0/2", "Vlan1"):
        with pytest.raises(ProtectedInterfaceError):
            build_write_action("disable_port", interface=iface)


def test_allowlisted_interface_accepted():
    plan = build_write_action("enable_port", interface="Gi0/6")
    assert plan.interface == "GigabitEthernet0/6"
    assert "interface GigabitEthernet0/6" in plan.commands
    assert plan.commands[-1] == "write memory"


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


def test_allowlists_and_protected_disjoint():
    assert set(ALLOWLISTED_INTERFACES).isdisjoint(set(PROTECTED_INTERFACES))
