from __future__ import annotations

import os
from datetime import datetime, timezone

os.environ.setdefault("SWITCH_MOCK_MODE", "true")

from fastapi.testclient import TestClient

from app import main as main_mod
from app.main import app
from app.meraki_models import (
    MerakiConnectionTestResult,
    MerakiNetwork,
    MerakiOrganization,
    MerakiSetupStatus,
)
from app.unified_models import SourceHealth, UnifiedLabState


NOW = datetime(2026, 1, 15, 12, 20, tzinfo=timezone.utc)
client = TestClient(app)


class FakeUnifiedService:
    def __init__(self) -> None:
        self.saved_key: str | None = None
        self.selection = None
        self.health = SourceHealth(
            provider="meraki-dashboard",
            state="healthy",
            detail="Synthetic source is healthy.",
            checkedAt=NOW,
            lastSuccessAt=NOW,
            complete=True,
        )

    def setup_status(self):
        return MerakiSetupStatus(
            configured=self.saved_key is not None,
            keyringAvailable=True,
            storage="keyring" if self.saved_key else "none",
            selection=self.selection,
            sourceHealth=self.health,
        )

    def save_api_key(self, value: str):
        self.saved_key = value
        return self.setup_status()

    def clear_api_key(self):
        self.saved_key = None
        return self.setup_status()

    def test_connection(self):
        return MerakiConnectionTestResult(
            ok=True,
            summary="Synthetic connection succeeded.",
            checkedAt=NOW,
            organizationsVisible=1,
            sourceHealth=self.health,
        )

    def organizations(self):
        return [MerakiOrganization(id="ORG_SYNTHETIC", name="Synthetic org")]

    def networks(self, organization_id: str):
        return [
            MerakiNetwork(
                id="NET_SYNTHETIC",
                organizationId=organization_id,
                name="Synthetic lab",
                productTypes=["appliance", "wireless"],
            )
        ]

    def save_selection(self, selection):
        self.selection = selection
        return self.setup_status()

    def refresh_meraki(self):
        return self.health

    def state(self):
        return UnifiedLabState(
            generatedAt=NOW,
            sourceHealth=[self.health],
        )

    def decide_identity(self, _link_id: str, _decision: str):
        return self.state()


def test_meraki_setup_and_selection_api_never_echo_api_key(monkeypatch):
    service = FakeUnifiedService()
    monkeypatch.setattr(main_mod, "get_unified_lab_service", lambda: service)
    secret = "synthetic-api-key-that-must-not-return"

    saved = client.post("/api/meraki/setup/credentials", json={"apiKey": secret})
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert secret not in saved.text

    selection = client.put(
        "/api/meraki/selection",
        json={
            "organizationId": "ORG_SYNTHETIC",
            "organizationName": "Synthetic org",
            "networkId": "NET_SYNTHETIC",
            "networkName": "Synthetic lab",
        },
    )
    assert selection.status_code == 200
    assert selection.json()["selection"]["networkId"] == "NET_SYNTHETIC"


def test_meraki_discovery_routes_are_scoped_read_only_operations(monkeypatch):
    service = FakeUnifiedService()
    service.saved_key = "stored-outside-response"
    monkeypatch.setattr(main_mod, "get_unified_lab_service", lambda: service)

    organizations = client.get("/api/meraki/organizations")
    networks = client.get(
        "/api/meraki/networks", params={"organizationId": "ORG_SYNTHETIC"}
    )
    tested = client.post("/api/meraki/setup/test")
    refreshed = client.post("/api/meraki/refresh")

    assert organizations.json() == [{"id": "ORG_SYNTHETIC", "name": "Synthetic org"}]
    assert networks.json()[0]["organizationId"] == "ORG_SYNTHETIC"
    assert tested.json()["ok"] is True
    assert refreshed.json()["accepted"] is True
    assert client.get("/api/meraki/proxy").status_code == 404
    assert client.post("/api/meraki/organizations").status_code == 405


def test_unified_state_endpoint_is_normalized_envelope(monkeypatch):
    service = FakeUnifiedService()
    monkeypatch.setattr(main_mod, "get_unified_lab_service", lambda: service)

    response = client.get("/api/unified-lab/state")

    assert response.status_code == 200
    body = response.json()
    assert body["providerEntities"] == []
    assert body["claims"] == []
    assert body["sourceHealth"][0]["provider"] == "meraki-dashboard"


def test_meraki_validation_does_not_echo_rejected_secret(monkeypatch):
    service = FakeUnifiedService()
    monkeypatch.setattr(main_mod, "get_unified_lab_service", lambda: service)
    rejected = "x" * 513

    response = client.post(
        "/api/meraki/setup/credentials",
        json={"apiKey": rejected},
    )

    assert response.status_code == 422
    assert rejected not in response.text
    assert response.json()["code"] == "invalid_request"
