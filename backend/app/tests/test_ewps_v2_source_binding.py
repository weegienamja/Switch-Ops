from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from app.ewps_lab import LAB_TOPOLOGY_VERSION
from app.ewps_models import RawMetrics
from app.ewps_store import EWPSResearchStore
from app.ewps_telemetry import ProbeResult
from app.ewps_v2_models import (
    EWPSV2Config,
    LabPathStatus,
    LabStatus,
    V2CandidatePath,
    V2CandidateSnapshot,
    V2ExperimentCreateRequest,
)
from app.ewps_v2_service import EWPSV2Service, V2EngineRuntime, V2InternalCandidate


INSTANCE_ID = "11111111-1111-4111-8111-111111111111"
SCENARIO = "faster-epistemically-weak"


def config() -> EWPSV2Config:
    return EWPSV2Config(
        pPerfMin=0,
        sampleIntervalSeconds=300,
        hysteresis={
            "minimumImprovement": 0,
            "minimumDwellSeconds": 0,
            "minimumEvidenceSeconds": 0,
            "recoveryHoldDownSeconds": 0,
        },
    )


def controlled_public(path_id: str) -> V2CandidatePath:
    suffix = "A" if path_id.endswith("a") else "B"
    profile = "Fast Stable" if suffix == "A" else "Slow Stable"
    return V2CandidatePath(
        pathId=path_id,
        displayLabel=f"Controlled Path {suffix}",
        adapterName=profile,
        sourceKind="controlled_lab",
        lifecycle="VIABLE",
        topologyEvidence="reciprocal_independent_direct",
        topologyDetail=f"Owned contained gateway chain {suffix}.",
    )


def real_internal(path_id: str) -> V2InternalCandidate:
    public = V2CandidatePath(
        pathId=path_id,
        displayLabel="Real Path A",
        adapterName="Test Ethernet",
        sourceKind="real_interface",
        lifecycle="VIABLE",
        topologyEvidence="weak_inference",
        topologyDetail="Synthetic local attachment.",
    )
    legacy = type("Legacy", (), {"public": public})()
    return V2InternalCandidate(public=public, source_ip="192.0.2.10", legacy=legacy)


def lab_status(*, ready: bool, verified_a: bool = True, verified_b: bool = True) -> LabStatus:
    return LabStatus(
        available=True,
        ready=ready,
        state="LAB_READY" if ready else "LAB_UNVERIFIED",
        prerequisitesPassed=True,
        labInstanceId=INSTANCE_ID,
        topologyVersion=LAB_TOPOLOGY_VERSION,
        message="test status",
        scenarioId=SCENARIO,
        paths=[
            LabPathStatus(pathId="lab-path-a", displayLabel="Path A", profile="fast-stable", independentlyValidated=verified_a),
            LabPathStatus(pathId="lab-path-b", displayLabel="Path B", profile="slow-stable", independentlyValidated=verified_b),
        ],
    )


class FakeLab:
    def __init__(self, status: LabStatus):
        self.current_status = status
        self.binding_current = True

    def validate_for_experiment(self, scenario):
        if scenario != SCENARIO:
            return self.current_status.model_copy(update={"ready": False, "state": "LAB_UNVERIFIED"})
        return self.current_status

    def candidates(self):
        if not self.current_status.ready:
            return []
        return [controlled_public("lab-path-a"), controlled_public("lab-path-b")]

    def binding_is_current(self, instance_id, topology_version):
        return self.binding_current and instance_id == INSTANCE_ID and topology_version == LAB_TOPOLOGY_VERSION

    def measure(self, path_id, count):
        now = datetime.now(timezone.utc)
        latency = 8.0 if path_id == "lab-path-a" else 28.0
        return ProbeResult(
            path_id=path_id,
            observed_at=now,
            collection_started_at=now,
            observation_validated_at=now,
            collection_duration_ms=1,
            raw=RawMetrics(latencyMs=latency, jitterMs=0.5, lossPct=0, sampleCount=count, reachable=True),
            probe_outcomes=tuple(True for _ in range(count)),
        )


def controlled_request(ids=None) -> V2ExperimentCreateRequest:
    return V2ExperimentCreateRequest(
        name="Controlled binding",
        workloadLabel="Calibration",
        sourceMode="CONTROLLED_DUAL_PATH",
        candidatePathIds=ids or ["lab-path-a", "lab-path-b"],
        controlledScenario=SCENARIO,
        config=config(),
    )


def service_with_lab(tmp_path, status: LabStatus):
    lab = FakeLab(status)
    service = EWPSV2Service(EWPSResearchStore(tmp_path / "binding.sqlite3"), lab)
    real = real_internal("path-real-a")
    controlled = [V2InternalCandidate(public=item) for item in lab.candidates()]
    service._catalog = lambda: [real, *controlled]
    return service, lab, real


def test_controlled_experiment_cannot_be_created_before_lab_and_both_paths_verify(tmp_path):
    service, lab, _real = service_with_lab(tmp_path, lab_status(ready=False, verified_a=False, verified_b=False))
    with pytest.raises(ValueError, match="freshly verified"):
        service.create(controlled_request())
    lab.current_status = lab_status(ready=False, verified_a=True, verified_b=False)
    with pytest.raises(ValueError, match="freshly verified"):
        service.create(controlled_request())


def test_verified_controlled_binding_is_exact_immutable_and_excludes_windows_discovery(tmp_path):
    service, _lab, _real = service_with_lab(tmp_path, lab_status(ready=True))
    session = service.create(controlled_request())
    assert session.source_mode == "CONTROLLED_DUAL_PATH"
    assert session.candidate_path_ids == ["lab-path-a", "lab-path-b"]
    assert [item.source_kind for item in session.candidate_snapshot] == ["controlled_lab", "controlled_lab"]
    assert session.lab_instance_id == INSTANCE_ID
    assert session.initial_verification_status == "VERIFIED"
    resolved = service._resolve_candidates(session)
    assert list(resolved) == ["lab-path-a", "lab-path-b"]
    assert all(item.public.source_kind == "controlled_lab" for item in resolved.values())


def test_controlled_start_rechecks_lab_and_rejects_loss_after_creation(tmp_path):
    service, lab, _real = service_with_lab(tmp_path, lab_status(ready=True))
    session = service.create(controlled_request())
    lab.current_status = lab_status(ready=False, verified_a=False, verified_b=False)
    with pytest.raises(ValueError, match="fresh two-path verification"):
        service.start(session.experiment_id)
    assert service.store.get(session.experiment_id).status == "CREATED"


def test_privacy_safe_fixture_recreates_and_rejects_controlled_name_with_real_ids(tmp_path):
    fixture_path = Path(__file__).parent / "fixtures" / "ewps_v02_source_binding_failure.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    service, _lab, _real = service_with_lab(tmp_path, lab_status(ready=True))
    request = V2ExperimentCreateRequest(
        **fixture["experiment"],
        workloadLabel="Calibration",
        config=config(),
    )
    with pytest.raises(ValueError, match="exactly lab-path-a and lab-path-b"):
        service.create(request)


def test_real_interface_mode_remains_isolated_and_rejects_controlled_scenario(tmp_path):
    service, _lab, _real = service_with_lab(tmp_path, lab_status(ready=True))
    with pytest.raises(ValueError, match="cannot reference"):
        V2ExperimentCreateRequest(
            name="Contradiction",
            workloadLabel="Calibration",
            sourceMode="REAL_INTERFACES",
            candidatePathIds=["path-real-a"],
            controlledScenario=SCENARIO,
            config=config(),
        )
    session = service.create(V2ExperimentCreateRequest(
        name="Real path control",
        workloadLabel="Calibration",
        sourceMode="REAL_INTERFACES",
        candidatePathIds=["path-real-a"],
        config=config(),
    ))
    assert session.source_mode == "REAL_INTERFACES"
    assert session.candidate_snapshot[0].source_kind == "real_interface"
    assert service._resolve_candidates(session)["path-real-a"].legacy is not None


def test_controlled_lab_loss_records_event_and_never_falls_back(tmp_path):
    service, lab, _real = service_with_lab(tmp_path, lab_status(ready=True))
    session = service.create(controlled_request())
    running = service.store.transition(session.experiment_id, "RUNNING")
    service._active_id = running.experiment_id
    service._runtime = V2EngineRuntime(running.config)
    service._candidates = service._resolve_candidates(running)
    lab.binding_current = False
    service.sample_once()
    timeline = service.store.timeline(running.experiment_id)
    point = timeline.decisions[0]
    assert "CONTROLLED_LAB_LOST" in point.events
    assert [item.path_id for item in point.calculations] == ["lab-path-a", "lab-path-b"]
    assert all(item.raw.telemetry_state == "controlled_lab_lost" for item in point.calculations)
    assert all(item.raw.candidate_lifecycle == "PERSISTENTLY_UNAVAILABLE" for item in point.calculations)


def test_candidate_identity_stays_stable_when_discovery_changes_mid_session(tmp_path):
    service, _lab, _real = service_with_lab(tmp_path, lab_status(ready=True))
    session = service.create(controlled_request())
    running = service.store.transition(session.experiment_id, "RUNNING")
    service._active_id = running.experiment_id
    service._runtime = V2EngineRuntime(running.config)
    service._candidates = service._resolve_candidates(running)
    service._catalog = lambda: (_ for _ in ()).throw(AssertionError("discovery must not run during controlled collection"))
    service.sample_once()
    service.sample_once()
    timeline = service.store.timeline(running.experiment_id)
    assert len(timeline.decisions) == 2
    assert all(
        [item.path_id for item in point.calculations] == ["lab-path-a", "lab-path-b"]
        for point in timeline.decisions
    )


def test_export_and_replay_retain_controlled_candidate_provenance(tmp_path, monkeypatch):
    from app import ewps_v2_service as service_module

    service, _lab, _real = service_with_lab(tmp_path, lab_status(ready=True))
    session = service.create(controlled_request())
    running = service.store.transition(session.experiment_id, "RUNNING")
    service._active_id = running.experiment_id
    service._runtime = V2EngineRuntime(running.config)
    service._candidates = service._resolve_candidates(running)
    service.sample_once()
    service.store.transition(running.experiment_id, "COMPLETED")
    replay = service.replay(running.experiment_id)
    assert replay.source_mode == "CONTROLLED_DUAL_PATH"
    assert [item.path_id for item in replay.candidate_snapshot] == ["lab-path-a", "lab-path-b"]
    monkeypatch.setattr(service_module, "get_settings", lambda: type("Settings", (), {"data_dir": tmp_path})())
    content, _media, _path = service.export(running.experiment_id, "json")
    exported = json.loads(content)
    assert exported["experiment"]["sourceMode"] == "CONTROLLED_DUAL_PATH"
    assert exported["experiment"]["labInstanceId"] == INSTANCE_ID
    assert exported["experiment"]["candidatePathIds"] == ["lab-path-a", "lab-path-b"]
