"""Strictly bounded, local-host service probes for Lab Assurance."""
from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import subprocess
from datetime import datetime, timezone

from .models import PerformanceObservation


_HOSTNAME = re.compile(r"(?=^.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def validate_probe_target(value: str) -> str:
    target = value.strip()
    try:
        ipaddress.ip_address(target)
        return target
    except ValueError:
        pass
    if not _HOSTNAME.fullmatch(target) or target.startswith("-"):
        raise ValueError("Probe target must be an IP address or a valid DNS hostname.")
    return target


def _target_token(target: str) -> str:
    return hashlib.sha256(target.casefold().encode("utf-8")).hexdigest()[:16]


def _route_signature(text: str) -> str | None:
    hops: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s*\d+\s+.*?((?:\d{1,3}\.){3}\d{1,3})\s*$", line)
        if match:
            hops.append(match.group(1))
    if not hops:
        return None
    return hashlib.sha256("|".join(hops).encode("utf-8")).hexdigest()


def run_bounded_probe(
    target: str,
    *,
    label: str,
    count: int = 4,
    previous_route_signature: str | None = None,
) -> tuple[PerformanceObservation, str | None]:
    target = validate_probe_target(target)
    count = max(1, min(5, count))
    is_windows = os.name == "nt"
    ping_args = (
        ["ping.exe", "-n", str(count), "-w", "1500", target]
        if is_windows
        else ["ping", "-c", str(count), "-W", "2", target]
    )
    try:
        completed = subprocess.run(
            ping_args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=count * 2 + 4,
            shell=False,
            check=False,
        )
        output = f"{completed.stdout}\n{completed.stderr}"
    except (OSError, subprocess.TimeoutExpired):
        output = ""

    loss = re.search(r"\((\d+(?:\.\d+)?)%\s*(?:loss|packet loss)\)", output, re.IGNORECASE)
    received = re.search(r"Received\s*=\s*(\d+)", output, re.IGNORECASE)
    if received is None:
        unix = re.search(r"(\d+) packets transmitted,\s*(\d+) received", output, re.IGNORECASE)
        received_count = int(unix.group(2)) if unix else 0
    else:
        received_count = int(received.group(1))
    loss_percent = float(loss.group(1)) if loss else ((count - received_count) / count * 100 if output else None)
    times = [float(value) for value in re.findall(r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms", output, re.IGNORECASE)]
    average = sum(times) / len(times) if times else None
    jitter = (max(times) - min(times)) if len(times) > 1 else (0.0 if times else None)

    route_output = ""
    try:
        route_args = (
            ["tracert.exe", "-d", "-h", "12", "-w", "750", target]
            if is_windows
            else ["traceroute", "-n", "-m", "12", "-w", "1", target]
        )
        route = subprocess.run(
            route_args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=14,
            shell=False,
            check=False,
        )
        route_output = route.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass
    signature = _route_signature(route_output)
    route_changed = (
        signature != previous_route_signature
        if signature is not None and previous_route_signature is not None
        else None
    )
    if not output:
        state = "INSUFFICIENT_EVIDENCE"
        detail = "The bounded probe could not execute or return parseable evidence."
    elif received_count == 0:
        state = "UNREACHABLE"
        detail = "No replies were received during the bounded probe."
    elif (loss_percent or 0) > 0 or (average is not None and average >= 100) or route_changed:
        state = "DEGRADED"
        detail = "Service replies were observed, but loss, latency or route change evidence is degraded."
    else:
        state = "HEALTHY"
        detail = "Every bounded probe reply succeeded without a detected route change."
    observation = PerformanceObservation(
        id=f"probe-{_target_token(target)}-{int(datetime.now(timezone.utc).timestamp())}",
        targetLabel=label.strip(),
        targetToken=_target_token(target),
        state=state,
        observedAt=datetime.now(timezone.utc),
        transmitted=count,
        received=received_count,
        lossPercent=loss_percent,
        latencyAvgMs=round(average, 2) if average is not None else None,
        jitterMs=round(jitter, 2) if jitter is not None else None,
        routeChanged=route_changed,
        detail=detail,
    )
    return observation, signature
