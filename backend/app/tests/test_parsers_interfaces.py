from pathlib import Path

from app.parsers.interfaces import parse_interface_status

SAMPLES = Path(__file__).resolve().parents[1] / "sample_outputs"


def test_parse_interface_status_has_ten_ports():
    text = (SAMPLES / "show_interfaces_status.txt").read_text(encoding="utf-8")
    interfaces = parse_interface_status(text)
    ports = [i.port for i in interfaces]
    assert ports[:2] == ["Gi0/1", "Gi0/2"]
    assert len(interfaces) == 10
    by_port = {i.port: i for i in interfaces}
    assert by_port["Gi0/1"].policy_state == "UNMANAGED"
    assert by_port["Gi0/2"].policy_state == "UNMANAGED"
    assert by_port["Gi0/6"].protected is False
    assert by_port["Gi0/1"].status == "connected"
    assert by_port["Gi0/1"].duplex == "a-full"
    assert by_port["Gi0/1"].speed == "a-1000"
    assert by_port["Gi0/6"].status == "disabled"
