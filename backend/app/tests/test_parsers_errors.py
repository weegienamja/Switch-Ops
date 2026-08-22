from pathlib import Path

from app.parsers.errors import parse_interface_errors

SAMPLES = Path(__file__).resolve().parents[1] / "sample_outputs"


def test_parse_interface_errors_all_zero():
    counters = parse_interface_errors(
        (SAMPLES / "show_interfaces_counters_errors.txt").read_text(encoding="utf-8")
    )
    assert len(counters) >= 8
    assert all(c.total == 0 for c in counters)
