from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import subprocess
import time

from fastapi.testclient import TestClient

from app.ewps_models import CandidatePath, RawMetrics
from app.ewps_store import EWPSResearchStore
from app.ewps_telemetry import FIXED_PROBE_TARGET, InternalCandidate, ProbeResult, measure_candidate
from app.ewps_v2_service import EWPSV2Service
from app.main import app


client = TestClient(app)


def internal(path_id: str, source_ip: str, label: str) -> InternalCandidate:
    return InternalCandidate(
        public=CandidatePath(
            pathId=path_id,
            displayLabel=label,
            adapterName=f"Adapter {label}",
            topologyEvidence="one_sided_direct",
            topologyDetail="Synthetic one-sided direct observation.",
        ),
        source_ip=source_ip,
    )


def test_meta_declares_non_probability_shadow_boundary_and_versioned_mapping():
    response = client.get("/api/ewps/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "SHADOW"
    assert body["changesNetworkState"] is False
    assert body["modelVersion"] == "0.2.0"
    assert body["releaseId"] == "ewps-v0.2.4-alpha"
    assert "calibrated probability" in body["confidenceSemantics"]
    assert body["topologyMappingVersion"]
    assert body["compatibility"]["v01SemanticsPreserved"] is True
    assert "start-to-start" in body["sampleIntervalSemantics"]
    assert body["compatibility"]["cadenceInstrumentationAdditive"] is True


def test_simulator_api_covers_scenarios_and_returns_shadow_decisions():
    scenarios = client.get("/api/ewps/simulator/scenarios")
    assert scenarios.status_code == 200
    assert len(scenarios.json()) >= 8
    response = client.post(
        "/api/ewps/simulator/run",
        json={"scenarioId": "faster-epistemically-weak", "config": {}},
    )
    assert response.status_code == 200
    assert response.json()["sourceMode"] == "SIMULATOR"
    assert response.json()["summary"]["shadowMode"] is True


def test_api_lifecycle_records_live_shadow_observation(tmp_path, monkeypatch):
    from app import ewps_api as api_mod
    from app import ewps_v2_service as service_mod

    candidates = [internal("path-a", "192.0.2.10", "A"), internal("path-b", "192.0.2.20", "B")]
    monkeypatch.setattr(service_mod, "candidate_catalog", lambda: candidates)

    def fake_measure(candidate, count):
        latency = 20.0 if candidate.public.path_id == "path-a" else 28.0
        return ProbeResult(
            path_id=candidate.public.path_id,
            observed_at=datetime.now(timezone.utc),
            raw=RawMetrics(latencyMs=latency, jitterMs=1, lossPct=0, sampleCount=count, reachable=True),
            collection_started_at=datetime.now(timezone.utc),
            observation_validated_at=datetime.now(timezone.utc),
            collection_duration_ms=1.0,
            probe_outcomes=tuple(True for _ in range(count)),
        )

    monkeypatch.setattr(service_mod, "measure_candidate", fake_measure)
    service = EWPSV2Service(
        EWPSResearchStore(tmp_path / "research.sqlite3"),
        SimpleNamespace(candidates=lambda: []),
    )
    monkeypatch.setattr(api_mod, "get_ewps_v2_service", lambda: service)
    created = client.post(
        "/api/ewps/experiments",
        json={
            "name": "API session",
            "workloadLabel": "Idle baseline",
            "sourceMode": "REAL_INTERFACES",
            "candidatePathIds": ["path-a", "path-b"],
            "config": {
                "sampleIntervalSeconds": 300,
                "pPerfMin": 0.01,
                "hysteresis": {
                    "minimumImprovement": 0,
                    "minimumDwellSeconds": 0,
                    "minimumEvidenceSeconds": 0,
                    "recoveryHoldDownSeconds": 0,
                },
            },
        },
    )
    assert created.status_code == 200
    experiment_id = created.json()["experimentId"]
    assert client.post(f"/api/ewps/experiments/{experiment_id}/start").status_code == 200
    deadline = time.monotonic() + 2
    while service.get(experiment_id).decision_points == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    timeline = client.get(f"/api/ewps/experiments/{experiment_id}/timeline").json()
    assert timeline["decisions"]
    assert all(item["hysteresis"]["switchBlockedBy"] == "shadow_mode" for item in timeline["decisions"])
    stopped = client.post(f"/api/ewps/experiments/{experiment_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["session"]["status"] == "COMPLETED"
    replay = client.post(f"/api/ewps/experiments/{experiment_id}/replay", json={"config": None})
    assert replay.status_code == 200
    assert replay.json()["deterministicDigest"]
    assert replay.json()["sourceMode"] == "REAL_INTERFACES"
    replay_override = client.post(
        f"/api/ewps/experiments/{experiment_id}/replay",
        json={"config": {"alpha": 2.0, "pPerfMin": 0.01}},
    )
    assert replay_override.status_code == 200
    assert replay_override.json()["config"]["alpha"] == 2.0
    exported = client.get(f"/api/ewps/experiments/{experiment_id}/export?format=jsonl")
    assert exported.status_code == 200
    assert "application/x-ndjson" in exported.headers["content-type"]


def test_api_rejects_arbitrary_probe_or_shell_fields(tmp_path, monkeypatch):
    from app import ewps_api as api_mod
    from app import ewps_v2_service as service_mod

    candidate = internal("path-a", "192.0.2.10", "A")
    monkeypatch.setattr(service_mod, "candidate_catalog", lambda: [candidate])
    service = EWPSV2Service(
        EWPSResearchStore(tmp_path / "research.sqlite3"),
        SimpleNamespace(candidates=lambda: []),
    )
    monkeypatch.setattr(api_mod, "get_ewps_v2_service", lambda: service)
    response = client.post(
        "/api/ewps/experiments",
        json={
            "name": "Rejected extra input",
            "workloadLabel": "Idle baseline",
            "candidatePathIds": ["path-a"],
            "config": {},
            "target": "attacker.invalid",
            "command": "route delete 0.0.0.0",
        },
    )
    assert response.status_code == 422
    assert "attacker.invalid" not in response.text
    assert "route delete" not in response.text


def test_live_probe_uses_fixed_argument_array_and_source_binding(monkeypatch):
    from app import ewps_telemetry as telemetry_mod

    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            stdout="Reply from 1.1.1.1: bytes=32 time=12ms TTL=57\nReply from 1.1.1.1: bytes=32 time=14ms TTL=57\nPackets: Sent = 2, Received = 2, Lost = 0 (0% loss)",
            stderr="",
        )

    monkeypatch.setattr(telemetry_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(telemetry_mod, "_interface_counters", lambda _name: (10, 20, 0, 0))
    result = measure_candidate(internal("path-a", "192.0.2.10", "A"), 2)
    args, kwargs = calls[0]
    assert isinstance(args, list)
    assert args[0].lower().startswith("ping")
    assert FIXED_PROBE_TARGET in args
    assert "192.0.2.10" in args
    assert kwargs["shell"] is False
    assert result.raw.latency_ms == 13
    assert result.raw.jitter_ms == 1
    assert result.raw.reachable is True


def test_probe_timeout_is_recorded_as_missing_not_infinite(monkeypatch):
    from app import ewps_telemetry as telemetry_mod

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="ping", timeout=1)

    monkeypatch.setattr(telemetry_mod.subprocess, "run", timeout)
    monkeypatch.setattr(telemetry_mod, "_interface_counters", lambda _name: (None, None, None, None))
    result = measure_candidate(internal("path-a", "192.0.2.10", "A"), 1)
    assert result.failure_reason == "probe_timeout"
    assert result.raw.latency_ms is None
    assert not result.raw.reachable


def test_ewps_module_has_no_route_changing_execution_path():
    root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.glob("ewps_*.py")
        if path.name not in {"test_ewps_api_and_telemetry.py", "ewps_lab.py"}
    ).lower()
    forbidden = ("route.exe", "route add", "route delete", "set-netroute", "netsh", "configure terminal", "meraki_client")
    assert all(token not in source for token in forbidden)
    lab_source = (root / "ewps_lab.py").read_text(encoding="utf-8").lower()
    assert all(token not in lab_source for token in ("route.exe", "set-netroute", "netsh"))
    assert all(
        "ip -n ewps02-" in line
        for line in lab_source.splitlines()
        if " route add " in line
    )
