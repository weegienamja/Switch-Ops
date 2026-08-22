"""Expected-topology API.

Recording intent is a SwitchOps-local action. These tests pin the boundary
that makes it safe: it never reaches the switch, and it never widens what the
switch will accept.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


DEVICE = "switch-mock-operatorlab-sw1"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client with an isolated intent database."""
    from backend.app import intent_store as intent_module

    store = intent_module.TopologyIntentStore(db_path=tmp_path / "intent.sqlite")
    monkeypatch.setattr(intent_module, "get_intent_store", lambda: store)
    monkeypatch.setattr("backend.app.main.get_intent_store", lambda: store)
    with TestClient(app) as test_client:
        yield test_client


def test_intent_starts_empty(client):
    response = client.get("/api/topology/intent", params={"deviceId": DEVICE})
    assert response.status_code == 200
    assert response.json() == {"deviceId": DEVICE, "relationships": []}


def test_recording_intent_round_trips(client):
    response = client.put(
        "/api/topology/intent/Gi0-4",
        params={"deviceId": DEVICE},
        json={
            "expectedName": "TEST-AP-01",
            "expectedDeviceType": "access-point",
            "expectedVendor": "Cisco Meraki",
            "expectedModel": "TEST-AP",
            "note": "moved to the MX during the hybrid-worker build",
        },
    )
    assert response.status_code == 200
    relationships = response.json()["relationships"]
    assert len(relationships) == 1
    recorded = relationships[0]
    assert recorded["interface"] == "Gi0/4"
    assert recorded["expectedName"] == "TEST-AP-01"
    assert recorded["source"] == "user-intent"
    assert recorded["expectedDeviceType"] == "access-point"


def test_recording_intent_sends_nothing_to_the_switch(client, monkeypatch):
    """The whole point: local metadata, no device session."""
    import backend.app.main as main_module

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("recording intent opened a switch session")

    monkeypatch.setattr(main_module, "switch_session", explode)
    response = client.put(
        "/api/topology/intent/Gi0-1",
        params={"deviceId": DEVICE},
        json={"expectedName": "TEST-GATEWAY-01"},
    )
    assert response.status_code == 200


def test_intent_can_be_cleared_back_to_the_description(client):
    client.put(
        "/api/topology/intent/Gi0-4",
        params={"deviceId": DEVICE},
        json={"expectedName": "TEST-AP-01"},
    )
    response = client.delete("/api/topology/intent/Gi0-4", params={"deviceId": DEVICE})
    assert response.status_code == 200
    assert response.json()["relationships"] == []


def test_intent_is_rejected_for_an_interface_the_switch_does_not_have(client):
    response = client.put(
        "/api/topology/intent/Gi9-99",
        params={"deviceId": DEVICE},
        json={"expectedName": "Nothing"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "command_not_allowed"


def test_intent_rejects_a_non_interface_string(client):
    response = client.put(
        "/api/topology/intent/..%2F..%2Fetc",
        params={"deviceId": DEVICE},
        json={"expectedName": "Nothing"},
    )
    assert response.status_code in {400, 404}


def test_intent_may_be_recorded_on_a_protected_interface(client):
    """Protection stops configuration writes, not documentation.

    Gi0/1 is the interface most likely to need its intent corrected, so
    read-only interface validation - not the write allowlist - is what applies.
    """
    response = client.put(
        "/api/topology/intent/Gi0-1",
        params={"deviceId": DEVICE},
        json={"expectedName": "Edge gateway"},
    )
    assert response.status_code == 200
    assert response.json()["relationships"][0]["interface"] == "Gi0/1"


def test_intent_rejects_control_characters(client):
    response = client.put(
        "/api/topology/intent/Gi0-4",
        params={"deviceId": DEVICE},
        json={"expectedName": "bad\x07name"},
    )
    assert response.status_code == 422


def test_intent_rejects_an_over_long_name(client):
    response = client.put(
        "/api/topology/intent/Gi0-4",
        params={"deviceId": DEVICE},
        json={"expectedName": "x" * 200},
    )
    assert response.status_code == 422


def test_intent_mutations_enforce_the_origin_check(client):
    for method, url in (
        ("put", "/api/topology/intent/Gi0-4"),
        ("delete", "/api/topology/intent/Gi0-4"),
    ):
        response = getattr(client, method)(
            url,
            params={"deviceId": DEVICE},
            headers={"origin": "https://evil.example"},
            **({"json": {"expectedName": "x"}} if method == "put" else {}),
        )
        assert response.status_code == 403, url
        assert response.json()["code"] == "origin_not_allowed"


def test_no_apply_or_write_endpoint_was_introduced(client):
    """Reconciliation adds no way to change the device."""
    paths = {route.path for route in app.routes}
    assert not any("apply" in path for path in paths)
    for path in paths:
        if path.startswith("/api/topology"):
            assert "write" not in path and "exec" not in path


def test_dashboard_exposes_reconciliation_separately_from_health(client):
    body = client.get("/api/switch/dashboard").json()
    assert "reconciliation" in body
    assert "health" in body["summary"]
    # Health must not gain a reconciliation field, or the two would be conflated.
    assert "reconciliation" not in body["summary"]
    assert "drift" not in str(body["summary"]["health"]).lower()


def test_recorded_intent_changes_the_next_reconciliation(client):
    before = client.get("/api/switch/dashboard").json()["reconciliation"]
    gi01_before = next(i for i in before["interfaces"] if i["interface"] == "Gi0/1")
    assert gi01_before["expected"]["source"] == "interface-description"

    client.put(
        "/api/topology/intent/Gi0-1",
        params={"deviceId": before["deviceId"]},
        json={"expectedName": "Edge gateway", "expectedDeviceType": "router"},
    )

    after = client.get("/api/switch/dashboard").json()["reconciliation"]
    gi01_after = next(i for i in after["interfaces"] if i["interface"] == "Gi0/1")
    assert gi01_after["expected"]["source"] == "user-intent"
    assert gi01_after["expected"]["objectLabel"] == "Edge gateway"
    # The switch still reports its own, now-stale, description.
    assert gi01_after["documentationStale"] is True
