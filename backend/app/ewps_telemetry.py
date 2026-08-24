"""Privacy-minimized, fixed-target, source-bound EWPS telemetry probes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
import os
import re
import statistics
import subprocess

from .discovery import discover_local_adapters
from .ewps_models import CandidatePath, RawMetrics, TopologyEvidenceKey
from .live_state import get_live_state


# The endpoint is fixed in code: the API cannot turn EWPS into a generic probe
# or shell surface. Only ICMP metadata is observed; no DNS name or URL is used.
FIXED_PROBE_TARGET = "1.1.1.1"
FIXED_PROBE_TARGET_TOKEN = hashlib.sha256(FIXED_PROBE_TARGET.encode("ascii")).hexdigest()[:16]


@dataclass(frozen=True)
class InternalCandidate:
    public: CandidatePath
    source_ip: str


@dataclass(frozen=True)
class ProbeResult:
    path_id: str
    observed_at: datetime
    raw: RawMetrics
    collection_started_at: datetime | None = None
    observation_validated_at: datetime | None = None
    collection_duration_ms: float | None = None
    probe_outcomes: tuple[bool, ...] = ()
    failure_reason: str | None = None


def _path_token(adapter_name: str, source_ip: str) -> str:
    digest = hashlib.sha256(f"{adapter_name.casefold()}|{source_ip}".encode("utf-8")).hexdigest()
    return f"path-{digest[:16]}"


def _derive_topology_evidence(source_ip: str) -> tuple[TopologyEvidenceKey, str]:
    """Map current SwitchOps evidence vocabulary to an explicit EWPS key."""
    topology = get_live_state().snapshot().get("topology") or {}
    devices = topology.get("devices") or []
    links = topology.get("links") or []
    matched = next(
        (
            item for item in devices
            if source_ip == item.get("ip") or source_ip in (item.get("ipAddresses") or [])
        ),
        None,
    )
    if matched:
        if matched.get("conflicts"):
            return "contradictory", "SwitchOps retains contradictory attachment evidence for this local path."
        related = [item for item in links if item.get("toDeviceId") == matched.get("id")]
        if related:
            strongest = related[0]
            identity_source = matched.get("identitySource")
            confidence = strongest.get("confidence") or matched.get("existenceConfidence")
            evidence_level = strongest.get("evidenceLevel")
            relationship = strongest.get("relationship")
            if identity_source == "local-host" and confidence == "confirmed":
                return (
                    "reciprocal_independent_direct",
                    "The host adapter and switch evidence independently confirm the direct local relationship.",
                )
            if evidence_level == "direct" or relationship == "direct-neighbour":
                return "one_sided_direct", "Current CDP/LLDP evidence directly observes one side of the relationship."
            if relationship in {"learned-behind", "gateway-path"}:
                return "strong_inference", "Current forwarding evidence strongly infers this relationship."
            if evidence_level in {"observed-on-port", "learned-behind"}:
                return "weak_inference", "Attachment is observed, but the complete path is not directly proven."
            if evidence_level == "expected":
                return "weak_inference", "Only expected or incomplete topology evidence supports this path."
    # The OS directly observes the source adapter, but no peer-side relationship
    # is claimed. This is precisely the existing one-sided evidence category.
    return (
        "one_sided_direct",
        "The active local adapter is directly observed; the upstream peer relationship is not independently proven.",
    )


def candidate_catalog() -> list[InternalCandidate]:
    adapters = sorted(discover_local_adapters(), key=lambda item: (item.name.casefold(), item.ip))
    candidates: list[InternalCandidate] = []
    for index, adapter in enumerate(adapters):
        evidence, detail = _derive_topology_evidence(adapter.ip)
        candidates.append(
            InternalCandidate(
                public=CandidatePath(
                    pathId=_path_token(adapter.name, adapter.ip),
                    displayLabel=f"Path {chr(65 + index)}",
                    adapterName=adapter.name,
                    topologyEvidence=evidence,
                    topologyDetail=detail,
                ),
                source_ip=adapter.ip,
            )
        )
    return candidates


def _interface_counters(adapter_name: str) -> tuple[int | None, int | None, int | None, int | None]:
    try:
        import psutil

        counters = psutil.net_io_counters(pernic=True).get(adapter_name)
        if counters is None:
            return None, None, None, None
        return (
            int(counters.packets_sent),
            int(counters.packets_recv),
            int(counters.errin + counters.errout),
            int(counters.dropin + counters.dropout),
        )
    except (ImportError, OSError, AttributeError):
        return None, None, None, None


def _parse_probe_output(output: str, transmitted: int) -> tuple[list[float], int, float | None]:
    times = [
        float(value)
        for value in re.findall(r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms", output, re.IGNORECASE)
    ]
    windows_received = re.search(r"Received\s*=\s*(\d+)", output, re.IGNORECASE)
    unix_received = re.search(r"(\d+) packets transmitted,\s*(\d+) (?:packets )?received", output, re.IGNORECASE)
    if windows_received:
        received = int(windows_received.group(1))
    elif unix_received:
        received = int(unix_received.group(2))
    else:
        received = len(times)
    received = min(transmitted, max(0, received))
    loss_match = re.search(r"(\d+(?:\.\d+)?)%\s*(?:loss|packet loss)", output, re.IGNORECASE)
    loss = float(loss_match.group(1)) if loss_match else (transmitted - received) / transmitted * 100.0
    return times, received, min(100.0, max(0.0, loss))


def _probe_outcomes(output: str, transmitted: int, received: int) -> tuple[bool, ...]:
    """Retain bounded per-probe success/failure outcomes without payload data."""
    windows: list[bool] = []
    for line in output.splitlines():
        lowered = line.casefold()
        if "reply from" in lowered and ("time=" in lowered or "time<" in lowered):
            windows.append(True)
        elif "request timed out" in lowered or "destination host unreachable" in lowered:
            windows.append(False)
    if windows:
        return tuple((windows + [False] * transmitted)[:transmitted])
    sequences = {
        int(value)
        for value in re.findall(r"icmp_seq[= ](\d+)", output, re.IGNORECASE)
    }
    if sequences:
        # iputils sequence numbers normally start at one.
        return tuple(index in sequences for index in range(1, transmitted + 1))
    return tuple([True] * received + [False] * max(0, transmitted - received))


def measure_candidate(candidate: InternalCandidate, count: int) -> ProbeResult:
    """Run one fixed, bounded ICMP sample through a selected source adapter."""
    count = max(1, min(5, int(count)))
    is_windows = os.name == "nt"
    args = (
        [
            "ping.exe",
            "-n",
            str(count),
            "-w",
            "900",
            "-S",
            candidate.source_ip,
            FIXED_PROBE_TARGET,
        ]
        if is_windows
        else [
            "ping",
            "-c",
            str(count),
            "-W",
            "1",
            "-I",
            candidate.source_ip,
            FIXED_PROBE_TARGET,
        ]
    )
    collection_started_at = datetime.now(timezone.utc)
    output = ""
    failure: str | None = None
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=count * 1.2 + 2.0,
            shell=False,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = f"{completed.stdout}\n{completed.stderr}"
    except subprocess.TimeoutExpired:
        failure = "probe_timeout"
    except OSError:
        failure = "probe_unavailable"
    observed_at = datetime.now(timezone.utc)
    times, received, loss = _parse_probe_output(output, count) if output else ([], 0, None)
    outcomes = _probe_outcomes(output, count, received)
    latency = statistics.fmean(times) if times else None
    jitter = statistics.pstdev(times) if len(times) > 1 else (0.0 if times else None)
    sent, received_packets, errors, drops = _interface_counters(candidate.public.adapter_name)
    if not times and failure is None:
        failure = "no_parseable_replies" if output else "probe_unavailable"
    raw = RawMetrics(
        latencyMs=round(latency, 6) if latency is not None and math.isfinite(latency) else None,
        jitterMs=round(jitter, 6) if jitter is not None and math.isfinite(jitter) else None,
        lossPct=round(loss, 6) if loss is not None else None,
        sampleCount=count,
        reachable=received > 0,
        interfacePacketsSent=sent,
        interfacePacketsReceived=received_packets,
        interfaceErrors=errors,
        interfaceDrops=drops,
    )
    return ProbeResult(
        path_id=candidate.public.path_id,
        observed_at=observed_at,
        collection_started_at=collection_started_at,
        observation_validated_at=observed_at if received > 0 else None,
        collection_duration_ms=max(
            0.0,
            (observed_at - collection_started_at).total_seconds() * 1000.0,
        ),
        raw=raw,
        probe_outcomes=outcomes,
        failure_reason=failure,
    )
