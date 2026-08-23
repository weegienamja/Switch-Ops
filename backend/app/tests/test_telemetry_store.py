from datetime import datetime, timedelta, timezone

from backend.app.models import (
    CpuStatus,
    EnvironmentStatus,
    InterfaceErrorCounters,
    InterfaceStatus,
    MacTableEntry,
    MemoryStatus,
    PoePort,
    PoeResponse,
)
from backend.app.telemetry_store import TelemetryStore, calculate_counter_delta


def _interface(status: str = "connected", speed: str = "a-1000") -> InterfaceStatus:
    return InterfaceStatus(
        port="Gi0/4",
        name="Lab access point",
        status=status,
        vlan="1",
        duplex="a-full",
        speed=speed,
        type="10/100/1000BaseTX",
    )


def _record(
    store: TelemetryStore,
    *,
    at: datetime,
    errors: int,
    status: str = "connected",
    speed: str = "a-1000",
    poe: str = "off",
    macs: list[str] | None = None,
):
    return store.record_snapshot(
        device_id="switch-synthetic",
        reachable=True,
        cpu=CpuStatus(cpu5Sec=8),
        memory=MemoryStatus(processorTotal=100, processorUsed=40),
        environment=EnvironmentStatus(temperatureC=45, state="GREEN"),
        poe=PoeResponse(
            availableWatts=124,
            usedWatts=12 if poe != "off" else 0,
            remainingWatts=112 if poe != "off" else 124,
            ports=[PoePort(interface="Gi0/4", oper=poe)],
        ),
        interfaces=[_interface(status=status, speed=speed)],
        errors=[InterfaceErrorCounters(port="Gi0/4", rcvErr=errors, total=errors)],
        mac_entries=[
            MacTableEntry(vlan="1", mac=mac, type="DYNAMIC", port="Gi0/4")
            for mac in (macs or [])
        ],
        observed_at=at,
    )


def test_first_nonzero_counter_is_baseline_not_fault(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    result = _record(store, at=datetime(2026, 8, 22, tzinfo=timezone.utc), errors=1)

    delta = result.interface_deltas[0]
    assert result.history_available is False
    assert delta.counter_state == "first"
    assert delta.error_delta is None
    assert store.recent_events() == []


def test_unchanged_counter_has_zero_delta_and_no_error_event(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    first = datetime(2026, 8, 22, tzinfo=timezone.utc)
    _record(store, at=first, errors=1)
    result = _record(store, at=first + timedelta(minutes=5), errors=1)

    delta = result.interface_deltas[0]
    assert delta.counter_state == "unchanged"
    assert delta.error_delta == 0
    assert not any(event.event_type == "interface_errors_increased" for event in store.recent_events())


def test_increasing_counter_persists_delta_event(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    first = datetime(2026, 8, 22, tzinfo=timezone.utc)
    _record(store, at=first, errors=1)
    result = _record(store, at=first + timedelta(minutes=5), errors=42)

    delta = result.interface_deltas[0]
    assert delta.counter_state == "increased"
    assert delta.error_delta == 41
    event = store.recent_events(event_type="interface_errors_increased")[0]
    assert event.severity == "ATTENTION"
    assert event.metadata["delta"] == 41


def test_reset_is_not_a_negative_or_wrapped_error_delta(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    first = datetime(2026, 8, 22, tzinfo=timezone.utc)
    _record(store, at=first, errors=42)
    result = _record(store, at=first + timedelta(minutes=5), errors=1)

    assert result.interface_deltas[0].counter_state == "reset"
    assert result.interface_deltas[0].error_delta is None
    assert store.recent_events()[0].event_type == "interface_counter_reset"


def test_counter_wraparound_is_handled_explicitly():
    delta, state = calculate_counter_delta((2**32) - 3, 4)
    assert state == "wrapped"
    assert delta == 7


def test_link_poe_and_device_transitions_create_distinct_events(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    first = datetime(2026, 8, 22, tzinfo=timezone.utc)
    _record(store, at=first, errors=0, status="notconnect")
    _record(
        store,
        at=first + timedelta(minutes=5),
        errors=0,
        status="connected",
        poe="on",
        macs=["0200.0000.0003"],
    )

    event_types = {event.event_type for event in store.recent_events()}
    assert {"interface_link_up", "poe_state_changed", "learned_addresses_changed"} <= event_types


def test_history_is_ordered_and_filtered_by_device(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    first = datetime(2026, 8, 22, tzinfo=timezone.utc)
    _record(store, at=first, errors=0)
    _record(store, at=first + timedelta(minutes=5), errors=0)

    history = store.history(device_id="switch-synthetic", since=first - timedelta(seconds=1))
    assert len(history.observations) == 2
    assert history.observations[0].timestamp < history.observations[1].timestamp
    assert history.observations[0].memory_used_pct == 40

