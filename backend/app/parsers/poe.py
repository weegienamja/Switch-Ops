"""Parse `show power inline`."""
from __future__ import annotations

import re
from typing import List, Tuple

from ..models import PoePort, PoeResponse


def _safe_float(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def parse_poe(text: str) -> PoeResponse:
    available = used = remaining = 0.0
    m = re.search(
        r"Available:\s*([\d.]+)\(w\)\s+Used:\s*([\d.]+)\(w\)\s+Remaining:\s*([\d.]+)\(w\)",
        text,
        re.IGNORECASE,
    )
    if m:
        available = _safe_float(m.group(1))
        used = _safe_float(m.group(2))
        remaining = _safe_float(m.group(3))

    ports: List[PoePort] = []
    for ln in text.splitlines():
        if not re.match(r"^\s*Gi\d", ln):
            continue
        parts = ln.split()
        # Gi0/1  auto  off  0.0  n/a  n/a  30.0
        if len(parts) < 7:
            continue
        port_name, admin, oper = parts[0], parts[1], parts[2]
        power = _safe_float(parts[3])
        # device may contain spaces; pull class & max from the right.
        try:
            max_w = _safe_float(parts[-1])
            poe_class = parts[-2]
            device = " ".join(parts[4:-2]) or "n/a"
        except Exception:
            max_w = 30.0
            poe_class = "n/a"
            device = "n/a"
        ports.append(
            PoePort.model_validate(
                {
                    "interface": port_name,
                    "admin": admin,
                    "oper": oper,
                    "powerWatts": power,
                    "device": device,
                    "class": poe_class,
                    "maxWatts": max_w,
                }
            )
        )
    return PoeResponse(
        availableWatts=available,
        usedWatts=used,
        remainingWatts=remaining,
        ports=ports,
    )
