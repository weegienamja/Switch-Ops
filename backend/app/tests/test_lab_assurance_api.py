from __future__ import annotations

import os

os.environ.setdefault("SWITCH_MOCK_MODE", "true")

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_lab_assurance_api_has_no_generic_command_or_score():
    response = client.post("/api/lab-assurance/refresh")
    assert response.status_code == 200
    state = response.json()["state"]
    assert state["collectionState"] == "CURRENT"
    assert state["summary"]["observedDevices"] == 1
    assert "score" not in state["summary"]
    assert state["limitations"]
    assert client.post("/api/lab-assurance/command", json={"command": "show run"}).status_code == 404


def test_lab_assurance_machine_readable_views_are_stable_lists():
    assert isinstance(client.get("/api/lab-assurance/capabilities").json(), list)
    assert isinstance(client.get("/api/lab-assurance/findings").json(), list)
    assert isinstance(client.get("/api/lab-assurance/failures").json(), list)
    assert isinstance(client.get("/api/lab-assurance/paths").json(), list)
    assert isinstance(client.get("/api/lab-assurance/performance").json(), list)


def test_probe_rejects_shell_like_target_without_echoing_execution():
    response = client.post(
        "/api/lab-assurance/performance/probe",
        json={"target": "example.test & whoami", "label": "synthetic", "count": 1},
    )
    assert response.status_code == 422
