from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.change_assurance import ChangeAssuranceService
from app.change_store import ChangeStore
from app.interface_policy import InterfacePolicyStore, device_key
from app.main import app
from app.operations import get_save_tracker, get_write_lock
from app.switch_client import MockSwitchClient


def _context() -> dict:
    return {
        "connection": {"state": "live"},
        "health": {"state": "HEALTHY", "reasons": []},
        "topology": {
            "generatedAt": "2026-08-23T12:00:00Z",
            "interfaces": [{
                "port": "Gi0/6",
                "role": "access",
                "freshness": "current",
                "evidenceIds": ["ev-interface"],
            }],
            "devices": [],
            "links": [],
        },
        "reconciliation": {"interfaces": [{"interface": "Gi0/6", "status": "aligned"}]},
    }


def test_change_session_api_preserves_plan_preflight_execute_boundary(tmp_path, monkeypatch):
    from app import change_assurance as assurance_mod
    from app import main as main_mod
    from app import operations as operations_mod

    host = "192.0.2.60"
    policy = InterfacePolicyStore(tmp_path / "policy.json")
    policy.set_state(host, "Gi0/6", "OPERABLE")
    policy.set_controlled_writes(True)
    credentials = SimpleNamespace(status=lambda: {"switch_host": host})
    service = ChangeAssuranceService(ChangeStore(tmp_path / "changes.sqlite"))
    switch = MockSwitchClient()
    monkeypatch.setattr(assurance_mod, "get_interface_policy_store", lambda: policy)
    monkeypatch.setattr(operations_mod, "get_interface_policy_store", lambda: policy)
    monkeypatch.setattr(assurance_mod, "get_credential_store", lambda: credentials)
    monkeypatch.setattr(operations_mod, "get_credential_store", lambda: credentials)
    monkeypatch.setattr(main_mod, "get_change_assurance_service", lambda: service)
    monkeypatch.setattr(
        main_mod,
        "_current_change_device_id",
        lambda: f"device-{device_key(host)[:16]}",
    )
    monkeypatch.setattr(main_mod, "_assurance_context", _context)
    monkeypatch.setattr(main_mod, "on_device", lambda _kind, run, **_kwargs: run(switch))
    get_save_tracker().reset()
    get_write_lock().lock()

    client = TestClient(app)
    created = client.post(
        "/api/change-sessions",
        json={"steps": [{"interface": "Gi0/6", "kind": "admin_up"}]},
    )
    assert created.status_code == 200
    session_id = created.json()["id"]
    assert created.json()["status"] == "planned"
    assert get_write_lock().unlocked is False

    preflight = client.post(f"/api/change-sessions/{session_id}/preflight")
    assert preflight.status_code == 200
    assert preflight.json()["status"] == "ready"
    assert preflight.json()["operationResult"] is None
    assert get_write_lock().unlocked is False

    locked_execute = client.post(f"/api/change-sessions/{session_id}/execute")
    assert locked_execute.status_code == 403

    get_write_lock().unlock()
    executed = client.post(f"/api/change-sessions/{session_id}/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "succeeded"
    assert executed.json()["operationResult"]["requiresSave"] is True
    assert get_save_tracker().state().running_modified is True

    history = client.get("/api/change-sessions")
    assert history.status_code == 200
    assert history.json()["sessions"][0]["id"] == session_id
    assert client.get(f"/api/change-sessions/{session_id}").json()["status"] == "succeeded"
    get_write_lock().lock()
