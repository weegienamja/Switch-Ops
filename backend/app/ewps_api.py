"""Loopback-only API for the versioned EWPS v0.2 research line."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from .ewps_engine import TOPOLOGY_CONFIDENCE, TOPOLOGY_CONFIDENCE_MAPPING_VERSION
from .ewps_telemetry import FIXED_PROBE_TARGET_TOKEN
from .ewps_v2_models import (
    EWPS_V2_MODEL_VERSION,
    EWPS_V2_RELEASE_ID,
    EWPSV2Config,
    ExportSaveRequest,
    LabProfileRequest,
    LabScenarioRequest,
    V2ExperimentCreateRequest,
    V2SimulatorRunRequest,
    VersionedReplayRequest,
)
from .ewps_v2_service import get_ewps_v2_service
from .ewps_v2_simulator import list_v2_scenarios, run_v2_scenario


router = APIRouter(prefix="/api/ewps", tags=["EWPS v0.2 research"])


def _not_found(experiment_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"EWPS experiment {experiment_id} was not found.")


@router.get("/meta")
def ewps_meta():
    return {
        "modelVersion": EWPS_V2_MODEL_VERSION,
        "releaseId": EWPS_V2_RELEASE_ID,
        "mode": "SHADOW",
        "modeLabel": "SHADOW MODE — RECOMMENDATIONS ONLY",
        "changesNetworkState": False,
        "confidenceSemantics": (
            "performance evidence confidence and structural topology confidence are separate "
            "dimensionless heuristic indices; neither is a calibrated probability"
        ),
        "topologyMappingVersion": TOPOLOGY_CONFIDENCE_MAPPING_VERSION,
        "topologyMapping": {
            key: {"score": score, "description": description}
            for key, (score, description) in TOPOLOGY_CONFIDENCE.items()
        },
        "defaultConfig": EWPSV2Config().model_dump(by_alias=True),
        "sampleIntervalSemantics": (
            "non-overlapping measurement-cycle start-to-start cadence; overruns start the next "
            "cycle as soon as the current collector is safe"
        ),
        "fixedProbeTargetToken": FIXED_PROBE_TARGET_TOKEN,
        "privacyBoundary": (
            "ICMP outcomes and aggregate interface counters only; no payloads, URLs, DNS history, "
            "cookies, credentials, or content labels are inspected."
        ),
        "compatibility": {
            "v01Replay": True,
            "v01SemanticsPreserved": True,
            "historicalRowsReinterpreted": False,
            "cadenceInstrumentationAdditive": True,
            "scenarioPhaseProvenanceAdditive": True,
        },
    }


@router.get("/candidates")
def ewps_candidates():
    return get_ewps_v2_service().candidates()


@router.get("/experiments")
def ewps_experiments(limit: int = Query(default=50, ge=1, le=500)):
    return get_ewps_v2_service().list(limit)


@router.get("/experiments/current")
def ewps_current_experiment():
    return get_ewps_v2_service().current()


@router.post("/experiments")
def ewps_create_experiment(request: V2ExperimentCreateRequest):
    try:
        return get_ewps_v2_service().create(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/experiments/{experiment_id}")
def ewps_get_experiment(experiment_id: str):
    try:
        return get_ewps_v2_service().get(experiment_id)
    except KeyError:
        raise _not_found(experiment_id) from None


@router.post("/experiments/{experiment_id}/start")
def ewps_start_experiment(experiment_id: str):
    try:
        return get_ewps_v2_service().start(experiment_id)
    except KeyError:
        raise _not_found(experiment_id) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/experiments/{experiment_id}/pause")
def ewps_pause_experiment(experiment_id: str):
    try:
        return get_ewps_v2_service().pause(experiment_id)
    except KeyError:
        raise _not_found(experiment_id) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/experiments/{experiment_id}/stop")
def ewps_stop_experiment(experiment_id: str):
    try:
        session, summary = get_ewps_v2_service().stop(experiment_id)
        return {"session": session, "summary": summary}
    except KeyError:
        raise _not_found(experiment_id) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/experiments/{experiment_id}/timeline")
def ewps_timeline(experiment_id: str, limit: int = Query(default=300, ge=1, le=5_000)):
    try:
        timeline = get_ewps_v2_service().timeline(experiment_id)
    except KeyError:
        raise _not_found(experiment_id) from None
    if len(timeline.decisions) > limit:
        timeline.decisions = timeline.decisions[-limit:]
    return timeline


@router.get("/experiments/{experiment_id}/summary")
def ewps_summary(experiment_id: str):
    try:
        return get_ewps_v2_service().summary(experiment_id)
    except KeyError:
        raise _not_found(experiment_id) from None


@router.post("/experiments/{experiment_id}/replay")
def ewps_replay(experiment_id: str, request: VersionedReplayRequest):
    try:
        return get_ewps_v2_service().replay(experiment_id, request.config)
    except KeyError:
        raise _not_found(experiment_id) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/experiments/{experiment_id}/export")
def ewps_export(
    experiment_id: str,
    format: str = Query(default="jsonl", pattern="^(jsonl|json|csv)$"),
):
    """Legacy download-compatible endpoint; the backend also saves locally."""
    try:
        content, media_type, path = get_ewps_v2_service().export(experiment_id, format)
    except KeyError:
        raise _not_found(experiment_id) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{path.name}"',
            "Cache-Control": "no-store",
            "X-EWPS-Export-Path": str(path),
        },
    )


@router.post("/experiments/{experiment_id}/exports")
def ewps_save_export(experiment_id: str, request: ExportSaveRequest):
    try:
        return get_ewps_v2_service().save_export(experiment_id, request.format)
    except KeyError:
        raise _not_found(experiment_id) from None
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"EWPS export failed: {exc}") from None


@router.get("/simulator/scenarios")
def ewps_simulator_scenarios():
    return list_v2_scenarios()


@router.post("/simulator/run")
def ewps_run_simulator(request: V2SimulatorRunRequest):
    try:
        return run_v2_scenario(request.scenario_id, request.config)
    except KeyError:
        raise HTTPException(status_code=404, detail="EWPS v0.2 simulator scenario was not found.") from None


@router.get("/lab/status")
def ewps_lab_status():
    return get_ewps_v2_service().lab_status()


@router.post("/lab/prerequisites")
def ewps_lab_prerequisites():
    return get_ewps_v2_service().lab_prerequisites()


@router.post("/lab/create")
def ewps_lab_create():
    return get_ewps_v2_service().lab_create()


@router.post("/lab/verify")
def ewps_lab_verify():
    return get_ewps_v2_service().lab_verify()


@router.post("/lab/teardown")
def ewps_lab_teardown():
    try:
        return get_ewps_v2_service().lab_teardown()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/lab/profile")
def ewps_lab_profile(request: LabProfileRequest):
    try:
        return get_ewps_v2_service().lab_profile(request.path_id, request.profile)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/lab/scenario/prepare")
def ewps_lab_prepare_scenario(request: LabScenarioRequest):
    try:
        return get_ewps_v2_service().lab_prepare_scenario(request.scenario_id)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/lab/scenario/advance")
def ewps_lab_advance_scenario():
    try:
        return get_ewps_v2_service().lab_advance_scenario()
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
