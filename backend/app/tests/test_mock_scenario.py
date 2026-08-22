from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.parsers.interfaces import parse_interface_status
from backend.app.parsers.mac_table import parse_mac_table
from backend.app.parsers.poe import parse_poe
from backend.app.switch_client import MockSwitchClient, set_mock_scenario


SAMPLES = Path(__file__).resolve().parents[1] / "sample_outputs"


def test_mock_attached_scenario_changes_only_selected_read_outputs():
    try:
        set_mock_scenario("baseline")
        baseline = MockSwitchClient(SAMPLES)
        before = next(
            interface
            for interface in parse_interface_status(baseline.run("show_interfaces_status"))
            if interface.port == "Gi0/4"
        )
        assert before.status == "notconnect"

        set_mock_scenario("ap_attached")
        attached = MockSwitchClient(SAMPLES)
        after = next(
            interface
            for interface in parse_interface_status(attached.run("show_interfaces_status"))
            if interface.port == "Gi0/4"
        )
        assert after.status == "connected"
        assert parse_poe(attached.run("show_power_inline")).used_watts == 18.4
        assert any(
            entry.port == "Gi0/4"
            for entry in parse_mac_table(attached.run("show_mac_address_table"))
        )
        assert attached.run("show_version") == baseline.run("show_version")
    finally:
        set_mock_scenario("baseline")


def test_mock_scenario_endpoint_is_enum_bounded():
    client = TestClient(app)
    try:
        response = client.post(
            "/api/mock/scenario",
            json={"scenario": "ap_attached"},
            headers={"origin": "http://localhost:3000"},
        )
        assert response.status_code == 200
        assert response.json() == {"scenario": "ap_attached", "mockMode": True}

        rejected = client.post(
            "/api/mock/scenario",
            json={"scenario": "configure_terminal"},
            headers={"origin": "http://localhost:3000"},
        )
        assert rejected.status_code == 422
        assert "configure_terminal" not in rejected.text
    finally:
        set_mock_scenario("baseline")
