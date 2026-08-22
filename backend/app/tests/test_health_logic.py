from datetime import datetime, timezone

from backend.app.health_logic import evaluate_health
from backend.app.models import (
    CpuStatus,
    EnvironmentStatus,
    InterfaceDelta,
    InterfaceStatus,
    MemoryStatus,
)


NOW = datetime(2026, 8, 22, tzinfo=timezone.utc)


def _health(delta: InterfaceDelta):
    return evaluate_health(
        interfaces=[
            InterfaceStatus(
                port=delta.port,
                name="Lab device",
                status=delta.status_after,
                speed=delta.speed_after,
                duplex=delta.duplex_after,
            )
        ],
        environment=EnvironmentStatus(temperatureC=54, state="GREEN"),
        cpu=CpuStatus(cpu5Sec=8),
        memory=MemoryStatus(processorTotal=100, processorUsed=40),
        deltas=[delta],
        telemetry_complete=True,
        evaluated_at=NOW,
    )


def test_first_sample_with_historic_nonzero_error_is_healthy():
    health = _health(InterfaceDelta(
        port="Gi0/2",
        currentTotalErrors=1,
        counterState="first",
        statusAfter="connected",
        speedAfter="a-1000",
        duplexAfter="a-full",
    ))
    assert health.state == "HEALTHY"
    assert health.reasons[0].code == "no_active_problems"


def test_unchanged_nonzero_error_is_healthy():
    health = _health(InterfaceDelta(
        port="Gi0/2",
        previousTotalErrors=1,
        currentTotalErrors=1,
        errorDelta=0,
        counterState="unchanged",
        statusBefore="connected",
        statusAfter="connected",
        speedBefore="a-1000",
        speedAfter="a-1000",
        duplexBefore="a-full",
        duplexAfter="a-full",
    ))
    assert health.state == "HEALTHY"


def test_error_increase_of_84_is_attention():
    health = _health(InterfaceDelta(
        port="Gi0/2",
        previousTotalErrors=1,
        currentTotalErrors=85,
        errorDelta=84,
        counterState="increased",
        statusBefore="connected",
        statusAfter="connected",
    ))
    assert health.state == "ATTENTION"
    assert health.reasons[0].interface == "Gi0/2"


def test_counter_reset_is_notice_not_error_spike():
    health = _health(InterfaceDelta(
        port="Gi0/2",
        previousTotalErrors=85,
        currentTotalErrors=1,
        counterState="reset",
        statusBefore="connected",
        statusAfter="connected",
    ))
    assert health.state == "NOTICE"
    assert health.reasons[0].code == "interface_counter_reset"


def test_lower_negotiated_speed_is_notice():
    health = _health(InterfaceDelta(
        port="Gi0/4",
        counterState="unchanged",
        errorDelta=0,
        statusBefore="connected",
        statusAfter="connected",
        speedBefore="a-1000",
        speedAfter="a-100",
        duplexBefore="a-full",
        duplexAfter="a-full",
    ))
    assert health.state == "NOTICE"
    assert health.reasons[0].code == "link_speed_reduced"


def test_partial_snapshot_is_notice_without_fabricating_fault():
    health = evaluate_health(
        interfaces=[],
        environment=EnvironmentStatus(),
        cpu=CpuStatus(),
        memory=MemoryStatus(),
        deltas=[],
        telemetry_complete=False,
        evaluated_at=NOW,
    )
    assert health.state == "NOTICE"
    assert health.reasons[0].code == "partial_telemetry"

