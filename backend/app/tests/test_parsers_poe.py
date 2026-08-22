from pathlib import Path

from app.parsers.poe import parse_poe

SAMPLES = Path(__file__).resolve().parents[1] / "sample_outputs"


def test_parse_poe_budget():
    poe = parse_poe((SAMPLES / "show_power_inline.txt").read_text(encoding="utf-8"))
    assert poe.available_watts == 124.0
    assert poe.used_watts == 0.0
    assert poe.remaining_watts == 124.0
    assert any(p.interface == "Gi0/4" for p in poe.ports)
