from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.operations import (
    classify_ios_response,
    config_fingerprints,
    get_save_tracker,
    run_operation,
    save_running_config,
)
from app.errors import WriteActionsDisabledError
from app.switch_client import MockSwitchClient


@pytest.fixture(autouse=True)
def authorized_operation_policy(tmp_path, monkeypatch):
    from app import operations as operations_mod
    from app.interface_policy import InterfacePolicyStore
    from app.operations import get_write_lock

    host = "192.0.2.10"
    store = InterfacePolicyStore(tmp_path / "interface-policy.json")
    store.set_state(host, "Gi0/6", "OPERABLE")
    store.set_controlled_writes(True)
    credentials = SimpleNamespace(status=lambda: {"switch_host": host})
    monkeypatch.setattr(operations_mod, "get_interface_policy_store", lambda: store)
    monkeypatch.setattr(operations_mod, "get_credential_store", lambda: credentials)
    get_write_lock().unlock()
    yield
    get_write_lock().lock()


def test_ios_rejection_is_classified_from_returned_text():
    assert classify_ios_response("% Invalid input detected at '^' marker.")
    assert classify_ios_response("% Authorization failed")
    assert classify_ios_response("Building configuration...\n[OK]") is None


def test_operation_helper_rechecks_ephemeral_session_lock():
    from app.operations import get_write_lock

    get_write_lock().lock()
    with pytest.raises(WriteActionsDisabledError):
        run_operation(MockSwitchClient(), kind="admin_up", interface="Gi0/6")


def test_save_helper_rechecks_ephemeral_session_lock():
    from app.operations import get_write_lock

    get_write_lock().lock()
    with pytest.raises(WriteActionsDisabledError):
        save_running_config(MockSwitchClient())


def test_mock_operation_changes_running_only_and_verifies():
    client = MockSwitchClient()
    before_running, before_startup = config_fingerprints(client)
    assert before_running == before_startup

    result = run_operation(client, kind="admin_up", interface="Gi0/6")

    assert result.status == "success"
    assert result.requires_save is True
    assert result.commands[-1] == "end"
    assert "write memory" not in result.commands
    assert [stage.name for stage in result.stages] == [
        "precheck",
        "backup",
        "execute",
        "verify",
        "audit",
    ]
    after_running, after_startup = config_fingerprints(client)
    assert after_running != after_startup


def test_already_satisfied_operation_is_a_verified_noop():
    client = MockSwitchClient()
    result = run_operation(client, kind="admin_down", interface="Gi0/6")
    assert result.status == "success"
    assert result.requires_save is False
    assert result.commands == []
    assert next(stage for stage in result.stages if stage.name == "execute").status == "skipped"


class _VerifyFailureClient(MockSwitchClient):
    def __init__(self) -> None:
        super().__init__()
        self._ignored_intent = False

    def run_raw_action(self, commands: list[str]) -> str:
        if commands[0:1] == ["configure terminal"] and commands[2] == "no shutdown" and not self._ignored_intent:
            self._ignored_intent = True
            return "Mock IOS appeared to accept the command."
        return super().run_raw_action(commands)


def test_verification_failure_rolls_back_and_verifies_original_admin_state():
    result = run_operation(_VerifyFailureClient(), kind="admin_up", interface="Gi0/6")
    assert result.status == "rolled_back"
    assert result.rolled_back is True
    assert result.requires_save is False
    rollback = next(stage for stage in result.stages if stage.name == "rollback")
    assert rollback.status == "ok"
    assert "shutdown" in result.commands


class _RollbackFailureClient(_VerifyFailureClient):
    def run_raw_action(self, commands: list[str]) -> str:
        if self._ignored_intent and commands[0:1] == ["configure terminal"]:
            return "% Authorization failed"
        return super().run_raw_action(commands)


def test_failed_rollback_is_not_misreported_as_success():
    result = run_operation(_RollbackFailureClient(), kind="admin_up", interface="Gi0/6")
    assert result.status == "failed"
    assert result.rolled_back is False
    assert result.requires_save is True
    assert next(stage for stage in result.stages if stage.name == "rollback").status == "failed"


class _PoeVerifyFailureClient(MockSwitchClient):
    def __init__(self) -> None:
        super().__init__()
        super().run_raw_action(
            ["configure terminal", "interface GigabitEthernet0/6", "power inline never", "end"]
        )
        self._lie_once = False

    def run_raw_action(self, commands: list[str]) -> str:
        output = super().run_raw_action(commands)
        if commands[0:1] == ["configure terminal"] and commands[2] == "power inline auto":
            self._lie_once = True
        return output

    def run(self, symbol: str) -> str:
        output = super().run(symbol)
        if symbol == "show_power_inline" and self._lie_once:
            self._lie_once = False
            return output.replace("Gi0/6     auto", "Gi0/6     never", 1)
        return output


def test_poe_rollback_restores_observed_prior_policy_not_inverse_guess():
    result = run_operation(_PoeVerifyFailureClient(), kind="poe_auto", interface="Gi0/6")
    assert result.status == "rolled_back"
    assert result.rolled_back is True
    assert "power inline never" in result.commands


def test_explicit_mock_save_updates_startup_and_tracker():
    client = MockSwitchClient()
    run_operation(client, kind="admin_up", interface="Gi0/6")
    tracker = get_save_tracker()
    tracker.reset()
    tracker.record_change()

    success, _detail = save_running_config(client)
    tracker.record_save()

    assert success is True
    assert config_fingerprints(client)[0] == config_fingerprints(client)[1]
    assert tracker.state().running_modified is False
