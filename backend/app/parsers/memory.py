"""Parse `show memory statistics`."""
from __future__ import annotations

import re

from ..models import MemoryStatus


def _i(s: str) -> int | None:
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def parse_memory(text: str) -> MemoryStatus:
    proc = re.search(r"Processor\s+\S+\s+(\d+)\s+(\d+)\s+(\d+)", text)
    io = re.search(r"I/O\s+\S+\s+(\d+)\s+(\d+)\s+(\d+)", text)
    out = MemoryStatus(raw=text)
    if proc:
        out.processor_total = _i(proc.group(1))
        out.processor_used = _i(proc.group(2))
        out.processor_free = _i(proc.group(3))
    if io:
        out.io_total = _i(io.group(1))
        out.io_used = _i(io.group(2))
        out.io_free = _i(io.group(3))
    return out
