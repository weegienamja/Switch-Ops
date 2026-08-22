"""Parse `show env all`."""
from __future__ import annotations

import re

from ..models import EnvironmentStatus


def parse_environment(text: str) -> EnvironmentStatus:
    temp = None
    state = "UNKNOWN"
    yellow = None
    red = None
    power = "unknown"
    m = re.search(r"Temperature Value:\s*(\d+)", text, re.IGNORECASE)
    if m:
        temp = int(m.group(1))
    m = re.search(r"Temperature State:\s*(\w+)", text, re.IGNORECASE)
    if m:
        state = m.group(1).upper()
    m = re.search(r"Yellow Threshold\s*:\s*(\d+)", text, re.IGNORECASE)
    if m:
        yellow = int(m.group(1))
    m = re.search(r"Red Threshold\s*:\s*(\d+)", text, re.IGNORECASE)
    if m:
        red = int(m.group(1))
    if re.search(r"POWER is OK", text, re.IGNORECASE):
        power = "ok"
    elif re.search(r"POWER", text, re.IGNORECASE):
        power = "warning"
    return EnvironmentStatus(
        temperatureC=temp,
        state=state,
        yellowThresholdC=yellow,
        redThresholdC=red,
        powerStatus=power,
        raw=text,
    )
