import os

os.environ.setdefault("SWITCH_MOCK_MODE", "true")

from fastapi.testclient import TestClient

from app import device_session
from app.main import app


client = TestClient(app)


def install_write_policy(tmp_path, monkeypatch, states):
    from types import SimpleNamespace
    from app import main as main_mod
    from app import operations as operations_mod
    from app.interface_policy import InterfacePolicyStore

    host = "192.0.2.10"
    store = InterfacePolicyStore(tmp_path / "interface-policy.json")
    for interface, state in states.items():
        store.set_state(host, interface, state)
    store.set_controlled_writes(True)
    credentials = SimpleNamespace(status=lambda: {"switch_host": host})
    monkeypatch.setattr(main_mod, "get_interface_policy_store", lambda: store)
    monkeypatch.setattr(operations_mod, "get_interface_policy_store", lambda: store)
    monkeypatch.setattr(main_mod, "get_credential_store", lambda: credentials)
    monkeypatch.setattr(operations_mod, "get_credential_store", lambda: credentials)
    return store


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["mockMode"] is True


def test_setup_status_in_mock_mode():
    r = client.get("/api/setup/status")
    assert r.status_code == 200
    body = r.json()
    assert body["mockMode"] is True
    assert body["enableWriteActions"] is False


def test_setup_credentials_never_returns_password(tmp_path, monkeypatch):
    from app import credential_store as credential_mod
    from app import main as main_mod

    store = credential_mod.CredentialStore()
    store._keyring = None  # type: ignore[attr-defined]
    monkeypatch.setattr(credential_mod, "CRED_FILE", tmp_path / "credentials.json")
    monkeypatch.setattr(main_mod, "get_credential_store", lambda: store)
    payload = {
        "switchHost": "192.0.2.190",
        "switchUsername": "operator",
        "switchPassword": "__REPLACE_WITH_LOCAL_SECRET__",
        "switchEnableSecret": "__REPLACE_WITH_LOCAL_SECRET__",
        "switchDeviceType": "cisco_ios",
    }
    r = client.post("/api/setup/credentials", json=payload)
    assert r.status_code == 200
    text = r.text
    assert "__REPLACE_WITH_LOCAL_SECRET__" not in text


def test_validation_error_does_not_echo_rejected_secret():
    rejected = "x" * 1025
    r = client.post(
        "/api/setup/credentials",
        json={
            "switchHost": "invalid host with spaces",
            "switchUsername": "user",
            "switchPassword": rejected,
            "switchDeviceType": "cisco_ios",
        },
    )
    assert r.status_code == 422
    assert rejected not in r.text
    assert r.json()["code"] == "invalid_request"


def test_summary_endpoint_mock():
    r = client.get("/api/switch/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["hostname"] == "SWITCHOPS-TEST-SW1"
    assert body["model"] == "WS-C3560CG-8PC-S"
    assert body["temperatureState"] == "GREEN"
    assert body["healthy"] is True
    assert "Gi0/1" in body["connectedPorts"]


def test_dashboard_endpoint_mock_uses_complete_contract():
    r = client.get("/api/switch/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["telemetryComplete"] is True
    assert body["sectionErrors"] == {}
    assert len(body["interfaces"]["interfaces"]) == 10
    assert body["macTable"]["entries"]
    assert body["discovery"]["lldp"]["state"] == "disabled"
    assert body["discovery"]["localEndpoint"]["state"] == "unavailable"
    assert body["discovery"]["snmp"]["configured"] is False
    assert "community" not in r.text.lower()


def test_dashboard_keeps_partial_data_when_ios_command_is_unsupported(monkeypatch):
    from app import main as main_mod
    from app.switch_client import MockSwitchClient

    class PartialClient(MockSwitchClient):
        def run(self, symbol: str) -> str:
            if symbol == "show_env_all":
                return "% Invalid input detected at '^' marker."
            return super().run(symbol)

    # The worker holds its client for the process lifetime, so the patched
    # factory has to be in place before it connects and cleared afterwards.
    device_session.reset_device_session()
    monkeypatch.setattr(device_session, "get_switch_client", lambda: PartialClient())
    try:
        r = client.get("/api/switch/dashboard")
    finally:
        device_session.reset_device_session()
    assert r.status_code == 200
    body = r.json()
    assert len(body["interfaces"]["interfaces"]) == 10
    assert body["environment"]["state"] == "UNKNOWN"
    assert body["sectionErrors"]["environment"] == "unsupported_by_ios"
    assert body["summary"]["telemetryComplete"] is False


def test_interfaces_endpoint_mock():
    r = client.get("/api/switch/interfaces")
    assert r.status_code == 200
    body = r.json()
    assert len(body["interfaces"]) == 10


def test_errors_endpoint_mock():
    r = client.get("/api/switch/errors")
    assert r.status_code == 200
    body = r.json()
    assert body["totalErrors"] == 0
    assert body["healthy"] is True


def test_poe_endpoint_mock():
    r = client.get("/api/switch/poe")
    assert r.status_code == 200
    body = r.json()
    assert body["availableWatts"] == 124.0


def test_env_endpoint_mock():
    r = client.get("/api/switch/environment")
    assert r.status_code == 200
    body = r.json()
    assert body["temperatureC"] == 49
    assert body["state"] == "GREEN"


def test_write_endpoints_403_when_disabled():
    r = client.post(
        "/api/interfaces/Gi0-6/operations",
        json={"kind": "admin_up"},
    )
    assert r.status_code == 403


def test_policy_cannot_make_an_unobserved_interface_operable(tmp_path, monkeypatch):
    install_write_policy(tmp_path, monkeypatch, {})
    r = client.put(
        "/api/interface-policy/interfaces/Hu99-99-99",
        json={"state": "OPERABLE"},
    )
    assert r.status_code == 409
    assert "currently reported" in r.json()["detail"]


def test_cross_site_mutation_origin_is_rejected():
    r = client.post(
        "/api/switch/backup-config",
        headers={"origin": "https://example.invalid"},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "origin_not_allowed"


def test_protected_port_always_refused(tmp_path, monkeypatch):
    install_write_policy(tmp_path, monkeypatch, {"Gi0/1": "PROTECTED"})
    assert client.post("/api/control/unlock").status_code == 200
    try:
        r = client.post(
            "/api/interfaces/Gi0-01/operations",
            json={"kind": "admin_down"},
        )
        # Canonicalisation makes the leading-zero alias protected too.
        assert r.status_code == 403
    finally:
        client.post("/api/control/lock")


def test_operation_catalog_exposes_only_bounded_actions():
    r = client.get("/api/operations/catalog")
    assert r.status_code == 200
    body = r.json()
    assert {item["kind"] for item in body["operations"]} == {
        "admin_up",
        "admin_down",
        "poe_auto",
        "poe_never",
        "set_description",
    }
    assert body["arbitraryCli"] is False
    assert body["automaticSave"] is False
    assert "Gi0/1" not in body["writableInterfaces"]


def test_controlled_operation_api_streams_progress_and_never_auto_saves(tmp_path, monkeypatch):
    from app.live_state import get_live_state
    from app.operations import get_save_tracker, get_write_lock

    install_write_policy(tmp_path, monkeypatch, {"Gi0/6": "OPERABLE"})
    device_session.reset_device_session()
    get_save_tracker().reset()
    get_write_lock().lock()
    published: list[str] = []
    monkeypatch.setattr(
        get_live_state().hub,
        "publish",
        lambda event_type, _payload: published.append(event_type),
    )
    try:
        assert client.post("/api/control/unlock").json()["unlocked"] is True
        r = client.post(
            "/api/interfaces/Gi0-6/operations",
            json={"kind": "admin_up"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert body["interface"] == "Gi0/6"
        assert body["requiresSave"] is True
        assert "write memory" not in body["commands"]
        assert "operation_progress" in published
        assert "operation_complete" in published
        state = client.get("/api/config/state").json()
        assert state["runningModified"] is True
        assert state["pendingOperations"] == 1

        save = client.post("/api/config/save")
        assert save.status_code == 200
        assert save.json()["success"] is True
        assert save.json()["state"]["runningModified"] is False
    finally:
        client.post("/api/control/lock")
        get_save_tracker().reset()
        device_session.reset_device_session()


def test_unmanaged_port_is_rejected_before_device_access(tmp_path, monkeypatch):
    from app.operations import get_write_lock

    install_write_policy(tmp_path, monkeypatch, {})
    get_write_lock().unlock()
    try:
        r = client.post(
            "/api/interfaces/Gi0-9/operations",
            json={"kind": "admin_down"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "command_not_allowed"
    finally:
        get_write_lock().lock()


def test_new_process_lifespan_always_relocks_control(tmp_path, monkeypatch):
    from app.operations import get_write_lock

    install_write_policy(tmp_path, monkeypatch, {"Gi0/6": "OPERABLE"})
    get_write_lock().unlock()
    with TestClient(app) as fresh_process:
        assert fresh_process.get("/api/control/lock").json()["unlocked"] is False
    assert get_write_lock().status()["unlocked"] is False


def test_backup_config_mock():
    r = client.post("/api/switch/backup-config")
    assert r.status_code == 200
    body = r.json()
    assert body["filename"].startswith("SWITCHOPS-TEST-SW1-running-config-")
    assert body["sizeBytes"] > 0
    assert "<redacted>" in body["redactedPreview"]


def test_audit_endpoint():
    # at least one event from previous tests should exist
    r = client.get("/api/switch/audit")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["events"], list)
