"""Parse `show processes cpu`."""
from __future__ import annotations

import re

from ..models import CpuStatus


def parse_cpu(text: str) -> CpuStatus:
    m = re.search(
        r"CPU utilization for five seconds:\s*(\d+)%/\d+%;\s*one minute:\s*(\d+)%;\s*five minutes:\s*(\d+)%",
        text,
        re.IGNORECASE,
    )
    if not m:
        return CpuStatus(raw=text)
    return CpuStatus(
        cpu5Sec=float(m.group(1)),
        cpu1Min=float(m.group(2)),
        cpu5Min=float(m.group(3)),
        raw=text,
    )
