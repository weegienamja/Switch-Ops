from pathlib import Path

from app.parsers.environment import parse_environment

SAMPLES = Path(__file__).resolve().parents[1] / "sample_outputs"


def test_parse_environment_green():
    env = parse_environment(
        (SAMPLES / "show_env_all.txt").read_text(encoding="utf-8")
    )
    assert env.temperature_c == 49
    assert env.state == "GREEN"
    assert env.yellow_threshold_c == 80
    assert env.red_threshold_c == 90
    assert env.power_status == "ok"
