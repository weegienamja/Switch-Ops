"""Deterministic health evaluation based on current conditions and deltas."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Iterable, List

from .models import (
    CpuStatus,
    EnvironmentStatus,
    HealthAssessment,
    HealthReason,
    InterfaceDelta,
    InterfaceStatus,
    InterfaceErrorsResponse,
    MemoryStatus,
    PoeResponse,
    SwitchSummary,
)


@dataclass(frozen=True)
class HealthThresholds:
    """Central, inspectable thresholds used by the health engine."""

    cpu_notice_pct: float = 65.0
    cpu_attention_pct: float = 80.0
    cpu_critical_pct: float = 95.0
    memory_notice_used_pct: float = 85.0
    memory_attention_used_pct: float = 90.0
    memory_critical_used_pct: float = 97.0
    error_notice_delta: int = 1
    error_attention_delta: int = 10
    error_critical_delta: int = 100


DEFAULT_THRESHOLDS = HealthThresholds()
_RANK = {"HEALTHY": 0, "NOTICE": 1, "ATTENTION": 2, "CRITICAL": 3}


def _memory_used_pct(memory: MemoryStatus) -> float | None:
    if (
        memory.processor_total is None
        or memory.processor_used is None
        or memory.processor_total <= 0
    ):
        return None
    return (memory.processor_used / memory.processor_total) * 100


def _speed_mbps(value: str | None) -> int | None:
    if not value:
        return None
    normalized = value.lower().removeprefix("a-").strip()
    if normalized in {"auto", "--", "n/a"}:
        return None
    match = re.search(r"(\d+)", normalized)
    return int(match.group(1)) if match else None


def _poe_active(value: str | None) -> bool:
    return bool(value and value.strip().lower() not in {"off", "n/a", "not-supported", "unknown"})


def evaluate_health(
    *,
    interfaces: Iterable[InterfaceStatus],
    environment: EnvironmentStatus,
    cpu: CpuStatus,
    memory: MemoryStatus,
    deltas: Iterable[InterfaceDelta],
    telemetry_complete: bool,
    evaluated_at: datetime | None = None,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
) -> HealthAssessment:
    """Evaluate only active conditions and changes since the prior sample.

    Cumulative counters are never treated as current faults by themselves.
    A first observation or an unchanged non-zero counter therefore produces no
    error reason; only a positive delta can affect current health.
    """
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    interface_by_port = {interface.port: interface for interface in interfaces}
    reasons: list[HealthReason] = []
    delta_list = list(deltas)

    def add(code: str, severity: str, title: str, detail: str, interface: str | None = None) -> None:
        reasons.append(HealthReason(
            code=code,
            severity=severity,
            title=title,
            detail=detail,
            interface=interface,
        ))

    if environment.state == "RED":
        add(
            "temperature_critical",
            "CRITICAL",
            "Temperature is in the red range",
            f"The observed temperature is {environment.temperature_c}C.",
        )
    elif environment.state == "YELLOW":
        add(
            "temperature_attention",
            "ATTENTION",
            "Temperature is in the yellow range",
            f"The observed temperature is {environment.temperature_c}C.",
        )

    if cpu.cpu_5sec is not None:
        if cpu.cpu_5sec >= thresholds.cpu_critical_pct:
            add("cpu_critical", "CRITICAL", "CPU is critically busy", f"Five-second CPU is {cpu.cpu_5sec:.0f}%.")
        elif cpu.cpu_5sec >= thresholds.cpu_attention_pct:
            add("cpu_attention", "ATTENTION", "CPU is heavily loaded", f"Five-second CPU is {cpu.cpu_5sec:.0f}%.")
        elif cpu.cpu_5sec >= thresholds.cpu_notice_pct:
            add("cpu_notice", "NOTICE", "CPU load is elevated", f"Five-second CPU is {cpu.cpu_5sec:.0f}%.")

    memory_used = _memory_used_pct(memory)
    if memory_used is not None:
        if memory_used >= thresholds.memory_critical_used_pct:
            add("memory_critical", "CRITICAL", "Memory is critically full", f"Processor memory is {memory_used:.1f}% used.")
        elif memory_used >= thresholds.memory_attention_used_pct:
            add("memory_attention", "ATTENTION", "Memory use is high", f"Processor memory is {memory_used:.1f}% used.")
        elif memory_used >= thresholds.memory_notice_used_pct:
            add("memory_notice", "NOTICE", "Memory use is elevated", f"Processor memory is {memory_used:.1f}% used.")

    for delta in delta_list:
        current = interface_by_port.get(delta.port)
        if delta.counter_state in {"increased", "wrapped"} and (delta.error_delta or 0) > 0:
            amount = int(delta.error_delta or 0)
            if amount >= thresholds.error_critical_delta:
                severity = "CRITICAL"
            elif amount >= thresholds.error_attention_delta:
                severity = "ATTENTION"
            else:
                severity = "NOTICE"
            add(
                "interface_errors_increased",
                severity,
                f"{delta.port} errors increased by {amount}",
                f"The cumulative error counter rose by {amount} since the previous observation.",
                delta.port,
            )
        elif delta.counter_state == "reset":
            add(
                "interface_counter_reset",
                "NOTICE",
                f"{delta.port} error counter reset observed",
                "The counter decreased. A restart or external clear can cause this; SwitchOps cannot determine which.",
                delta.port,
            )

        if delta.status_before == "connected" and delta.status_after != "connected":
            description = current.name.lower() if current else ""
            is_uplink = any(word in description for word in ("uplink", "router", "gateway"))
            add(
                "uplink_down" if is_uplink else "interface_link_down",
                "CRITICAL" if is_uplink else "NOTICE",
                f"{delta.port} link is down",
                "This interface was connected at the previous observation and no longer has a link.",
                delta.port,
            )

        previous_speed = _speed_mbps(delta.speed_before)
        current_speed = _speed_mbps(delta.speed_after)
        if (
            delta.status_before == "connected"
            and delta.status_after == "connected"
            and previous_speed is not None
            and current_speed is not None
            and current_speed < previous_speed
        ):
            add(
                "link_speed_reduced",
                "NOTICE",
                f"{delta.port} negotiated a lower speed",
                f"Link speed changed from {previous_speed} Mbps to {current_speed} Mbps.",
                delta.port,
            )
        if (
            delta.status_before == "connected"
            and delta.status_after == "connected"
            and "half" in delta.duplex_after.lower()
            and delta.duplex_before != delta.duplex_after
        ):
            add(
                "half_duplex_negotiated",
                "ATTENTION",
                f"{delta.port} negotiated half duplex",
                f"Duplex changed from {delta.duplex_before or 'unknown'} to {delta.duplex_after}.",
                delta.port,
            )
        if delta.poe_before is not None and _poe_active(delta.poe_before) and not _poe_active(delta.poe_after):
            add(
                "poe_stopped",
                "NOTICE",
                f"PoE stopped on {delta.port}",
                "The switch was supplying PoE at the previous observation and is not supplying it now.",
                delta.port,
            )

    if not telemetry_complete:
        add(
            "partial_telemetry",
            "NOTICE",
            "Some telemetry is unavailable",
            "Health was evaluated from the sections that were collected successfully.",
        )

    if reasons:
        state = max((reason.severity for reason in reasons), key=lambda item: _RANK[item])
    else:
        state = "HEALTHY"
        reasons.append(HealthReason(
            code="no_active_problems",
            severity="HEALTHY",
            title="No active problems detected",
            detail="Current conditions are within thresholds and no adverse change was observed.",
        ))
    return HealthAssessment(
        state=state,
        reasons=reasons,
        evaluatedAt=evaluated_at,
        basedOnHistory=any(delta.status_before is not None for delta in delta_list),
    )


def build_summary(
    *,
    hostname: str,
    model: str,
    management_ip: str,
    gateway: str,
    ios_version: str,
    serial: str | None,
    uptime: str | None,
    interfaces: List[InterfaceStatus],
    env: EnvironmentStatus,
    cpu: CpuStatus,
    memory: MemoryStatus,
    poe: PoeResponse,
    errors: InterfaceErrorsResponse,
    deltas: Iterable[InterfaceDelta],
    evaluated_at: datetime,
    pid: str | None = None,
    hardware_revision: str | None = None,
    ios_image: str | None = None,
    bootloader: str | None = None,
    interface_counts: str | None = None,
    telemetry_complete: bool = True,
) -> SwitchSummary:
    connected = [interface.port for interface in interfaces if interface.status == "connected"]
    shutdown = [interface.port for interface in interfaces if interface.status == "disabled"]
    delta_list = list(deltas)
    error_ports = [
        delta.port
        for delta in delta_list
        if delta.counter_state in {"increased", "wrapped"} and (delta.error_delta or 0) > 0
    ]
    health = evaluate_health(
        interfaces=interfaces,
        environment=env,
        cpu=cpu,
        memory=memory,
        deltas=delta_list,
        telemetry_complete=telemetry_complete,
        evaluated_at=evaluated_at,
    )

    if health.state == "HEALTHY":
        history_note = " No previous sample is available yet." if not health.based_on_history else ""
        summary = (
            f"Switch is healthy. {len(connected)} port(s) connected; no active error increases detected."
            f"{history_note}"
        )
    else:
        active = [reason.title for reason in health.reasons[:3]]
        summary = f"{health.state.title()}: " + "; ".join(active) + "."

    return SwitchSummary(
        hostname=hostname,
        model=model,
        managementIp=management_ip,
        gateway=gateway,
        iosVersion=ios_version,
        serial=serial,
        pid=pid,
        hardwareRevision=hardware_revision,
        iosImage=ios_image,
        bootloader=bootloader,
        interfaceCounts=interface_counts,
        uptime=uptime,
        temperatureC=env.temperature_c,
        temperatureState=env.state,
        cpu5Sec=cpu.cpu_5sec,
        poeAvailableW=poe.available_watts,
        poeUsedW=poe.used_watts,
        connectedPorts=connected,
        shutdownPorts=shutdown,
        errorPorts=error_ports,
        summary=summary,
        healthy=health.state == "HEALTHY",
        health=health,
        telemetryComplete=telemetry_complete,
    )
