"""Connection-test behaviour, including what it must never say or leak."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import connection_test as ct
from backend.app.errors import (
    CredentialsMissingError,
    HostKeyChangedError,
    LegacySshNegotiationError,
    SwitchConnectionError,
)
from backend.app.main import app


SECRET = "hunter2-not-a-real-secret"


class _FakeClient:
    """Serves canned IOS output for the two commands the test uses."""

    def __init__(self, version: str = "", interfaces: str = "", fail: Exception | None = None):
        self.version = version
        self.interfaces = interfaces
        self.fail = fail
        self.closed = False
        self.commands: list[str] = []

    def run(self, symbol: str) -> str:
        self.commands.append(symbol)
        if self.fail is not None:
            raise self.fail
        if symbol == "show_version":
            return self.version
        if symbol == "show_interfaces_status":
            return self.interfaces
        raise AssertionError(f"connection test ran an unexpected command: {symbol}")

    def close(self) -> None:
        self.closed = True

    def is_alive(self) -> bool:
        return True

    def refresh_prompt(self) -> None:
        return None


IOS_VERSION = """Cisco IOS Software, C3560C Software (C3560c405ex-UNIVERSALK9-M), Version 12.2(55)EX2, RELEASE SOFTWARE (fc1)
SWITCHOPS-TEST-SW1 uptime is 4 days, 7 hours
cisco WS-C3560CG-8PC-S (PowerPC405) processor (revision V03) with 131072K bytes of memory.
"""

IFACE_STATUS = """Port      Name               Status       Vlan       Duplex  Speed Type
Gi0/1     Uplink             connected    1          a-full a-1000 10/100/1000BaseTX
Gi0/2     Test Workstation            connected    1          a-full a-1000 10/100/1000BaseTX
"""


@pytest.fixture
def real_mode(monkeypatch):
    """Pretend mock mode is off and credentials exist, without touching the store."""
    settings = ct.get_settings()
    monkeypatch.setattr(settings, "mock_mode", False, raising=False)
    monkeypatch.setattr(ct, "get_settings", lambda: settings)
    monkeypatch.setattr(
        ct,
        "get_credential_store",
        lambda: type(
            "Store",
            (),
            {
                "status": staticmethod(
                    lambda: {
                        "configured": True,
                        "storage": "keyring",
                        "switch_host": "192.0.2.10",
                        "switch_username": "synthetic-user",
                    }
                )
            },
        )(),
    )
    monkeypatch.setattr(ct, "_probe_tcp", lambda *args, **kwargs: True)
    monkeypatch.setattr(ct, "is_host_pinned", lambda host: True)
    return settings


def _checks(result) -> dict[str, str]:
    return {check.id: check.status for check in result.checks}


def test_mock_mode_contacts_nothing_and_says_so(monkeypatch):
    settings = ct.get_settings()
    monkeypatch.setattr(settings, "mock_mode", True, raising=False)
    monkeypatch.setattr(ct, "get_settings", lambda: settings)
    result = ct.run_connection_test()
    assert result.mode == "mock"
    assert result.ok is True
    assert "did not contact a device" in result.summary
    assert set(_checks(result).values()) == {"skipped"}


def test_healthy_path_reports_every_check_and_changes_nothing(real_mode, monkeypatch):
    client = _FakeClient(version=IOS_VERSION, interfaces=IFACE_STATUS)
    result = ct.run_connection_test(client=client)
    assert result.ok is True
    assert _checks(result) == {
        "credentials": "pass",
        "reachable": "pass",
        "ssh": "pass",
        "host_key": "pass",
        "auth": "pass",
        "platform": "pass",
        "read_ops": "pass",
    }
    # Only read-only show commands, and the worker's session is left open:
    # it belongs to the device worker, not to this diagnostic.
    assert client.commands == ["show_version", "show_interfaces_status"]
    assert client.closed is False
    assert "Nothing was changed" in result.summary


def test_missing_credentials_stops_before_touching_the_network(real_mode, monkeypatch):
    monkeypatch.setattr(
        ct,
        "get_credential_store",
        lambda: type("Store", (), {"status": staticmethod(lambda: {"configured": False})})(),
    )
    monkeypatch.setattr(ct, "_probe_tcp", lambda *a, **k: pytest.fail("must not probe"))

    result = ct.run_connection_test()
    assert result.ok is False
    assert result.failure_code == "credentials_missing"
    assert _checks(result)["credentials"] == "fail"
    assert _checks(result)["reachable"] == "skipped"


def test_unreachable_host_does_not_attempt_authentication(real_mode, monkeypatch):
    monkeypatch.setattr(ct, "_probe_tcp", lambda *a, **k: False)

    result = ct.run_connection_test()
    assert result.ok is False
    assert result.failure_code == "host_unreachable"
    assert _checks(result)["reachable"] == "fail"
    assert _checks(result)["auth"] == "skipped"


def test_host_key_change_is_reported_as_blocked_for_safety(real_mode, monkeypatch):
    result = ct.run_connection_test(
        session_error=HostKeyChangedError("The switch SSH host key changed; connection refused.")
    )
    assert result.ok is False
    assert result.failure_code == "host_key_changed"
    checks = _checks(result)
    assert checks["ssh"] == "pass"
    assert checks["host_key"] == "fail"
    assert checks["auth"] == "skipped"
    assert "Blocked for safety" in result.summary


def test_authentication_failure_is_distinguished_from_unreachable(real_mode, monkeypatch):
    class NetmikoAuthenticationException(Exception):
        pass

    cause = NetmikoAuthenticationException(f"Authentication to 192.0.2.10 failed: {SECRET}")
    error = SwitchConnectionError("Netmiko connection failed", detail=str(cause))
    error.__cause__ = cause
    result = ct.run_connection_test(session_error=error)
    assert result.failure_code == "authentication_failed"
    checks = _checks(result)
    assert checks["ssh"] == "pass"
    assert checks["host_key"] == "pass"
    assert checks["auth"] == "fail"
    assert checks["platform"] == "skipped"


def test_negotiation_failure_is_reported_at_the_ssh_step(real_mode, monkeypatch):
    result = ct.run_connection_test(session_error=LegacySshNegotiationError("no shared kex"))
    assert result.failure_code == "ssh_negotiation_failed"
    assert _checks(result)["ssh"] == "fail"


def test_non_ios_platform_is_refused_rather_than_assumed(real_mode, monkeypatch):
    client = _FakeClient(version="BusyBox v1.36 built-in shell", interfaces=IFACE_STATUS)
    result = ct.run_connection_test(client=client)
    assert result.ok is False
    assert result.failure_code == "unsupported_platform"
    assert _checks(result)["platform"] == "fail"
    assert _checks(result)["read_ops"] == "skipped"


def test_unusable_read_output_is_a_failure_not_a_pass(real_mode, monkeypatch):
    client = _FakeClient(version=IOS_VERSION, interfaces="% Invalid input detected")
    result = ct.run_connection_test(client=client)
    assert result.ok is False
    assert result.failure_code == "read_ops_unavailable"
    assert _checks(result)["read_ops"] == "fail"


def test_no_failure_detail_echoes_the_underlying_exception(real_mode, monkeypatch):
    """A server-side message must never reach the API response verbatim."""

    result = ct.run_connection_test(
        session_error=SwitchConnectionError(
            "Netmiko connection failed",
            detail=f"password={SECRET} user=synthetic-user path=C:/Synthetic/credentials.json",
        )
    )
    blob = result.model_dump_json()
    assert SECRET not in blob
    assert "synthetic-user" not in blob
    assert "creds.json" not in blob
    assert "C:/Synthetic" not in blob
    # Only the fixed vocabulary is exposed.
    assert result.failure_code in ct.FAILURE_TEXT


def test_result_never_claims_untested_capabilities(real_mode, monkeypatch):
    client = _FakeClient(version=IOS_VERSION, interfaces=IFACE_STATUS)
    blob = ct.run_connection_test(client=client).model_dump_json().lower()
    for forbidden in ("internet", "privilege 15", "privilege level", "healthy switch", "gateway reachable"):
        assert forbidden not in blob


def test_a_failed_read_does_not_close_the_shared_session(real_mode):
    """The session belongs to the device worker; the test must not tear it down."""
    client = _FakeClient(fail=TimeoutError("read timed out"))
    result = ct.run_connection_test(client=client)
    assert result.ok is False
    assert result.failure_code == "timed_out"
    assert client.closed is False


def test_endpoint_is_reachable_and_serialized_in_mock_mode():
    with TestClient(app) as client:
        response = client.post("/api/setup/test-connection")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "mock"
    assert body["ok"] is True
    assert [check["id"] for check in body["checks"]] == [key for key, _ in ct.CHECK_LABELS]


def test_endpoint_rejects_a_foreign_origin():
    with TestClient(app) as client:
        response = client.post(
            "/api/setup/test-connection",
            headers={"origin": "https://evil.example"},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"


def test_connection_test_is_not_exposed_as_a_get():
    with TestClient(app) as client:
        assert client.get("/api/setup/test-connection").status_code == 405
