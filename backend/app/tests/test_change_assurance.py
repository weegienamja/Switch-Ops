from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.change_assurance import ChangeAssuranceService
from app.change_models import ChangePlanRequest, ChangeStep
from app.change_store import ChangeStore
from app.errors import CommandNotAllowedError
from app.interface_policy import InterfacePolicyStore, device_key
from app.operations import get_write_lock
from app.switch_client import MockSwitchClient


HOST = "192.0.2.40"
DEVICE = f"device-{device_key(HOST)[:16]}"


def context(*, local_host: bool = False, learned_behind: bool = False) -> dict:
    devices = []
    links = []
    if local_host:
        devices.append({
            "id": "local-host",
            "name": "This SwitchOps PC",
            "connectedInterface": "Gi0/6",
            "identitySource": "local-host",
            "existenceState": "observed",
        })
        links.append({
            "id": "local-link",
            "fromInterface": "Gi0/6",
            "toDeviceId": "local-host",
            "relationship": "attached-endpoint",
            "evidenceIds": ["ev-local-host"],
        })
    if learned_behind:
        links.append({
            "id": "behind-link",
            "fromInterface": "Gi0/6",
            "toDeviceId": "behind-1",
            "relationship": "learned-behind",
            "evidenceIds": ["ev-mac"],
        })
    return {
        "connection": {"state": "live"},
        "health": {"state": "HEALTHY", "reasons": []},
        "topology": {
            "generatedAt": "2026-08-23T12:00:00Z",
            "interfaces": [{
                "port": "Gi0/6",
                "role": "access",
                "freshness": "current",
                "expectedName": "Synthetic spare port",
                "evidenceIds": ["ev-interface"],
            }],
            "devices": devices,
            "links": links,
        },
        "reconciliation": {
            "interfaces": [{"interface": "Gi0/6", "status": "aligned"}],
        },
    }


@pytest.fixture
def assurance(tmp_path, monkeypatch):
    from app import change_assurance as assurance_mod
    from app import operations as operations_mod

    policy = InterfacePolicyStore(tmp_path / "interface-policy.json")
    policy.set_state(HOST, "Gi0/6", "OPERABLE")
    policy.set_controlled_writes(True)
    credentials = SimpleNamespace(status=lambda: {"switch_host": HOST})
    monkeypatch.setattr(assurance_mod, "get_interface_policy_store", lambda: policy)
    monkeypatch.setattr(operations_mod, "get_interface_policy_store", lambda: policy)
    monkeypatch.setattr(assurance_mod, "get_credential_store", lambda: credentials)
    monkeypatch.setattr(operations_mod, "get_credential_store", lambda: credentials)
    store = ChangeStore(tmp_path / "change-sessions.sqlite")
    service = ChangeAssuranceService(store)
    get_write_lock().lock()
    yield service, store
    get_write_lock().lock()


def create(service: ChangeAssuranceService, kind: str = "admin_up", value: str | None = None):
    return service.create_plan(
        ChangePlanRequest(steps=[ChangeStep(interface="Gi0/6", kind=kind, value=value)]),
        device_id=DEVICE,
    )


def test_v06_schema_refuses_multi_step_atomicity():
    with pytest.raises(ValidationError):
        ChangePlanRequest(steps=[
            ChangeStep(interface="Gi0/6", kind="admin_up"),
            ChangeStep(interface="Gi0/7", kind="admin_up"),
        ])


def test_preflight_is_read_only_and_does_not_require_unlock(assurance):
    service, _store = assurance
    session = create(service)
    result = service.run_preflight(
        session.id,
        MockSwitchClient(),
        context=context(learned_behind=True),
    )

    assert get_write_lock().unlocked is False
    assert result.status == "ready"
    assert result.operation_result is None
    assert result.preflight is not None
    assert next(check for check in result.preflight.checks if check.code == "session_unlock").status == "info"
    assert next(check for check in result.preflight.checks if check.code == "learned_behind").status == "warn"
    assert result.preflight.impact.learned_behind >= 1


def test_preflight_runs_but_blocks_when_controlled_writes_are_disabled(assurance):
    from app import change_assurance as assurance_mod

    service, _store = assurance
    assurance_mod.get_interface_policy_store().set_controlled_writes(False)
    session = create(service)

    result = service.run_preflight(session.id, MockSwitchClient(), context=context())

    assert result.status == "blocked"
    assert result.preflight is not None
    gate = next(check for check in result.preflight.checks if check.code == "controlled_writes")
    assert gate.status == "block"
    assert result.operation_result is None


def test_plan_is_bound_to_the_configured_device(assurance, monkeypatch):
    from app import change_assurance as assurance_mod

    service, _store = assurance
    session = create(service)
    changed_credentials = SimpleNamespace(status=lambda: {"switch_host": "192.0.2.99"})
    monkeypatch.setattr(assurance_mod, "get_credential_store", lambda: changed_credentials)

    result = service.run_preflight(session.id, MockSwitchClient(), context=context())

    assert result.status == "blocked"
    assert result.preflight is not None
    target = next(check for check in result.preflight.checks if check.code == "device_target")
    assert target.status == "block"


def test_disruptive_change_is_blocked_on_confirmed_local_control_path(assurance):
    service, _store = assurance
    session = create(service, "admin_down")
    result = service.run_preflight(session.id, MockSwitchClient(), context=context(local_host=True))

    assert result.status == "blocked"
    assert result.preflight is not None
    control = next(check for check in result.preflight.checks if check.code == "control_path")
    assert control.status == "block"
    assert result.preflight.impact.control_path == "confirmed"


def test_poe_disable_is_also_blocked_on_confirmed_control_path(assurance):
    service, _store = assurance
    session = create(service, "poe_never")

    result = service.run_preflight(session.id, MockSwitchClient(), context=context(local_host=True))

    assert result.status == "blocked"
    assert result.preflight is not None
    control = next(check for check in result.preflight.checks if check.code == "control_path")
    assert control.status == "block"


def test_final_preflight_blocks_if_control_path_changed_after_review(assurance):
    service, _store = assurance
    client = MockSwitchClient()
    session = create(service, "admin_down")
    assert service.run_preflight(session.id, client, context=context()).status == "ready"
    get_write_lock().unlock()

    result = service.execute(
        session.id,
        client,
        context_provider=lambda: context(local_host=True),
    )

    assert result.status == "blocked"
    assert result.operation_result is None
    assert result.preflight is not None
    control = next(check for check in result.preflight.checks if check.code == "control_path")
    assert control.status == "block"
    assert "before IOS configuration" in result.outcome_detail


def test_successful_change_persists_before_after_and_never_saves_startup(assurance):
    service, store = assurance
    client = MockSwitchClient()
    session = create(service)
    preflight = service.run_preflight(session.id, client, context=context())
    assert preflight.status == "ready"
    get_write_lock().unlock()

    result = service.execute(
        session.id,
        client,
        context_provider=context,
    )

    assert result.status == "succeeded"
    assert result.before_snapshot is not None
    assert result.after_snapshot is not None
    assert result.comparison is not None
    assert result.comparison.direct_postcondition == "met"
    assert result.operation_result is not None
    assert result.operation_result.requires_save is True
    assert "write memory" not in result.operation_result.commands
    assert result.before_snapshot.configuration.startup_fingerprint == result.after_snapshot.configuration.startup_fingerprint
    assert store.get(result.id).status == "succeeded"  # type: ignore[union-attr]


class _VerifyFailureClient(MockSwitchClient):
    def __init__(self) -> None:
        super().__init__()
        self._ignored_intent = False

    def run_raw_action(self, commands: list[str]) -> str:
        if commands[0:1] == ["configure terminal"] and commands[2] == "no shutdown" and not self._ignored_intent:
            self._ignored_intent = True
            return "Mock IOS appeared to accept the command."
        return super().run_raw_action(commands)


def test_verified_primitive_rollback_becomes_rolled_back_session(assurance):
    service, _store = assurance
    client = _VerifyFailureClient()
    session = create(service)
    assert service.run_preflight(session.id, client, context=context()).status == "ready"
    get_write_lock().unlock()

    result = service.execute(session.id, client, context_provider=context)

    assert result.status == "rolled_back"
    assert result.operation_result is not None
    assert result.operation_result.rolled_back is True
    assert result.after_snapshot is not None


class _CollateralChangeClient(MockSwitchClient):
    def run_raw_action(self, commands: list[str]) -> str:
        output = super().run_raw_action(commands)
        if commands[0:1] == ["configure terminal"] and commands[1] == "interface GigabitEthernet0/6":
            self._admin_overrides["GigabitEthernet0/5"] = True
        return output


def test_unrelated_observation_is_warning_not_false_causality_or_rollback(assurance):
    service, _store = assurance
    client = _CollateralChangeClient()
    session = create(service)
    assert service.run_preflight(session.id, client, context=context()).status == "ready"
    get_write_lock().unlock()

    result = service.execute(session.id, client, context_provider=context)

    assert result.status == "succeeded_with_warnings"
    assert result.comparison is not None
    assert any("Temporal proximity does not prove" in warning for warning in result.comparison.warnings)
    assert result.operation_result is not None and result.operation_result.status == "success"


class _AfterSnapshotFailureClient(MockSwitchClient):
    def __init__(self) -> None:
        super().__init__()
        self.changed = False

    def run_raw_action(self, commands: list[str]) -> str:
        output = super().run_raw_action(commands)
        if commands[0:1] == ["configure terminal"]:
            self.changed = True
        return output

    def run(self, symbol: str) -> str:
        if self.changed and symbol == "show_startup_config":
            raise RuntimeError("synthetic control-path loss")
        return super().run(symbol)


def test_missing_after_evidence_is_indeterminate_not_success(assurance):
    service, _store = assurance
    client = _AfterSnapshotFailureClient()
    session = create(service)
    assert service.run_preflight(session.id, client, context=context()).status == "ready"
    get_write_lock().unlock()

    result = service.execute(session.id, client, context_provider=context)

    assert result.status == "indeterminate"
    assert result.operation_result is not None
    assert "could not prove" in result.outcome_detail


def test_store_recovers_interrupted_session_as_indeterminate(assurance, tmp_path):
    service, _store = assurance
    path = tmp_path / "recover.sqlite"
    first = ChangeStore(path)
    session = service.create_plan(
        ChangePlanRequest(steps=[ChangeStep(interface="Gi0/6", kind="admin_up")]),
        device_id=DEVICE,
    )
    # Copy through a dedicated store to simulate an application process that
    # stopped after persisting an in-flight lifecycle state.
    session.status = "executing"
    first.save(session)

    recovered = ChangeStore(path).get(session.id)
    assert recovered is not None
    assert recovered.status == "indeterminate"
    assert "stopped while this change was in progress" in recovered.outcome_detail


def test_terminal_session_cannot_be_reopened_or_overwritten(assurance):
    service, store = assurance
    session = create(service)
    session.status = "succeeded"
    session.outcome_detail = "Synthetic completed audit record."
    store.save(session)

    with pytest.raises(CommandNotAllowedError, match="immutable"):
        service.run_preflight(session.id, MockSwitchClient(), context=context())

    unchanged = service.block_before_execution(session.id, "Late duplicate request.")
    assert unchanged.status == "succeeded"
    assert unchanged.outcome_detail == "Synthetic completed audit record."
