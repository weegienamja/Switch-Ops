"""Narrowly scoped WSL2 dual-path laboratory for EWPS v0.2.

Every mutation is fixed in this module and occurs inside namespaces whose
names begin with ``ewps02-``.  The loopback API exposes enums, never command
text, interface names, addresses, or a generic shell surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
import statistics
import subprocess
from threading import RLock
import uuid

from .ewps_telemetry import ProbeResult, _parse_probe_output, _probe_outcomes
from .ewps_models import RawMetrics
from .ewps_v2_models import (
    LabPhaseTransitionResult,
    LabPathStatus,
    LabProfileName,
    LabScenarioName,
    LabStatus,
    V2CandidatePath,
    V2NormalizedNetemConfig,
    V2PhasePathProfile,
    V2ScenarioPhaseSnapshot,
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
LAB_TOPOLOGY_VERSION = "switchops-ewps-contained-dual-path-v1"
LAB_MARKER_PATH = "/run/switchops-ewps-v02/owner"


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

SCENARIO_PHASE_IDS: dict[LabScenarioName, tuple[str, ...]] = {
    "conventional-agreement": ("baseline",),
    "faster-epistemically-weak": ("baseline", "fast-noisy", "telemetry-stale"),
    "raw-metric-flapping": (
        "fast-stable-vs-moderate-jitter",
        "slow-stable-vs-fast-stable",
        "fast-stable-vs-slow-stable",
    ),
    "evidence-outage": ("baseline", "telemetry-stale"),
    "recovery": ("temporary-failure", "recovery"),
}


def _milliseconds(value: str) -> float:
    if not value.endswith("ms"):
        raise ValueError("Only bounded millisecond netem values are supported.")
    return round(float(value[:-2]), 6)


def _percentage(value: str) -> float:
    if not value.endswith("%"):
        raise ValueError("Only bounded percentage netem values are supported.")
    return round(float(value[:-1]), 6)


def _requested_netem(profile: ImpairmentProfile) -> V2NormalizedNetemConfig:
    tokens = list(profile.netem_args)
    values: dict[str, object] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "delay":
            values["delayMs"] = _milliseconds(tokens[index + 1])
            index += 2
            if index < len(tokens) and tokens[index].endswith("ms"):
                values["jitterMs"] = _milliseconds(tokens[index])
                index += 1
            if index < len(tokens) and tokens[index].endswith("%"):
                values["delayCorrelationPct"] = _percentage(tokens[index])
                index += 1
        elif token == "distribution":
            values["distribution"] = tokens[index + 1]
            index += 2
        elif token == "loss":
            values["lossPct"] = _percentage(tokens[index + 1])
            index += 2
            if index < len(tokens) and tokens[index].endswith("%"):
                values["lossCorrelationPct"] = _percentage(tokens[index])
                index += 1
        else:
            raise ValueError(f"Unsupported fixed netem profile token: {token}")
    return V2NormalizedNetemConfig(**values)


def _normalized_actual_netem(payload: str) -> V2NormalizedNetemConfig | None:
    try:
        entries = json.loads(payload)
        entry = next(item for item in entries if item.get("kind") == "netem")
        options = entry.get("options") or {}
    except (json.JSONDecodeError, StopIteration, TypeError, AttributeError):
        return None
    values: dict[str, object] = {}
    delay = options.get("delay")
    if isinstance(delay, dict):
        if isinstance(delay.get("delay"), (int, float)):
            values["delayMs"] = round(float(delay["delay"]) * 1000.0, 6)
        if isinstance(delay.get("jitter"), (int, float)):
            values["jitterMs"] = round(float(delay["jitter"]) * 1000.0, 6)
        if isinstance(delay.get("correlation"), (int, float)):
            values["delayCorrelationPct"] = round(float(delay["correlation"]) * 100.0, 6)
    loss = options.get("loss")
    if isinstance(loss, dict):
        random_loss = loss.get("random", loss)
        if isinstance(random_loss, dict):
            probability = random_loss.get("probability", random_loss.get("loss"))
            correlation = random_loss.get("correlation")
            if isinstance(probability, (int, float)):
                values["lossPct"] = round(float(probability) * 100.0, 6)
            if isinstance(correlation, (int, float)):
                values["lossCorrelationPct"] = round(float(correlation) * 100.0, 6)
        elif isinstance(random_loss, (int, float)):
            values["lossPct"] = round(float(random_loss) * 100.0, 6)
    return V2NormalizedNetemConfig(**values)


def _netem_matches(requested: V2NormalizedNetemConfig, applied: V2NormalizedNetemConfig | None) -> bool:
    if applied is None:
        return False
    # iproute2 does not echo the distribution-table identifier. Every numeric
    # parameter it does expose must match the fixed requested profile.
    fields = (
        "delay_ms", "jitter_ms", "delay_correlation_pct",
        "loss_pct", "loss_correlation_pct",
    )
    for field in fields:
        expected = getattr(requested, field)
        actual = getattr(applied, field)
        if expected is None:
            if actual not in {None, 0.0}:
                return False
        elif actual is None or abs(expected - actual) > 0.01:
            return False
    return True


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
install -d -m 700 /run/switchops-ewps-v02
umask 077
printf '%s\n%s\n%s\n%s\n%s\n%s\n' "$1" 'switchops-ewps-contained-dual-path-v1' 'fast-stable' 'slow-stable' '-' '0' > /run/switchops-ewps-v02/owner
""".strip()

TEARDOWN_SCRIPT = r"""
set -eu
for ns in ewps02-src ewps02-gwa ewps02-gwb ewps02-target; do
  ip netns del "$ns" 2>/dev/null || true
done
rm -rf /run/switchops-ewps-v02
""".strip()

PREREQUISITE_SCRIPT = r"""
set -eu
test "$(id -u)" = "0"
command -v ip >/dev/null
command -v tc >/dev/null
command -v ping >/dev/null
grep -qi microsoft /proc/sys/kernel/osrelease
""".strip()

RECONCILE_SCRIPT = r"""
set -eu
test -f /run/switchops-ewps-v02/owner
test "$(sed -n '2p' /run/switchops-ewps-v02/owner)" = 'switchops-ewps-contained-dual-path-v1'
for ns in ewps02-src ewps02-gwa ewps02-gwb ewps02-target; do
  ip netns list | awk '{print $1}' | grep -Fxq "$ns"
done
ip -n ewps02-src link show ewp-as >/dev/null
ip -n ewps02-src link show ewp-bs >/dev/null
ip -n ewps02-gwa link show ewp-ag0 >/dev/null
ip -n ewps02-gwa link show ewp-ag1 >/dev/null
ip -n ewps02-gwb link show ewp-bg0 >/dev/null
ip -n ewps02-gwb link show ewp-bg1 >/dev/null
ip -n ewps02-target link show ewp-at >/dev/null
ip -n ewps02-target link show ewp-bt >/dev/null
cat /run/switchops-ewps-v02/owner
""".strip()

MARKER_SCRIPT = r"""
set -eu
test -f /run/switchops-ewps-v02/owner
umask 077
printf '%s\n%s\n%s\n%s\n%s\n%s\n' "$1" "$2" "$3" "$4" "$5" "$6" > /run/switchops-ewps-v02/owner
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
        self._reconciled = False
        self._created = False
        self._ready = False
        self._state = "LAB_NOT_CREATED"
        self._prerequisites_passed = False
        self._instance_id: str | None = None
        self._scenario: LabScenarioName | None = None
        self._phase = 0
        self._profiles: dict[str, LabProfileName] = {
            "lab-path-a": "fast-stable",
            "lab-path-b": "slow-stable",
        }
        self._last_results: dict[str, ProbeResult] = {}
        self._message = "Run the prerequisite check, then explicitly create the contained lab."

    def _check_prerequisites(self) -> tuple[bool, str]:
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
        self._prerequisites_passed = available
        return available, message

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
        available, message = self._check_prerequisites()
        return self._status(available=available, message=message)

    def _namespaces_present(self) -> bool:
        try:
            completed = _wsl(["ip", "netns", "list"], timeout=5.0)
        except (OSError, subprocess.TimeoutExpired):
            return False
        names = {line.split()[0] for line in completed.stdout.splitlines() if line.strip()}
        return {"ewps02-src", "ewps02-gwa", "ewps02-gwb", "ewps02-target"}.issubset(names)

    def _read_owned_topology(self) -> tuple[str, LabProfileName, LabProfileName, LabScenarioName | None, int] | None:
        try:
            completed = _wsl(["sh", "-lc", RECONCILE_SCRIPT], timeout=8.0)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if len(lines) != 6 or lines[1] != LAB_TOPOLOGY_VERSION:
            return None
        try:
            uuid.UUID(lines[0])
            profile_a = lines[2]
            profile_b = lines[3]
            scenario = None if lines[4] == "-" else lines[4]
            phase = int(lines[5])
        except (ValueError, TypeError):
            return None
        if profile_a not in PROFILES or profile_b not in PROFILES:
            return None
        if scenario is not None and scenario not in SCENARIO_PHASES:
            return None
        if phase < 0 or (scenario is not None and phase >= len(SCENARIO_PHASES[scenario])):
            return None
        return lines[0], profile_a, profile_b, scenario, phase

    def _write_marker(self) -> None:
        if not self._instance_id:
            raise RuntimeError("The controlled lab has no owned instance identity.")
        completed = _wsl(
            [
                "sh", "-lc", MARKER_SCRIPT, "switchops-ewps-marker",
                self._instance_id,
                LAB_TOPOLOGY_VERSION,
                self._profiles["lab-path-a"],
                self._profiles["lab-path-b"],
                self._scenario or "-",
                str(self._phase),
            ],
            timeout=5.0,
        )
        if completed.returncode != 0:
            raise RuntimeError("The controlled-lab ownership marker could not be updated.")

    def _reconcile_existing(self) -> None:
        self._reconciled = True
        available, _message = self._check_prerequisites()
        owned = self._read_owned_topology() if available else None
        if owned is None:
            self._created = False
            self._ready = False
            self._instance_id = None
            self._last_results.clear()
            if self._namespaces_present():
                self._state = "LAB_UNVERIFIED"
                self._message = "Contained namespaces exist, but EWPS ownership or topology validation failed."
            else:
                self._state = "LAB_NOT_CREATED"
                self._message = "LAB NOT CREATED. Run prerequisites, then explicitly create the contained lab."
            return
        instance_id, profile_a, profile_b, scenario, phase = owned
        self._created = True
        self._ready = False
        self._state = "LAB_UNVERIFIED"
        self._instance_id = instance_id
        self._profiles = {"lab-path-a": profile_a, "lab-path-b": profile_b}
        self._scenario = scenario
        self._phase = phase
        self._message = "Owned contained lab found after restart; verifying both paths."
        self._start_keepalive()
        self.verify()

    def _status(self, *, available: bool | None = None, message: str | None = None) -> LabStatus:
        if available is None:
            available = os.name == "nt"
        ready = self._ready and self._created
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
            state="LAB_READY" if ready else self._state,
            prerequisitesPassed=self._prerequisites_passed,
            labInstanceId=self._instance_id,
            topologyVersion=LAB_TOPOLOGY_VERSION,
            message=message or self._message,
            scenarioId=self._scenario,
            scenarioPhase=self._phase,
            scenarioPhaseId=(
                SCENARIO_PHASE_IDS[self._scenario][self._phase] if self._scenario is not None else None
            ),
            scenarioPhaseCount=(len(SCENARIO_PHASES[self._scenario]) if self._scenario is not None else 0),
            paths=paths,
        )

    def status(self) -> LabStatus:
        with self._lock:
            if not self._reconciled:
                self._reconcile_existing()
            if self._created and self._read_owned_topology() is None:
                self._created = False
                self._ready = False
                self._state = "LAB_LOST"
                self._message = "CONTROLLED LAB LOST. The owned topology is no longer present or no longer matches."
            return self._status()

    def create(self) -> LabStatus:
        prerequisite = self.prerequisites()
        if not prerequisite.available:
            return prerequisite
        with self._lock:
            instance_id = str(uuid.uuid4())
            try:
                self._start_keepalive()
                completed = _wsl(
                    ["sh", "-lc", SETUP_SCRIPT, "switchops-ewps-setup", instance_id],
                    timeout=20.0,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._stop_keepalive()
                self._created = False
                self._ready = False
                self._state = "LAB_NOT_CREATED"
                self._message = f"Contained lab creation failed: {type(exc).__name__}."
                return self._status(message=self._message)
            if completed.returncode != 0:
                self._stop_keepalive()
                self._created = False
                self._ready = False
                self._state = "LAB_NOT_CREATED"
                detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "unknown setup error"
                self._message = f"Contained lab creation failed: {detail[:180]}"
                return self._status(message=self._message)
            self._reconciled = True
            self._created = True
            self._ready = False
            self._state = "LAB_UNVERIFIED"
            self._instance_id = instance_id
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
            self._reconciled = True
            self._created = False
            self._ready = False
            self._state = "LAB_NOT_CREATED" if success else "LAB_UNVERIFIED"
            self._instance_id = None
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
        if self._created or self._keepalive is not None:
            self.teardown()

    def candidates(self) -> list[V2CandidatePath]:
        if not self.status().ready:
            return []
        return [
            V2CandidatePath(
                pathId=path_id,
                displayLabel=f"Controlled {definition['label']}",
                adapterName=self._profiles[path_id].replace("-", " ").title(),
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

    def _apply_profile_command(self, path_id: str, profile_name: LabProfileName) -> None:
        if path_id not in LAB_PATHS or profile_name not in PROFILES:
            raise KeyError("Unknown controlled lab path or profile.")
        if not self._created:
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

    def _query_profile(
        self,
        path_id: str,
        requested_profile: LabProfileName,
        *,
        current_profile: LabProfileName | None = None,
    ) -> V2PhasePathProfile:
        definition = LAB_PATHS[path_id]
        requested = _requested_netem(PROFILES[requested_profile])
        try:
            completed = _wsl([
                "ip", "netns", "exec", definition["gateway_namespace"],
                "tc", "-j", "qdisc", "show", "dev", definition["gateway_interface"],
            ], timeout=5.0)
            applied = _normalized_actual_netem(completed.stdout) if completed.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            applied = None
        matched = _netem_matches(requested, applied)
        applied_profile: LabProfileName | None = requested_profile if matched else None
        if not matched and applied is not None and current_profile is not None:
            if _netem_matches(_requested_netem(PROFILES[current_profile]), applied):
                applied_profile = current_profile
        return V2PhasePathProfile(
            requestedProfileId=requested_profile,
            appliedProfileId=applied_profile,
            requestedConfiguration=requested,
            appliedConfiguration=applied,
            verification="PASSED" if matched else "FAILED",
            verificationDetail=(
                "Owned namespace tc -j qdisc state matches every kernel-reported numeric netem parameter."
                if matched else
                "Owned namespace tc -j qdisc state does not match the requested fixed profile."
            ),
        )

    def _phase_snapshot(
        self,
        scenario: LabScenarioName,
        phase: int,
        profiles: dict[str, LabProfileName],
    ) -> V2ScenarioPhaseSnapshot:
        if not self._instance_id:
            raise RuntimeError("The controlled lab has no owned instance identity.")
        return V2ScenarioPhaseSnapshot(
            scenarioId=scenario,
            phaseIndex=phase,
            phaseId=SCENARIO_PHASE_IDS[scenario][phase],
            labInstanceId=self._instance_id,
            pathProfiles={
                path_id: self._query_profile(
                    path_id,
                    profile,
                    current_profile=self._profiles.get(path_id),
                )
                for path_id, profile in profiles.items()
            },
        )

    def current_phase_snapshot(self) -> V2ScenarioPhaseSnapshot | None:
        """Return the committed phase that owns collection, never telemetry inference."""

        with self._lock:
            if not self._created or self._scenario is None or not self._instance_id:
                return None
            snapshot = self._phase_snapshot(self._scenario, self._phase, dict(self._profiles))
            if any(item.verification != "PASSED" for item in snapshot.path_profiles.values()):
                self._ready = False
                self._state = "LAB_UNVERIFIED"
                self._message = "The committed scenario phase no longer matches the owned qdisc state."
                raise RuntimeError(self._message)
            return snapshot

    def apply_profile(self, path_id: str, profile_name: LabProfileName) -> LabStatus:
        with self._lock:
            self._apply_profile_command(path_id, profile_name)
            proof = self._query_profile(path_id, profile_name, current_profile=self._profiles.get(path_id))
            if proof.verification != "PASSED":
                self._ready = False
                self._state = "LAB_UNVERIFIED"
                raise RuntimeError(proof.verification_detail)
            self._profiles[path_id] = profile_name
            self._scenario = None
            self._phase = 0
            self._write_marker()
            self._message = f"{LAB_PATHS[path_id]['label']} now uses the verified {profile_name} profile."
            return self._status()

    def prepare_scenario(self, scenario_id: LabScenarioName) -> LabStatus:
        if scenario_id not in SCENARIO_PHASES:
            raise KeyError(scenario_id)
        if not self._created:
            raise ValueError("Create the controlled lab before preparing a scenario.")
        with self._lock:
            target = dict(SCENARIO_PHASES[scenario_id][0])
            for path_id, profile in target.items():
                self._apply_profile_command(path_id, profile)
            proof = self._phase_snapshot(scenario_id, 0, target)
            if any(item.verification != "PASSED" for item in proof.path_profiles.values()):
                self._ready = False
                self._state = "LAB_UNVERIFIED"
                raise RuntimeError("Scenario phase 0 qdisc verification failed.")
            self._scenario = scenario_id
            self._phase = 0
            self._profiles = target
            self._write_marker()
            self._message = f"Scenario {scenario_id} prepared at phase 1. No experiment was started."
            return self.verify()

    def advance_scenario(self, requested_at: datetime | None = None) -> LabPhaseTransitionResult:
        with self._lock:
            if self._scenario is None:
                raise ValueError("Prepare a scenario before advancing its impairment phase.")
            scenario = self._scenario
            phases = SCENARIO_PHASES[scenario]
            if self._phase + 1 >= len(phases):
                raise ValueError("The prepared scenario is already at its final impairment phase.")
            request_time = requested_at or datetime.now(timezone.utc)
            previous_phase = self._phase
            new_phase = previous_phase + 1
            previous_profiles = dict(self._profiles)
            target_profiles = dict(phases[new_phase])
            affected = [
                path_id for path_id in LAB_PATHS
                if previous_profiles[path_id] != target_profiles[path_id]
            ]
            apply_error: str | None = None
            for path_id in affected:
                try:
                    self._apply_profile_command(path_id, target_profiles[path_id])
                except RuntimeError as exc:
                    apply_error = str(exc)
                    break
            proof = self._phase_snapshot(scenario, new_phase, target_profiles)
            succeeded = apply_error is None and all(
                item.verification == "PASSED" for item in proof.path_profiles.values()
            )
            if succeeded:
                self._phase = new_phase
                self._profiles = target_profiles
                self._write_marker()
                self._ready = True
                self._state = "LAB_READY"
                detail = "Requested profiles match normalized qdisc state in both owned gateway namespaces."
                self._message = f"Scenario {scenario} advanced to verified phase {new_phase + 1}."
            else:
                rollback_ok = True
                for path_id, profile in previous_profiles.items():
                    try:
                        self._apply_profile_command(path_id, profile)
                    except RuntimeError:
                        rollback_ok = False
                rollback_proof = self._phase_snapshot(scenario, previous_phase, previous_profiles)
                rollback_ok = rollback_ok and all(
                    item.verification == "PASSED" for item in rollback_proof.path_profiles.values()
                )
                self._profiles = previous_profiles
                self._phase = previous_phase
                if rollback_ok:
                    self._write_marker()
                    self._ready = True
                    self._state = "LAB_READY"
                else:
                    self._ready = False
                    self._state = "LAB_UNVERIFIED"
                detail = (
                    f"Phase application failed ({apply_error or 'qdisc mismatch'}); "
                    f"rollback {'verified' if rollback_ok else 'could not be verified'}."
                )
                self._message = detail
            return LabPhaseTransitionResult(
                requestedAt=request_time,
                completedAt=datetime.now(timezone.utc),
                scenarioId=scenario,
                previousPhaseIndex=previous_phase,
                previousPhaseId=SCENARIO_PHASE_IDS[scenario][previous_phase],
                newPhaseIndex=new_phase,
                newPhaseId=SCENARIO_PHASE_IDS[scenario][new_phase],
                applicationSucceeded=succeeded,
                labInstanceId=self._instance_id or "",
                affectedPathIds=affected,
                pathProfiles=proof.path_profiles,
                verification="PASSED" if succeeded else "FAILED",
                detail=detail,
            )

    def profile(self, path_id: str) -> ImpairmentProfile:
        return PROFILES[self._profiles[path_id]]

    def measure(self, path_id: str, count: int) -> ProbeResult:
        if path_id not in LAB_PATHS:
            raise KeyError(path_id)
        if not self._created:
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
        if not self._created:
            return self._status(message="Create the controlled lab before verification.")
        if self._read_owned_topology() is None:
            self._created = False
            self._ready = False
            self._state = "LAB_LOST"
            self._message = "CONTROLLED LAB LOST. Ownership or topology validation failed."
            return self._status(message=self._message)
        qdisc_proofs = {
            path_id: self._query_profile(path_id, profile, current_profile=profile)
            for path_id, profile in self._profiles.items()
        }
        if any(proof.verification != "PASSED" for proof in qdisc_proofs.values()):
            self._ready = False
            self._state = "LAB_UNVERIFIED"
            self._message = "The owned lab exists, but its normalized qdisc state does not match the marker."
            return self._status(message=self._message)
        results = {path_id: self.measure(path_id, 3) for path_id in LAB_PATHS}
        self._last_results = results
        valid = all(result.observation_validated_at and result.raw.reachable for result in results.values())
        self._ready = bool(valid)
        self._state = "LAB_READY" if valid else "LAB_UNVERIFIED"
        self._message = (
            "Both controlled logical paths independently returned validated telemetry."
            if valid
            else "The lab exists, but both paths did not independently validate; inspect the selected profiles."
        )
        return self._status(message=self._message)

    def validate_for_experiment(self, scenario: LabScenarioName | None) -> LabStatus:
        """Perform the full backend-owned start gate and return its fresh proof."""
        with self._lock:
            available, message = self._check_prerequisites()
            if not available:
                return self._status(available=False, message=message)
            if not self._reconciled:
                self._reconcile_existing()
            if not self._created:
                return self._status(message="LAB NOT CREATED. Controlled experiment startup rejected.")
            if scenario != self._scenario:
                self._ready = False
                self._state = "LAB_UNVERIFIED"
                return self._status(message="The experiment scenario does not match the prepared controlled-lab scenario.")
            return self.verify()

    def binding_is_current(self, instance_id: str | None, topology_version: str | None) -> bool:
        with self._lock:
            owned = self._read_owned_topology()
            current = bool(
                owned
                and instance_id
                and topology_version == LAB_TOPOLOGY_VERSION
                and owned[0] == instance_id
            )
            if not current:
                self._created = False
                self._ready = False
                self._state = "LAB_LOST"
                self._message = "CONTROLLED LAB LOST. The experiment's immutable lab binding is absent."
            return current


_lab_manager: EWPSLabManager | None = None


def get_ewps_lab() -> EWPSLabManager:
    global _lab_manager
    if _lab_manager is None:
        _lab_manager = EWPSLabManager()
    return _lab_manager
