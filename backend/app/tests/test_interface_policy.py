import json

import pytest

from app.errors import CommandNotAllowedError, ProtectedInterfaceError, WriteActionsDisabledError
from app.interface_policy import InterfacePolicyStore, device_key


HOST = "192.0.2.10"


def test_new_policy_is_read_only_and_unknown_interfaces_are_unmanaged(tmp_path):
    store = InterfacePolicyStore(tmp_path / "policy.json")

    assert store.controlled_writes_enabled() is False
    assert store.state_for(HOST, "Gi1/0/48") == "UNMANAGED"
    status = store.status(HOST, ["Gi1/0/48"])
    assert status["valid"] is True
    assert status["controlledWritesEnabled"] is False
    assert status["interfaces"] == {"Gi1/0/48": "UNMANAGED"}


def test_policy_is_scoped_by_hashed_device_address(tmp_path):
    path = tmp_path / "policy.json"
    store = InterfacePolicyStore(path)
    store.set_state(HOST, "Gi1/0/48", "OPERABLE")

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert HOST not in path.read_text(encoding="utf-8")
    assert list(raw["devices"]) == [device_key(HOST)]
    assert store.state_for(HOST, "Gi1/0/48") == "OPERABLE"
    assert store.state_for("192.0.2.11", "Gi1/0/48") == "UNMANAGED"


def test_operable_requires_physical_interface(tmp_path):
    store = InterfacePolicyStore(tmp_path / "policy.json")
    with pytest.raises(CommandNotAllowedError):
        store.set_state(HOST, "Vlan10", "OPERABLE")


def test_protected_and_unmanaged_interfaces_fail_closed(tmp_path):
    store = InterfacePolicyStore(tmp_path / "policy.json")
    store.set_state(HOST, "Gi1/0/1", "PROTECTED")
    store.set_controlled_writes(True)

    with pytest.raises(ProtectedInterfaceError):
        with store.operation_guard(HOST, "Gi1/0/1"):
            pass
    with pytest.raises(CommandNotAllowedError):
        with store.operation_guard(HOST, "Gi1/0/2"):
            pass


def test_global_write_gate_is_checked_inside_operation_guard(tmp_path):
    store = InterfacePolicyStore(tmp_path / "policy.json")
    store.set_state(HOST, "Gi1/0/48", "OPERABLE")

    with pytest.raises(WriteActionsDisabledError):
        with store.operation_guard(HOST, "Gi1/0/48"):
            pass

    store.set_controlled_writes(True)
    with store.operation_guard(HOST, "Gi1/0/48") as canonical:
        assert canonical == "GigabitEthernet1/0/48"


def test_invalid_file_disables_writes_and_policy_edits(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"schemaVersion": 1, "controlledWritesEnabled": true}', encoding="utf-8")
    store = InterfacePolicyStore(path)

    assert store.controlled_writes_enabled() is False
    assert store.state_for(HOST, "Gi1/0/48") == "UNMANAGED"
    assert store.status(HOST)["valid"] is False
    with pytest.raises(CommandNotAllowedError):
        store.set_state(HOST, "Gi1/0/48", "OPERABLE")


def test_management_protection_never_grants_authority(tmp_path):
    store = InterfacePolicyStore(tmp_path / "policy.json")
    store.ensure_protected(HOST, "Vlan10")

    assert store.state_for(HOST, "Vlan10") == "PROTECTED"
    assert store.controlled_writes_enabled() is False
