"""Loopback-only HTTP surface for the EWPS shadow-mode observatory."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from .ewps_engine import TOPOLOGY_CONFIDENCE, TOPOLOGY_CONFIDENCE_MAPPING_VERSION
from .ewps_models import (
    EWPSConfig,
    EWPS_MODEL_VERSION,
    EWPS_RELEASE_ID,
    ExperimentCreateRequest,
    ExperimentSession,
    ExperimentTimeline,
    ReplayRequest,
    ReplayResult,
    SimulatorRunRequest,
    SimulatorRunResult,
    SimulatorScenario,
)
from .ewps_service import get_ewps_service
from .ewps_simulator import list_scenarios, run_scenario
from .ewps_telemetry import FIXED_PROBE_TARGET_TOKEN


router = APIRouter(prefix="/api/ewps", tags=["EWPS v0.1 research"])


def _not_found(experiment_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"EWPS experiment {experiment_id} was not found.")


@router.get("/meta")
def ewps_meta():
    return {
        "modelVersion": EWPS_MODEL_VERSION,
        "releaseId": EWPS_RELEASE_ID,
        "mode": "SHADOW",
        "changesNetworkState": False,
        "confidenceSemantics": "dimensionless evidence-confidence index; not a calibrated probability",
        "topologyMappingVersion": TOPOLOGY_CONFIDENCE_MAPPING_VERSION,
        "topologyMapping": {
            key: {"score": score, "description": description}
            for key, (score, description) in TOPOLOGY_CONFIDENCE.items()
        },
        "defaultConfig": EWPSConfig().model_dump(by_alias=True),
        "fixedProbeTargetToken": FIXED_PROBE_TARGET_TOKEN,
        "privacyBoundary": (
            "ICMP metadata and aggregate interface counters only; no payloads, URLs, DNS history, "
            "cookies, credentials, or content labels are inspected."
        ),
    }


@router.get("/candidates")
def ewps_candidates():
    return get_ewps_service().candidates()


@router.get("/experiments", response_model=list[ExperimentSession], response_model_by_alias=True)
def ewps_experiments(limit: int = Query(default=50, ge=1, le=500)):
    return get_ewps_service().list(limit)


@router.get("/experiments/current", response_model=ExperimentSession | None, response_model_by_alias=True)
def ewps_current_experiment():
    return get_ewps_service().current()


@router.post("/experiments", response_model=ExperimentSession, response_model_by_alias=True)
def ewps_create_experiment(request: ExperimentCreateRequest):
    try:
        return get_ewps_service().create(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/experiments/{experiment_id}", response_model=ExperimentSession, response_model_by_alias=True)
def ewps_get_experiment(experiment_id: str):
    try:
        return get_ewps_service().get(experiment_id)
    except KeyError:
        raise _not_found(experiment_id) from None


@router.post("/experiments/{experiment_id}/start", response_model=ExperimentSession, response_model_by_alias=True)
def ewps_start_experiment(experiment_id: str):
    try:
        return get_ewps_service().start(experiment_id)
    except KeyError:
        raise _not_found(experiment_id) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/experiments/{experiment_id}/pause", response_model=ExperimentSession, response_model_by_alias=True)
def ewps_pause_experiment(experiment_id: str):
    try:
        return get_ewps_service().pause(experiment_id)
    except KeyError:
        raise _not_found(experiment_id) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/experiments/{experiment_id}/stop")
def ewps_stop_experiment(experiment_id: str):
    try:
        session, summary = get_ewps_service().stop(experiment_id)
        return {"session": session, "summary": summary}
    except KeyError:
        raise _not_found(experiment_id) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/experiments/{experiment_id}/timeline", response_model=ExperimentTimeline, response_model_by_alias=True)
def ewps_timeline(experiment_id: str, limit: int = Query(default=300, ge=1, le=5_000)):
    try:
        timeline = get_ewps_service().timeline(experiment_id)
    except KeyError:
        raise _not_found(experiment_id) from None
    if len(timeline.decisions) > limit:
        timeline.decisions = timeline.decisions[-limit:]
    return timeline


@router.get("/experiments/{experiment_id}/summary")
def ewps_summary(experiment_id: str):
    try:
        return get_ewps_service().summary(experiment_id)
    except KeyError:
        raise _not_found(experiment_id) from None


@router.post(
    "/experiments/{experiment_id}/replay",
    response_model=ReplayResult,
    response_model_by_alias=True,
)
def ewps_replay(experiment_id: str, request: ReplayRequest):
    try:
        return get_ewps_service().replay(experiment_id, request.config)
    except KeyError:
        raise _not_found(experiment_id) from None


@router.get("/experiments/{experiment_id}/export")
def ewps_export(
    experiment_id: str,
    format: str = Query(default="jsonl", pattern="^(jsonl|json|csv)$"),
):
    try:
        content, media_type, path = get_ewps_service().export(experiment_id, format)
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
        },
    )


@router.get("/simulator/scenarios", response_model=list[SimulatorScenario], response_model_by_alias=True)
def ewps_simulator_scenarios():
    return list_scenarios()


@router.post("/simulator/run", response_model=SimulatorRunResult, response_model_by_alias=True)
def ewps_run_simulator(request: SimulatorRunRequest):
    try:
        return run_scenario(request.scenario_id, request.config)
    except KeyError:
        raise HTTPException(status_code=404, detail="EWPS simulator scenario was not found.") from None
