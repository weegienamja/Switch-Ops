import os

os.environ.setdefault("SWITCH_MOCK_MODE", "true")

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


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


def test_dashboard_keeps_partial_data_when_ios_command_is_unsupported(monkeypatch):
    from app import main as main_mod
    from app.switch_client import MockSwitchClient

    class PartialClient(MockSwitchClient):
        def run(self, symbol: str) -> str:
            if symbol == "show_env_all":
                return "% Invalid input detected at '^' marker."
            return super().run(symbol)

    monkeypatch.setattr(main_mod, "get_switch_client", lambda: PartialClient())
    r = client.get("/api/switch/dashboard")
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
    r = client.post("/api/switch/ports/Gi0-6/enable")
    assert r.status_code == 403


def test_cross_site_mutation_origin_is_rejected():
    r = client.post(
        "/api/switch/backup-config",
        headers={"origin": "https://example.invalid"},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "origin_not_allowed"


def test_protected_port_always_refused(monkeypatch):
    # Enable writes via a settings monkeypatch to confirm protected refusal still applies.
    from app import main as main_mod
    monkeypatch.setattr(main_mod.settings, "enable_write_actions", True)
    r = client.post("/api/switch/ports/Gi0-1/disable")
    # The protected check fires regardless of write-enable.
    assert r.status_code in (403,)


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
