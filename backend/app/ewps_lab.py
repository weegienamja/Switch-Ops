"""Narrowly scoped WSL2 dual-path laboratory for EWPS v0.2.

Every mutation is fixed in this module and occurs inside namespaces whose
names begin with ``ewps02-``.  The loopback API exposes enums, never command
text, interface names, addresses, or a generic shell surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
import statistics
import subprocess
from threading import RLock

from .ewps_telemetry import ProbeResult, _parse_probe_output, _probe_outcomes
from .ewps_models import RawMetrics
from .ewps_v2_models import (
    LabPathStatus,
    LabProfileName,
    LabScenarioName,
    LabStatus,
    V2CandidatePath,
)


LAB_PATHS = {
    "lab-path-a": {
        "label": "Path A",
        "gateway_namespace": "ewps02-gwa",
        "gateway_interface": "ewp-ag1",
        "source_ip": "10.254.1.1",
        "target_ip": "10.254.11.2",
    },
    "lab-path-b": {
        "label": "Path B",
        "gateway_namespace": "ewps02-gwb",
        "gateway_interface": "ewp-bg1",
        "source_ip": "10.254.2.1",
        "target_ip": "10.254.12.2",
    },
}


@dataclass(frozen=True)
class ImpairmentProfile:
    name: LabProfileName
    netem_args: tuple[str, ...]
    suppress_collection: bool = False


PROFILES: dict[LabProfileName, ImpairmentProfile] = {
    "fast-stable": ImpairmentProfile("fast-stable", ("delay", "8ms", "0.5ms", "10%")),
    "slow-stable": ImpairmentProfile("slow-stable", ("delay", "28ms", "0.5ms", "10%")),
    "fast-noisy": ImpairmentProfile("fast-noisy", ("delay", "8ms", "12ms", "25%", "distribution", "normal")),
    "moderate-jitter": ImpairmentProfile("moderate-jitter", ("delay", "18ms", "6ms", "25%", "distribution", "normal")),
    "intermittent-loss": ImpairmentProfile("intermittent-loss", ("delay", "12ms", "2ms", "10%", "loss", "8%", "25%")),
    "sustained-loss": ImpairmentProfile("sustained-loss", ("delay", "12ms", "2ms", "10%", "loss", "33%", "50%")),
    "telemetry-stale": ImpairmentProfile("telemetry-stale", ("delay", "8ms", "0.5ms", "10%"), suppress_collection=True),
    "temporary-failure": ImpairmentProfile("temporary-failure", ("delay", "8ms", "loss", "100%")),
    "recovery": ImpairmentProfile("recovery", ("delay", "9ms", "1ms", "10%")),
    "crossing-latency": ImpairmentProfile("crossing-latency", ("delay", "20ms", "4ms", "20%", "distribution", "normal")),
}


SCENARIO_PHASES: dict[LabScenarioName, tuple[dict[str, LabProfileName], ...]] = {
    "conventional-agreement": (
        {"lab-path-a": "fast-stable", "lab-path-b": "slow-stable"},
    ),
    "faster-epistemically-weak": (
        {"lab-path-a": "fast-stable", "lab-path-b": "slow-stable"},
        {"lab-path-a": "fast-noisy", "lab-path-b": "slow-stable"},
        {"lab-path-a": "telemetry-stale", "lab-path-b": "slow-stable"},
    ),
    "raw-metric-flapping": (
        {"lab-path-a": "fast-stable", "lab-path-b": "moderate-jitter"},
        {"lab-path-a": "slow-stable", "lab-path-b": "fast-stable"},
        {"lab-path-a": "fast-stable", "lab-path-b": "slow-stable"},
    ),
    "evidence-outage": (
        {"lab-path-a": "fast-stable", "lab-path-b": "slow-stable"},
        {"lab-path-a": "telemetry-stale", "lab-path-b": "slow-stable"},
    ),
    "recovery": (
        {"lab-path-a": "temporary-failure", "lab-path-b": "slow-stable"},
        {"lab-path-a": "recovery", "lab-path-b": "slow-stable"},
    ),
}


SETUP_SCRIPT = r"""
set -eu
for ns in ewps02-src ewps02-gwa ewps02-gwb ewps02-target; do
  ip netns del "$ns" 2>/dev/null || true
done
ip netns add ewps02-src
ip netns add ewps02-gwa
ip netns add ewps02-gwb
ip netns add ewps02-target
ip link add ewp-as type veth peer name ewp-ag0
ip link add ewp-at type veth peer name ewp-ag1
ip link add ewp-bs type veth peer name ewp-bg0
ip link add ewp-bt type veth peer name ewp-bg1
ip link set ewp-as netns ewps02-src
ip link set ewp-ag0 netns ewps02-gwa
ip link set ewp-at netns ewps02-target
ip link set ewp-ag1 netns ewps02-gwa
ip link set ewp-bs netns ewps02-src
ip link set ewp-bg0 netns ewps02-gwb
ip link set ewp-bt netns ewps02-target
ip link set ewp-bg1 netns ewps02-gwb
for ns in ewps02-src ewps02-gwa ewps02-gwb ewps02-target; do
  ip -n "$ns" link set lo up
done
ip -n ewps02-src addr add 10.254.1.1/30 dev ewp-as
ip -n ewps02-gwa addr add 10.254.1.2/30 dev ewp-ag0
ip -n ewps02-gwa addr add 10.254.11.1/30 dev ewp-ag1
ip -n ewps02-target addr add 10.254.11.2/30 dev ewp-at
ip -n ewps02-src addr add 10.254.2.1/30 dev ewp-bs
ip -n ewps02-gwb addr add 10.254.2.2/30 dev ewp-bg0
ip -n ewps02-gwb addr add 10.254.12.1/30 dev ewp-bg1
ip -n ewps02-target addr add 10.254.12.2/30 dev ewp-bt
ip -n ewps02-src link set ewp-as up
ip -n ewps02-gwa link set ewp-ag0 up
ip -n ewps02-gwa link set ewp-ag1 up
ip -n ewps02-target link set ewp-at up
ip -n ewps02-src link set ewp-bs up
ip -n ewps02-gwb link set ewp-bg0 up
ip -n ewps02-gwb link set ewp-bg1 up
ip -n ewps02-target link set ewp-bt up
ip netns exec ewps02-gwa sysctl -q -w net.ipv4.ip_forward=1
ip netns exec ewps02-gwb sysctl -q -w net.ipv4.ip_forward=1
ip -n ewps02-src route add 10.254.11.2/32 via 10.254.1.2 dev ewp-as
ip -n ewps02-src route add 10.254.12.2/32 via 10.254.2.2 dev ewp-bs
ip -n ewps02-target route add 10.254.1.1/32 via 10.254.11.1 dev ewp-at
ip -n ewps02-target route add 10.254.2.1/32 via 10.254.12.1 dev ewp-bt
""".strip()

TEARDOWN_SCRIPT = r"""
set -eu
for ns in ewps02-src ewps02-gwa ewps02-gwb ewps02-target; do
  ip netns del "$ns" 2>/dev/null || true
done
""".strip()

PREREQUISITE_SCRIPT = r"""
set -eu
test "$(id -u)" = "0"
command -v ip >/dev/null
command -v tc >/dev/null
command -v ping >/dev/null
grep -qi microsoft /proc/sys/kernel/osrelease
""".strip()


def _wsl(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    if os.name != "nt":
        raise OSError("The controlled lab requires Windows with WSL2.")
    return subprocess.run(
        ["wsl.exe", "--user", "root", "--exec", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class EWPSLabManager:
    def __init__(self) -> None:
        self._lock = RLock()
        self._keepalive: subprocess.Popen[str] | None = None
        self._ready = False
        self._scenario: LabScenarioName | None = None
        self._phase = 0
        self._profiles: dict[str, LabProfileName] = {
            "lab-path-a": "fast-stable",
            "lab-path-b": "slow-stable",
        }
        self._last_results: dict[str, ProbeResult] = {}
        self._message = "Run the prerequisite check, then explicitly create the contained lab."

    def _start_keepalive(self) -> None:
        """Keep the WSL VM alive only for the lifetime of an explicit lab.

        Without a resident process, WSL may stop its utility VM between API
        calls and discard the network namespaces. The command is fixed here;
        no API field can influence it.
        """
        if self._keepalive is not None and self._keepalive.poll() is None:
            return
        if os.name != "nt":
            raise OSError("The controlled lab requires Windows with WSL2.")
        self._keepalive = subprocess.Popen(
            [
                "wsl.exe", "--user", "root", "--exec", "sh", "-lc",
                "trap 'exit 0' TERM INT; while :; do sleep 3600; done",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _stop_keepalive(self) -> None:
        keepalive = self._keepalive
        self._keepalive = None
        if keepalive is None or keepalive.poll() is not None:
            return
        keepalive.terminate()
        try:
            keepalive.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            keepalive.kill()
            keepalive.wait(timeout=2.0)

    def prerequisites(self) -> LabStatus:
        try:
            completed = _wsl(["sh", "-lc", PREREQUISITE_SCRIPT], timeout=10.0)
            available = completed.returncode == 0
            message = (
                "WSL2, iproute2, tc netem, ping, and non-interactive root execution are available."
                if available
                else "WSL2 is present but the lab requires root plus ip, tc, and ping inside the default distribution."
            )
        except (OSError, subprocess.TimeoutExpired):
            available = False
            message = "WSL2 or a required contained-networking facility is unavailable."
        return self._status(available=available, message=message)

    def _namespaces_present(self) -> bool:
        try:
            completed = _wsl(["ip", "netns", "list"], timeout=5.0)
        except (OSError, subprocess.TimeoutExpired):
            return False
        names = {line.split()[0] for line in completed.stdout.splitlines() if line.strip()}
        return {"ewps02-src", "ewps02-gwa", "ewps02-gwb", "ewps02-target"}.issubset(names)

    def _status(self, *, available: bool | None = None, message: str | None = None) -> LabStatus:
        if available is None:
            available = os.name == "nt"
        ready = self._ready and self._namespaces_present()
        paths: list[LabPathStatus] = []
        for path_id, definition in LAB_PATHS.items():
            result = self._last_results.get(path_id)
            paths.append(LabPathStatus(
                pathId=path_id,
                displayLabel=definition["label"],
                profile=self._profiles[path_id],
                independentlyValidated=bool(
                    result and result.observation_validated_at and result.raw.reachable
                ),
                lastLatencyMs=result.raw.latency_ms if result else None,
                lastValidatedAt=result.observation_validated_at if result else None,
            ))
        return LabStatus(
            available=available,
            ready=ready,
            message=message or self._message,
            scenarioId=self._scenario,
            scenarioPhase=self._phase,
            paths=paths,
        )

    def status(self) -> LabStatus:
        with self._lock:
            if self._ready and not self._namespaces_present():
                self._ready = False
                self._message = "The contained namespaces are no longer present; create the lab again."
            return self._status()

    def create(self) -> LabStatus:
        prerequisite = self.prerequisites()
        if not prerequisite.available:
            return prerequisite
        with self._lock:
            try:
                self._start_keepalive()
                completed = _wsl(["sh", "-lc", SETUP_SCRIPT], timeout=20.0)
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._stop_keepalive()
                self._ready = False
                self._message = f"Contained lab creation failed: {type(exc).__name__}."
                return self._status(message=self._message)
            if completed.returncode != 0:
                self._stop_keepalive()
                self._ready = False
                detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "unknown setup error"
                self._message = f"Contained lab creation failed: {detail[:180]}"
                return self._status(message=self._message)
            self._ready = True
            self._scenario = None
            self._phase = 0
            self._last_results.clear()
            self.apply_profile("lab-path-a", "fast-stable")
            self.apply_profile("lab-path-b", "slow-stable")
            self._message = "Contained logical paths created. Verification has not yet been run."
            return self.verify()

    def teardown(self) -> LabStatus:
        with self._lock:
            try:
                completed = _wsl(["sh", "-lc", TEARDOWN_SCRIPT], timeout=12.0)
                success = completed.returncode == 0 and not self._namespaces_present()
            except (OSError, subprocess.TimeoutExpired):
                success = False
            self._stop_keepalive()
            self._ready = False
            self._scenario = None
            self._phase = 0
            self._last_results.clear()
            self._message = (
                "Controlled lab removed; no ewps02 namespaces remain."
                if success
                else "Lab teardown could not be verified; inspect WSL before recreating it."
            )
            return self._status(message=self._message)

    def shutdown(self) -> None:
        """Best-effort cleanup when the desktop/backend process exits."""
        if self._ready or self._keepalive is not None:
            self.teardown()

    def candidates(self) -> list[V2CandidatePath]:
        if not self.status().ready:
            return []
        return [
            V2CandidatePath(
                pathId=path_id,
                displayLabel=definition["label"],
                adapterName=f"Controlled logical {definition['label']}",
                sourceKind="controlled_lab",
                lifecycle="VIABLE",
                topologyEvidence="reciprocal_independent_direct",
                topologyDetail=(
                    "Both endpoints and the contained gateway chain are explicitly configured and independently probed "
                    "inside WSL2; this does not establish physical or ISP diversity."
                ),
            )
            for path_id, definition in LAB_PATHS.items()
        ]

    def apply_profile(self, path_id: str, profile_name: LabProfileName) -> LabStatus:
        if path_id not in LAB_PATHS or profile_name not in PROFILES:
            raise KeyError("Unknown controlled lab path or profile.")
        if not self._ready:
            raise ValueError("Create the controlled lab before applying an impairment profile.")
        definition = LAB_PATHS[path_id]
        profile = PROFILES[profile_name]
        args = [
            "ip", "netns", "exec", definition["gateway_namespace"],
            "tc", "qdisc", "replace", "dev", definition["gateway_interface"],
            "root", "netem", *profile.netem_args,
        ]
        completed = _wsl(args, timeout=8.0)
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "tc netem failed"
            raise RuntimeError(detail[:180])
        self._profiles[path_id] = profile_name
        self._message = f"{LAB_PATHS[path_id]['label']} now uses the {profile_name} profile."
        return self._status()

    def prepare_scenario(self, scenario_id: LabScenarioName) -> LabStatus:
        if scenario_id not in SCENARIO_PHASES:
            raise KeyError(scenario_id)
        if not self._ready:
            raise ValueError("Create the controlled lab before preparing a scenario.")
        with self._lock:
            self._scenario = scenario_id
            self._phase = 0
            for path_id, profile in SCENARIO_PHASES[scenario_id][0].items():
                self.apply_profile(path_id, profile)
            self._message = f"Scenario {scenario_id} prepared at phase 1. No experiment was started."
            return self.verify()

    def advance_scenario(self) -> LabStatus:
        with self._lock:
            if self._scenario is None:
                raise ValueError("Prepare a scenario before advancing its impairment phase.")
            phases = SCENARIO_PHASES[self._scenario]
            self._phase = min(self._phase + 1, len(phases) - 1)
            for path_id, profile in phases[self._phase].items():
                self.apply_profile(path_id, profile)
            self._message = f"Scenario {self._scenario} advanced to phase {self._phase + 1}."
            return self._status()

    def profile(self, path_id: str) -> ImpairmentProfile:
        return PROFILES[self._profiles[path_id]]

    def measure(self, path_id: str, count: int) -> ProbeResult:
        if path_id not in LAB_PATHS:
            raise KeyError(path_id)
        if not self._ready:
            raise ValueError("The controlled lab is not ready.")
        definition = LAB_PATHS[path_id]
        profile = self.profile(path_id)
        started = datetime.now(timezone.utc)
        if profile.suppress_collection:
            ended = datetime.now(timezone.utc)
            return ProbeResult(
                path_id=path_id,
                observed_at=ended,
                collection_started_at=started,
                observation_validated_at=None,
                collection_duration_ms=max(0.0, (ended - started).total_seconds() * 1000.0),
                raw=RawMetrics(sampleCount=0, reachable=True),
                probe_outcomes=(),
                failure_reason="controlled_evidence_stale",
            )
        count = max(1, min(5, int(count)))
        args = [
            "ip", "netns", "exec", "ewps02-src", "ping", "-n",
            "-c", str(count), "-W", "1", "-I", definition["source_ip"], definition["target_ip"],
        ]
        output = ""
        failure: str | None = None
        try:
            completed = _wsl(args, timeout=count * 1.5 + 3.0)
            output = f"{completed.stdout}\n{completed.stderr}"
        except subprocess.TimeoutExpired:
            failure = "probe_timeout"
        except OSError:
            failure = "probe_unavailable"
        ended = datetime.now(timezone.utc)
        times, received, loss = _parse_probe_output(output, count) if output else ([], 0, None)
        outcomes = _probe_outcomes(output, count, received)
        latency = statistics.fmean(times) if times else None
        jitter = statistics.pstdev(times) if len(times) > 1 else (0.0 if times else None)
        if not times and failure is None:
            failure = "complete_probe_failure"
        return ProbeResult(
            path_id=path_id,
            observed_at=ended,
            collection_started_at=started,
            observation_validated_at=ended if received > 0 else None,
            collection_duration_ms=max(0.0, (ended - started).total_seconds() * 1000.0),
            raw=RawMetrics(
                latencyMs=round(latency, 6) if latency is not None and math.isfinite(latency) else None,
                jitterMs=round(jitter, 6) if jitter is not None and math.isfinite(jitter) else None,
                lossPct=round(loss, 6) if loss is not None else None,
                sampleCount=count,
                reachable=received > 0,
            ),
            probe_outcomes=outcomes,
            failure_reason=failure,
        )

    def verify(self) -> LabStatus:
        if not self._ready:
            return self._status(message="Create the controlled lab before verification.")
        results = {path_id: self.measure(path_id, 3) for path_id in LAB_PATHS}
        self._last_results = results
        valid = all(result.observation_validated_at and result.raw.reachable for result in results.values())
        self._message = (
            "Both controlled logical paths independently returned validated telemetry."
            if valid
            else "The lab exists, but both paths did not independently validate; inspect the selected profiles."
        )
        return self._status(message=self._message)


_lab_manager: EWPSLabManager | None = None


def get_ewps_lab() -> EWPSLabManager:
    global _lab_manager
    if _lab_manager is None:
        _lab_manager = EWPSLabManager()
    return _lab_manager
