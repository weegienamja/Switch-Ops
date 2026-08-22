"""Compose a high-level health summary for the dashboard."""
from __future__ import annotations

from typing import List

from .models import (
    CpuStatus,
    EnvironmentStatus,
    InterfaceStatus,
    InterfaceErrorsResponse,
    PoeResponse,
    SwitchSummary,
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
    poe: PoeResponse,
    errors: InterfaceErrorsResponse,
    pid: str | None = None,
    hardware_revision: str | None = None,
    ios_image: str | None = None,
    bootloader: str | None = None,
    interface_counts: str | None = None,
    telemetry_complete: bool = True,
) -> SwitchSummary:
    connected = [i.port for i in interfaces if i.status == "connected"]
    shutdown = [i.port for i in interfaces if i.status == "disabled"]
    error_ports = [c.port for c in errors.counters if c.total > 0]

    issues: List[str] = []
    if env.state == "RED":
        issues.append(f"Temperature is RED at {env.temperature_c}C")
    elif env.state == "YELLOW":
        issues.append(f"Temperature is YELLOW at {env.temperature_c}C")
    if cpu.cpu_5sec is not None and cpu.cpu_5sec >= 80:
        issues.append(f"CPU is high: {cpu.cpu_5sec:.0f}%")
    if errors.total_errors > 0:
        issues.append(f"{errors.total_errors} interface errors across {len(error_ports)} ports")

    if issues:
        summary = "Switch needs attention: " + "; ".join(issues) + "."
        healthy = False
    else:
        temp_str = f" Temperature is {env.temperature_c}C, below the {env.yellow_threshold_c}C yellow threshold." if env.temperature_c is not None else ""
        cpu_str = f" CPU is {cpu.cpu_5sec:.0f}%." if cpu.cpu_5sec is not None else ""
        poe_str = f" PoE budget is {poe.available_watts:.0f}W with {poe.used_watts:.0f}W used."
        summary = (
            f"Switch is healthy. {len(connected)} port(s) connected, interface errors are zero."
            + temp_str + cpu_str + poe_str
        ).strip()
        healthy = True

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
        healthy=healthy,
        telemetryComplete=telemetry_complete,
    )
