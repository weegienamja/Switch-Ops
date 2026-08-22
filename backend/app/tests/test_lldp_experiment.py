from types import SimpleNamespace

import pytest

from backend.app.tools import lldp_experiment as experiment


DETAIL = """
------------------------------------------------
Local Intf: Gi0/1
Chassis id: 0200.0000.000B
Port id: Internet 1
System Name: TEST-GATEWAY-01-HQ
System Description: Cisco Meraki TEST-GATEWAY-01
Enabled Capabilities: B,R
Management Addresses:
    IP: 192.0.2.19
"""


class AuditSink:
    def __init__(self):
        self.events = []

    def record(self, **kwargs):
        self.events.append(kwargs)


class FakeClient:
    def __init__(self, *, fail_after_enable: bool = False):
        self.enabled = False
        self.startup = "hostname lab-sw\ninterface GigabitEthernet0/1\n description uplink\n"
        self.actions: list[list[str]] = []
        self.fail_after_enable = fail_after_enable

    def run(self, symbol: str) -> str:
        if symbol == "terminal_length_0":
            return ""
        if symbol == "show_running_config":
            return self.startup + ("lldp run\n" if self.enabled else "")
        if symbol == "show_startup_config":
            return self.startup
        if symbol in {"show_lldp_neighbors", "show_lldp_neighbors_detail"}:
            if self.enabled and self.fail_after_enable:
                raise RuntimeError("simulated read failure")
            if not self.enabled:
                return "% LLDP is not enabled"
            return DETAIL if symbol.endswith("detail") else "TEST-GATEWAY-01-HQ Gi0/1 120 B,R Internet1"
        raise AssertionError(symbol)

    def run_raw_action(self, commands: list[str]) -> str:
        self.actions.append(list(commands))
        assert "write memory" not in commands
        if commands == experiment.ENABLE_LLDP:
            self.enabled = True
        elif commands == experiment.DISABLE_LLDP:
            self.enabled = False
        else:
            raise AssertionError(commands)
        return "accepted"


def _wire(monkeypatch):
    audit = AuditSink()
    monkeypatch.setattr(experiment, "get_audit_store", lambda: audit)
    monkeypatch.setattr(
        experiment,
        "backup_running_config",
        lambda *_args, **_kwargs: SimpleNamespace(filename="safe-backup.txt"),
    )
    return audit


def test_temporary_lldp_experiment_observes_and_restores(monkeypatch):
    audit = _wire(monkeypatch)
    client = FakeClient()
    result = experiment.run_temporary_lldp_experiment(
        client, wait_seconds=65, sleeper=lambda _seconds: None
    )
    assert result.status == "complete"
    assert result.neighbors == ("TEST-GATEWAY-01-HQ",)
    assert result.running_restored is True
    assert result.startup_unchanged is True
    assert client.enabled is False
    assert client.actions == [experiment.ENABLE_LLDP, experiment.DISABLE_LLDP]
    assert [event["action"] for event in audit.events] == [
        "temporary_lldp_enable",
        "temporary_lldp_restore",
    ]


def test_temporary_lldp_experiment_restores_after_observation_failure(monkeypatch):
    _wire(monkeypatch)
    client = FakeClient(fail_after_enable=True)
    with pytest.raises(RuntimeError, match="simulated read failure"):
        experiment.run_temporary_lldp_experiment(
            client, wait_seconds=0, sleeper=lambda _seconds: None
        )
    assert client.enabled is False
    assert client.actions[-1] == experiment.DISABLE_LLDP
