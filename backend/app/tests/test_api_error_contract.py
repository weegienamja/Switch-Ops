from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from backend.app import main
from backend.app.errors import DeviceSessionLostError, SwitchUnreachableError


client = TestClient(main.app, raise_server_exceptions=False)


def test_deep_dashboard_collection_does_not_swallow_a_transport_failure():
    class BrokenTransport:
        def run(self, _symbol: str) -> str:
            raise DeviceSessionLostError("The active Catalyst session was lost.")

    with pytest.raises(DeviceSessionLostError):
        main._collect_dashboard(BrokenTransport())  # type: ignore[arg-type]


def test_running_backend_returns_structured_device_unreachable(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise SwitchUnreachableError(
            "The configured Catalyst could not be reached.",
            detail="192.0.2.10 synthetic-password raw socket detail",
            safe_detail="No device-side cause was assumed.",
        )

    monkeypatch.setattr(main, "on_device", unavailable)
    response = client.get("/api/switch/dashboard")

    assert response.status_code == 502
    assert response.json() == {
        "code": "switch_unreachable",
        "message": "The configured Catalyst could not be reached.",
        "detail": "No device-side cause was assumed.",
    }
    assert "192.0.2.10" not in response.text
    assert "synthetic-password" not in response.text


def test_unhandled_http_500_from_running_backend_has_safe_structured_error(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("synthetic-password C:/private/trace")

    monkeypatch.setattr(main, "on_device", broken)
    response = client.get("/api/switch/dashboard")

    assert response.status_code == 500
    assert response.json() == {
        "code": "backend_internal_error",
        "message": "The SwitchOps backend could not complete the request.",
        "detail": None,
    }
    assert "synthetic-password" not in response.text
    assert "private" not in response.text
