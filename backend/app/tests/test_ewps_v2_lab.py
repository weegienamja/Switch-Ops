from types import SimpleNamespace

import pytest

from app import ewps_lab
from app.ewps_lab import EWPSLabManager, LAB_PATHS, PROFILES, SCENARIO_PHASES


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
                return completed()
            if "ip netns del" in script:
                namespaces_present = False
                return completed()
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
    assert "no ewps02 namespaces remain" in removed.message
    assert not namespaces_present
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
