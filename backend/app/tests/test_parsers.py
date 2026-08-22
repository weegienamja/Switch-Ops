from pathlib import Path

from app.parsers.interfaces import parse_interface_status
from app.parsers.errors import parse_interface_errors
from app.parsers.poe import parse_poe
from app.parsers.environment import parse_environment
from app.parsers.cpu import parse_cpu
from app.parsers.memory import parse_memory
from app.parsers.mac_table import parse_mac_table
from app.parsers.logs import parse_logs
from app.parsers.version import parse_version
from app.parsers.inventory import parse_inventory
from app.parsers.config_parser import parse_running_config, redact_config


SAMPLES = Path(__file__).resolve().parents[1] / "sample_outputs"


def _load(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


def test_parse_interface_status_has_ten_ports():
    interfaces = parse_interface_status(_load("show_interfaces_status.txt"))
    ports = [i.port for i in interfaces]
    assert ports[:2] == ["Gi0/1", "Gi0/2"]
    assert len(interfaces) == 10
    # Gi0/1 and Gi0/2 are protected
    by_port = {i.port: i for i in interfaces}
    assert by_port["Gi0/1"].protected is True
    assert by_port["Gi0/2"].protected is True
    assert by_port["Gi0/6"].protected is False
    assert by_port["Gi0/1"].status == "connected"
    assert by_port["Gi0/1"].duplex == "a-full"
    assert by_port["Gi0/1"].speed == "a-1000"
    assert by_port["Gi0/6"].status == "disabled"


def test_parse_interface_errors_all_zero():
    counters = parse_interface_errors(_load("show_interfaces_counters_errors.txt"))
    assert len(counters) >= 8
    assert all(c.total == 0 for c in counters)


def test_parse_poe_budget():
    poe = parse_poe(_load("show_power_inline.txt"))
    assert poe.available_watts == 124.0
    assert poe.used_watts == 0.0
    assert poe.remaining_watts == 124.0
    assert any(p.interface == "Gi0/4" for p in poe.ports)


def test_parse_environment_green():
    env = parse_environment(_load("show_env_all.txt"))
    assert env.temperature_c == 49
    assert env.state == "GREEN"
    assert env.yellow_threshold_c == 80
    assert env.red_threshold_c == 90
    assert env.power_status == "ok"


def test_parse_cpu():
    cpu = parse_cpu(_load("show_processes_cpu.txt"))
    assert cpu.cpu_5sec == 6.0
    assert cpu.cpu_1min == 7.0
    assert cpu.cpu_5min == 6.0


def test_parse_memory():
    mem = parse_memory(_load("show_memory_statistics.txt"))
    assert mem.processor_total == 97574088


def test_parse_mac_table():
    entries = parse_mac_table(_load("show_mac_address_table.txt"))
    assert any(e.port == "Gi0/1" for e in entries)


def test_parse_logs():
    logs = parse_logs(_load("show_logging.txt"))
    assert any("LINK-3-UPDOWN" in e.line for e in logs.entries)


def test_parse_version():
    v = parse_version(_load("show_version.txt"))
    assert v.get("hostname") == "SWITCHOPS-TEST-SW1"
    assert v.get("model") == "WS-C3560CG-8PC-S"
    assert v.get("ios_version") == "12.2(55)EX2"
    assert v.get("serial") == "FOC0000T001"
    assert v.get("ios_image") == "flash:c3560c405ex-universalk9-mz.122-55.EX2.bin"
    assert v.get("hardware_revision") == "V03"
    assert v.get("interface_counts") == "1 Virtual Ethernet, 10 Gigabit Ethernet"


def test_parse_inventory():
    inventory = parse_inventory(_load("show_inventory.txt"))
    assert inventory["pid"] == "WS-C3560CG-8PC-S"
    assert inventory["vid"] == "V03"


def test_parse_running_config_redacts():
    text = _load("show_running_config.txt")
    cfg = parse_running_config(text)
    assert cfg["hostname"] == "SWITCHOPS-TEST-SW1"
    assert cfg["management_ip"] == "192.0.2.190"
    assert cfg["gateway"] == "192.0.2.19"
    assert cfg["http_disabled"] is True
    assert cfg["https_disabled"] is True
    assert "GigabitEthernet0/6" in cfg["shutdown_interfaces"]
    redacted = redact_config(text)
    assert "<redacted>" in redacted
    assert "__REPLACE_WITH_LOCAL_SECRET__" not in redacted


def test_config_redaction_covers_common_ios_secret_forms():
    text = "\n".join(
        [
            "enable password 7 value-one",
            "username admin privilege 15 password 0 value-two",
            " password 7 value-three",
            "snmp-server community value-four RO",
            "tacacs-server key 7 value-five",
            "crypto isakmp key value-six address 192.0.2.10.1",
        ]
    )
    redacted = redact_config(text)
    for secret in ("value-one", "value-two", "value-three", "value-four", "value-five", "value-six"):
        assert secret not in redacted
    assert redacted.count("<redacted>") == 6
