"""Parse `show logging`."""
from __future__ import annotations

import re
from typing import List

from ..models import LogEntry, LogsResponse


_SEVERITY = [
    (re.compile(r"%.*-(\d)-", re.IGNORECASE), None),  # captured below
]


def _severity_for(line: str) -> str:
    lower = line.lower()
    if any(t in lower for t in ("crit", "alert", "emerg", "fail", "%link-3-updown.*down")):
        if "down" in lower and "updown" in lower and "to up" not in lower:
            return "critical"
    m = re.search(r"%\S+-(\d)-", line)
    if m:
        level = int(m.group(1))
        if level <= 2:
            return "critical"
        if level == 3:
            return "warning"
        if level <= 5:
            return "notice"
    if "warn" in lower or "error" in lower:
        return "warning"
    return "info"


def parse_logs(text: str) -> LogsResponse:
    entries: List[LogEntry] = []
    started = False
    for ln in text.splitlines():
        if not started:
            if ln.startswith("Log Buffer"):
                started = True
            continue
        if not ln.strip():
            continue
        entries.append(LogEntry(line=ln, severity=_severity_for(ln)))
    if not entries:
        # Older outputs may not have the header; fall back to all lines that
        # look like timestamped log records.
        for ln in text.splitlines():
            if re.match(r"^\w{3} \d+ \d{2}:\d{2}:\d{2}", ln):
                entries.append(LogEntry(line=ln, severity=_severity_for(ln)))
    return LogsResponse(entries=entries, raw=text)
