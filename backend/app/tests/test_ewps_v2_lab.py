from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from app import ewps_lab
from app.ewps_lab import EWPSLabManager, LAB_PATHS, LAB_TOPOLOGY_VERSION, PROFILES, SCENARIO_PHASES
from app.ewps_models import RawMetrics
from app.ewps_telemetry import ProbeResult


def completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_wsl_wrapper_uses_a_fixed_argument_array_without_a_shell(monkeypatch):
    calls = []
    monkeypatch.setattr(ewps_lab.os, "name", "nt")

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return completed()

    monkeypatch.setattr(ewps_lab.subprocess, "run", fake_run)
    ewps_lab._wsl(["ip", "netns", "list"], timeout=5)
    args, kwargs = calls[0]
    assert args == ["wsl.exe", "--user", "root", "--exec", "ip", "netns", "list"]
    assert kwargs["shell"] is False
    assert kwargs["timeout"] == 5


def test_lab_keepalive_is_fixed_and_stops_with_the_lab(monkeypatch):
    calls = []

    class Process:
        running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def wait(self, timeout):
            calls.append(("wait", timeout))
            return 0

        def kill(self):
            self.running = False

    def fake_popen(args, **kwargs):
        calls.append((list(args), kwargs))
        return Process()

    monkeypatch.setattr(ewps_lab.os, "name", "nt")
    monkeypatch.setattr(ewps_lab.subprocess, "Popen", fake_popen)
    manager = EWPSLabManager()
    manager._start_keepalive()
    args, kwargs = calls[0]
    assert args[:6] == ["wsl.exe", "--user", "root", "--exec", "sh", "-lc"]
    assert args[6] == "trap 'exit 0' TERM INT; while :; do sleep 3600; done"
    assert kwargs["shell"] is False
    manager._stop_keepalive()
    assert calls[-1] == ("wait", 5.0)


def test_lab_surface_is_fixed_to_two_paths_profiles_and_named_scenarios():
    assert set(LAB_PATHS) == {"lab-path-a", "lab-path-b"}
    assert set(PROFILES) == {
        "fast-stable", "slow-stable", "fast-noisy", "moderate-jitter",
        "intermittent-loss", "sustained-loss", "telemetry-stale",
        "temporary-failure", "recovery", "crossing-latency",
    }
    assert set(SCENARIO_PHASES) == {
        "conventional-agreement", "faster-epistemically-weak",
        "raw-metric-flapping", "evidence-outage", "recovery",
    }
    for phases in SCENARIO_PHASES.values():
        assert phases
        assert all(set(phase) == set(LAB_PATHS) for phase in phases)


def test_manager_requires_explicit_create_and_reports_missing_facilities(monkeypatch):
    manager = EWPSLabManager()
    assert not manager.status().ready
    monkeypatch.setattr(ewps_lab, "_wsl", lambda args, timeout: completed(returncode=1))
    status = manager.create()
    assert not status.available
    assert not status.ready
    assert status.explicit_start_required
    assert "requires root" in status.message


def test_create_verify_scenario_and_complete_teardown_are_auditable(monkeypatch):
    namespaces_present = False
    marker: list[str] = []
    calls: list[list[str]] = []
    ping = (
        "64 bytes from 10.254.11.2: icmp_seq=1 ttl=63 time=16.1 ms\n"
        "64 bytes from 10.254.11.2: icmp_seq=2 ttl=63 time=16.3 ms\n"
        "64 bytes from 10.254.11.2: icmp_seq=3 ttl=63 time=16.2 ms\n"
        "3 packets transmitted, 3 received, 0% packet loss\n"
    )

    def fake_wsl(args, *, timeout):
        nonlocal namespaces_present
        calls.append(list(args))
        if args[:2] == ["sh", "-lc"]:
            script = args[2]
            if "command -v tc" in script:
                return completed()
            if "ip netns add ewps02-src" in script:
                namespaces_present = True
                marker[:] = [args[4], LAB_TOPOLOGY_VERSION, "fast-stable", "slow-stable", "-", "0"]
                return completed()
            if "ip netns del" in script:
                namespaces_present = False
                marker.clear()
                return completed()
            if script == ewps_lab.MARKER_SCRIPT:
                marker[:] = args[4:10]
                return completed()
            if script == ewps_lab.RECONCILE_SCRIPT:
                return completed(stdout="\n".join(marker) + "\n") if namespaces_present and marker else completed(returncode=1)
        if args == ["ip", "netns", "list"]:
            names = "ewps02-src\newps02-gwa\newps02-gwb\newps02-target\n" if namespaces_present else ""
            return completed(stdout=names)
        if "ping" in args:
            return completed(stdout=ping)
        if "tc" in args:
            return completed()
        return completed(returncode=1, stderr="unexpected test command")

    monkeypatch.setattr(ewps_lab, "_wsl", fake_wsl)
    manager = EWPSLabManager()
    monkeypatch.setattr(manager, "_start_keepalive", lambda: None)
    monkeypatch.setattr(manager, "_stop_keepalive", lambda: None)
    created = manager.create()
    assert created.ready
    assert created.state == "LAB_READY"
    assert created.lab_instance_id
    assert created.topology_version == LAB_TOPOLOGY_VERSION
    assert all(path.independently_validated for path in created.paths)
    assert all(path.last_latency_ms is not None for path in created.paths)
    assert "logical test paths" in created.diversity_claim.lower()
    assert "no physical" in created.diversity_claim.lower()

    scenario = manager.prepare_scenario("faster-epistemically-weak")
    assert scenario.scenario_id == "faster-epistemically-weak"
    assert scenario.scenario_phase == 0
    advanced = manager.advance_scenario()
    assert advanced.scenario_phase == 1
    assert manager.profile("lab-path-a").name == "fast-noisy"
    assert manager.profile("lab-path-b").name == "slow-stable"

    removed = manager.teardown()
    assert not removed.ready
    assert removed.state == "LAB_NOT_CREATED"
    assert "no ewps02 namespaces remain" in removed.message
    assert not namespaces_present
    assert manager.candidates() == []
    assert all(isinstance(args, list) for args in calls)
    tc_calls = [args for args in calls if "tc" in args and "qdisc" in args]
    assert tc_calls
    assert all(args[:3] == ["ip", "netns", "exec"] for args in tc_calls)
    assert all(args[3] in {"ewps02-gwa", "ewps02-gwb"} for args in tc_calls)


def test_profile_and_scenario_inputs_are_enums_not_commands():
    manager = EWPSLabManager()
    with pytest.raises(KeyError):
        manager.apply_profile("lab-path-a", "delay 1ms; route delete default")
    with pytest.raises(KeyError):
        manager.apply_profile("host-adapter", "fast-stable")
    with pytest.raises(KeyError):
        manager.prepare_scenario("arbitrary-shell")


def test_restart_reconciles_owned_topology_then_reverifies_both_paths(monkeypatch):
    marker = [
        "11111111-1111-4111-8111-111111111111",
        LAB_TOPOLOGY_VERSION,
        "fast-stable",
        "slow-stable",
        "faster-epistemically-weak",
        "0",
    ]
    ping = (
        "64 bytes from target: icmp_seq=1 ttl=63 time=8.1 ms\n"
        "64 bytes from target: icmp_seq=2 ttl=63 time=8.2 ms\n"
        "64 bytes from target: icmp_seq=3 ttl=63 time=8.3 ms\n"
        "3 packets transmitted, 3 received, 0% packet loss\n"
    )

    def fake_wsl(args, *, timeout):
        if args[:2] == ["sh", "-lc"] and args[2] == ewps_lab.PREREQUISITE_SCRIPT:
            return completed()
        if args[:2] == ["sh", "-lc"] and args[2] == ewps_lab.RECONCILE_SCRIPT:
            return completed(stdout="\n".join(marker) + "\n")
        if "ping" in args:
            return completed(stdout=ping)
        if args == ["ip", "netns", "list"]:
            return completed(stdout="ewps02-src\newps02-gwa\newps02-gwb\newps02-target\n")
        return completed(returncode=1)

    monkeypatch.setattr(ewps_lab, "_wsl", fake_wsl)
    manager = EWPSLabManager()
    monkeypatch.setattr(manager, "_start_keepalive", lambda: None)
    status = manager.status()
    assert status.state == "LAB_READY"
    assert status.ready
    assert status.lab_instance_id == marker[0]
    assert all(path.independently_validated for path in status.paths)
    assert [item.path_id for item in manager.candidates()] == ["lab-path-a", "lab-path-b"]


def test_restart_never_trusts_unowned_or_mismatched_namespaces(monkeypatch):
    def fake_wsl(args, *, timeout):
        if args[:2] == ["sh", "-lc"] and args[2] == ewps_lab.PREREQUISITE_SCRIPT:
            return completed()
        if args[:2] == ["sh", "-lc"] and args[2] == ewps_lab.RECONCILE_SCRIPT:
            return completed(returncode=1, stderr="ownership mismatch")
        if args == ["ip", "netns", "list"]:
            return completed(stdout="ewps02-src\newps02-gwa\newps02-gwb\newps02-target\n")
        return completed(returncode=1)

    monkeypatch.setattr(ewps_lab, "_wsl", fake_wsl)
    manager = EWPSLabManager()
    status = manager.status()
    assert status.state == "LAB_UNVERIFIED"
    assert not status.ready
    assert manager.candidates() == []


def test_failed_path_verification_revokes_ready_and_candidates(monkeypatch):
    manager = EWPSLabManager()
    manager._reconciled = True
    manager._created = True
    manager._ready = True
    manager._state = "LAB_READY"
    manager._instance_id = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setattr(manager, "_read_owned_topology", lambda: (
        manager._instance_id, "fast-stable", "slow-stable", None, 0
    ))

    def measure(path_id, count):
        now = datetime.now(timezone.utc)
        valid = path_id == "lab-path-a"
        return ProbeResult(
            path_id=path_id,
            observed_at=now,
            collection_started_at=now,
            observation_validated_at=now if valid else None,
            collection_duration_ms=1,
            raw=RawMetrics(latencyMs=8 if valid else None, sampleCount=count, reachable=valid),
            probe_outcomes=tuple(valid for _ in range(count)),
            failure_reason=None if valid else "complete_probe_failure",
        )

    monkeypatch.setattr(manager, "measure", measure)
    status = manager.verify()
    assert status.state == "LAB_UNVERIFIED"
    assert not status.ready
    assert [path.independently_validated for path in status.paths] == [True, False]
    assert manager.candidates() == []
