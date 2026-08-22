from pathlib import Path

import pytest

from backend.app.command_registry import READ_ONLY_COMMANDS
from backend.app.errors import CommandNotAllowedError
from backend.app.guide import list_guide_operations, run_guide_operation
from backend.app.switch_client import MockSwitchClient


SAMPLES = Path(__file__).resolve().parents[1] / "sample_outputs"


def test_every_guide_command_is_a_literal_registry_value():
    allowed = set(READ_ONLY_COMMANDS.values())
    operations = list_guide_operations()
    assert len(operations) >= 10
    assert all(operation.safety == "READ ONLY" for operation in operations)
    assert all(command in allowed for operation in operations for command in operation.commands)


def test_connected_ports_returns_parsed_result_and_explanation():
    result = run_guide_operation(
        MockSwitchClient(SAMPLES), operation_id="connected_ports"
    )
    assert len(result.result["interfaces"]) == 10
    assert "2 port(s) have a link" in result.explanation
    assert result.operation.commands == ["show interfaces status"]


def test_port_guide_parameter_is_validated_and_never_becomes_cli():
    result = run_guide_operation(
        MockSwitchClient(SAMPLES),
        operation_id="port_state",
        interface="Gi0/4",
    )
    assert result.result["interface"]["port"] == "Gi0/4"
    assert result.operation.commands == ["show interfaces status", "show power inline"]

    with pytest.raises(CommandNotAllowedError):
        run_guide_operation(
            MockSwitchClient(SAMPLES),
            operation_id="port_state",
            interface="Gi0/4; configure terminal",
        )


def test_unknown_operation_is_rejected_before_any_command_runs():
    class RecordingClient:
        calls: list[str] = []

        def run(self, symbol: str) -> str:
            self.calls.append(symbol)
            return ""

        def close(self) -> None:
            pass

    client = RecordingClient()
    with pytest.raises(CommandNotAllowedError):
        run_guide_operation(client, operation_id="send_anything")
    assert client.calls == []


def test_empty_cdp_result_is_valid_and_not_fabricated():
    result = run_guide_operation(MockSwitchClient(SAMPLES), operation_id="neighbors")
    assert result.result == {"neighbors": []}
    assert "0 neighbour(s)" in result.explanation
