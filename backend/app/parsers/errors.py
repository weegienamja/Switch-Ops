"""Parse `show interfaces counters errors`."""
from __future__ import annotations

import re
from typing import Dict, List

from ..models import InterfaceErrorCounters


def _safe_int(s: str) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def parse_interface_errors(text: str) -> List[InterfaceErrorCounters]:
    rows: Dict[str, InterfaceErrorCounters] = {}
    lines = text.splitlines()
    block_header_first = ("Align-Err", "FCS-Err", "Xmit-Err", "Rcv-Err")
    block_header_second = ("Single-Col", "Multi-Col", "Late-Col", "Excess-Col")
    mode: str | None = None
    for ln in lines:
        if not ln.strip():
            continue
        if all(tok in ln for tok in block_header_first):
            mode = "first"
            continue
        if all(tok in ln for tok in block_header_second):
            mode = "second"
            continue
        if not re.match(r"^\s*Gi\d", ln):
            continue
        parts = ln.split()
        port = parts[0]
        row = rows.get(port) or InterfaceErrorCounters(port=port)
        if mode == "first":
            row.align_err = _safe_int(parts[1] if len(parts) > 1 else "0")
            row.fcs_err = _safe_int(parts[2] if len(parts) > 2 else "0")
            row.xmit_err = _safe_int(parts[3] if len(parts) > 3 else "0")
            row.rcv_err = _safe_int(parts[4] if len(parts) > 4 else "0")
            row.under_size = _safe_int(parts[5] if len(parts) > 5 else "0")
        elif mode == "second":
            row.single_col = _safe_int(parts[1] if len(parts) > 1 else "0")
            row.multi_col = _safe_int(parts[2] if len(parts) > 2 else "0")
            row.late_col = _safe_int(parts[3] if len(parts) > 3 else "0")
            row.excess_col = _safe_int(parts[4] if len(parts) > 4 else "0")
        row.total = (
            row.align_err + row.fcs_err + row.xmit_err + row.rcv_err
            + row.under_size + row.single_col + row.multi_col
            + row.late_col + row.excess_col
        )
        rows[port] = row
    return list(rows.values())
