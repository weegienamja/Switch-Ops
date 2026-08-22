"""Live state cache, tier scheduling and the event hub."""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone

import pytest

from backend.app.device_session import DeviceSessionManager, JobPriority
from backend.app.live_state import (
    FAST_BOUNDS,
    HISTORY_BOUNDS,
    MEDIUM_BOUNDS,
    SLOW_BOUNDS,
    LiveCollector,
    LiveHub,
    LiveState,
    TierConfig,
)
from backend.app.models import InterfaceStatus, PoePort


NOW = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)


def iface(port: str, status: str = "connected", **kw) -> InterfaceStatus:
    return InterfaceStatus(
        port=port,
        name=kw.pop("name", ""),
        status=status,
        vlan=kw.pop("vlan", "1"),
        speed=kw.pop("speed", "a-1000" if status == "connected" else "auto"),
        duplex=kw.pop("duplex", "a-full" if status == "connected" else "auto"),
        **kw,
    )


# --- state cache ------------------------------------------------------------


def test_first_sample_establishes_state_without_claiming_transitions():
    state = LiveState()
    changes = state.apply_interfaces([iface("Gi0/1"), iface("Gi0/4", "notconnect")], at=NOW)
    assert changes == [], "the first sample has nothing to compare against"
    assert len(state.snapshot()["interfaces"]) == 2
    assert state.freshness.fast is not None


def test_a_link_going_down_is_reported_as_a_change():
    state = LiveState()
    state.apply_interfaces([iface("Gi0/3")], at=NOW)
    changes = state.apply_interfaces([iface("Gi0/3", "notconnect")], at=NOW)
    assert len(changes) == 1
    assert changes[0]["port"] == "Gi0/3"
    assert changes[0]["before"]["oper_state"] == "up"
    assert changes[0]["after"]["oper_state"] == "down"


def test_an_unchanged_sample_produces_no_change():
    state = LiveState()
    state.apply_interfaces([iface("Gi0/1")], at=NOW)
    for _ in range(10):
        assert state.apply_interfaces([iface("Gi0/1")], at=NOW) == []


def test_an_admin_shutdown_is_distinguished_from_a_lost_link():
    state = LiveState()
    state.apply_interfaces([iface("Gi0/6", "notconnect")], at=NOW)
    changes = state.apply_interfaces([iface("Gi0/6", "disabled")], at=NOW)
    assert changes[0]["after"]["admin_state"] == "down"
    assert changes[0]["before"]["admin_state"] == "up"


def test_a_fast_sample_does_not_erase_poe_from_another_tier():
    """PoE arrives on the medium tier; the fast tier must not wipe it."""
    state = LiveState()
    state.apply_interfaces([iface("Gi0/4")], at=NOW)
    state.apply_poe([PoePort(interface="Gi0/4", oper="on", powerWatts=13.4)], 13.4, 124.0, at=NOW)
    state.apply_interfaces([iface("Gi0/4")], at=NOW)
    live = {item["port"]: item for item in state.snapshot()["interfaces"]}
    assert live["Gi0/4"]["poe_state"] == "on"
    assert live["Gi0/4"]["poe_watts"] == 13.4


def test_poe_transitions_are_reported():
    state = LiveState()
    state.apply_interfaces([iface("Gi0/4")], at=NOW)
    state.apply_poe([PoePort(interface="Gi0/4", oper="off")], 0.0, 124.0, at=NOW)
    changes = state.apply_poe(
        [PoePort(interface="Gi0/4", oper="on", powerWatts=13.4)], 13.4, 124.0, at=NOW
    )
    assert len(changes) == 1
    assert changes[0]["after"] == "on"


def test_freshness_is_tracked_per_domain():
    """Slow data must never be presented as being as fresh as fast data."""
    state = LiveState()
    state.apply_interfaces([iface("Gi0/1")], at=NOW)
    snapshot = state.snapshot()
    assert snapshot["freshness"]["fast"] is not None
    assert snapshot["freshness"]["slow"] is None
    assert snapshot["freshness"]["deep"] is None


# --- tier bounds ------------------------------------------------------------


def test_intervals_are_clamped_into_safe_ranges():
    """A mistyped setting must not turn SwitchOps into a load generator."""
    absurd = TierConfig(
        fast_seconds=0.01, medium_seconds=0.01, slow_seconds=0.01, history_seconds=0.01
    ).clamped()
    assert absurd.fast_seconds == FAST_BOUNDS[0]
    assert absurd.medium_seconds == MEDIUM_BOUNDS[0]
    assert absurd.slow_seconds == SLOW_BOUNDS[0]
    assert absurd.history_seconds == HISTORY_BOUNDS[0]

    lazy = TierConfig(
        fast_seconds=99999, medium_seconds=99999, slow_seconds=99999, history_seconds=99999
    ).clamped()
    assert lazy.fast_seconds == FAST_BOUNDS[1]
    assert lazy.slow_seconds == SLOW_BOUNDS[1]


def test_default_cadence_matches_the_measured_design():
    config = TierConfig().clamped()
    assert (config.fast_seconds, config.medium_seconds, config.slow_seconds) == (5.0, 20.0, 60.0)


# --- hub --------------------------------------------------------------------


def test_hub_delivers_to_every_subscriber():
    hub = LiveHub()
    loop = asyncio.new_event_loop()
    try:
        queues = [asyncio.Queue(maxsize=8) for _ in range(3)]
        for queue in queues:
            hub.subscribe(loop, queue)
        hub.publish("interface_state", {"hello": "world"})
        loop.call_soon(loop.stop)
        loop.run_forever()
        for queue in queues:
            message = queue.get_nowait()
            assert message["type"] == "interface_state"
            assert message["data"] == {"hello": "world"}
    finally:
        loop.close()


def test_a_slow_subscriber_drops_its_oldest_rather_than_stalling_collection():
    hub = LiveHub()
    loop = asyncio.new_event_loop()
    try:
        queue = asyncio.Queue(maxsize=3)
        hub.subscribe(loop, queue)
        for index in range(10):
            hub.publish("interface_state", {"n": index})
        loop.call_soon(loop.stop)
        loop.run_forever()
        assert queue.qsize() == 3
        # The newest survive; the oldest were dropped.
        newest = [queue.get_nowait()["data"]["n"] for _ in range(3)]
        assert newest == [7, 8, 9]
    finally:
        loop.close()


def test_unsubscribing_stops_delivery():
    hub = LiveHub()
    loop = asyncio.new_event_loop()
    try:
        queue = asyncio.Queue(maxsize=4)
        hub.subscribe(loop, queue)
        hub.unsubscribe(queue)
        assert hub.subscriber_count == 0
        hub.publish("interface_state", {})
        loop.call_soon(loop.stop)
        loop.run_forever()
        assert queue.empty()
    finally:
        loop.close()


def test_a_broken_subscriber_cannot_break_collection():
    hub = LiveHub()
    closed = asyncio.new_event_loop()
    closed.close()
    hub.subscribe(closed, asyncio.Queue())
    hub.publish("interface_state", {})  # must not raise


# --- collector scheduling ---------------------------------------------------


class _Client:
    def __init__(self, tracker: list[str]) -> None:
        self.tracker = tracker

    def run(self, symbol: str) -> str:
        self.tracker.append(symbol)
        if symbol == "show_interfaces_status":
            return (
                "Port      Name               Status       Vlan       Duplex  Speed Type\n"
                "Gi0/1     Uplink             connected    1          a-full a-1000 10/100/1000BaseTX\n"
            )
        if symbol == "show_power_inline":
            return "Available:124.0(w)  Used:0.0(w)  Remaining:124.0(w)\n"
        return ""

    def is_alive(self) -> bool:
        return True

    def refresh_prompt(self) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.fixture
def wired(monkeypatch):
    from backend.app import device_session as ds

    commands: list[str] = []
    monkeypatch.setattr(ds, "get_switch_client", lambda: _Client(commands))
    session = DeviceSessionManager()
    session.start()
    state = LiveState()
    collector = LiveCollector(state=state, session=session, config=TierConfig())
    yield collector, session, state, commands
    collector.stop()
    session.stop()


def test_the_fast_tier_reads_only_one_command(wired):
    collector, _session, _state, commands = wired
    collector.collect_fast()
    time.sleep(0.4)
    assert commands == ["show_interfaces_status"], (
        "the fast tier must stay one command; anything else changes its cost profile"
    )


def test_a_tick_is_skipped_rather_than_queued_when_the_device_is_busy(wired):
    """Twenty stale telemetry requests help nobody."""
    collector, session, _state, _commands = wired
    release = threading.Event()
    session.submit("blocker", lambda c: release.wait(timeout=5), priority=JobPriority.TRANSACTION)
    time.sleep(0.05)

    accepted = [collector._submit("live-fast", lambda c: c.run("show_interfaces_status"), JobPriority.FAST) for _ in range(6)]
    release.set()
    time.sleep(0.5)

    assert accepted.count(True) == 1, "only the first tick should have been queued"
    assert collector.ticks_skipped == 5


def test_pausing_stops_collection_during_a_transaction(wired):
    collector, _session, _state, commands = wired
    collector.pause()
    collector.start()
    time.sleep(0.6)
    assert commands == [], "collectors ran while a transaction held the device"
    collector.resume()
    time.sleep(0.6)
    assert commands, "collectors did not resume"


def test_the_medium_tier_rotates_rather_than_running_everything(wired):
    """`show processes cpu` costs 147 ms and spikes the switch's own CPU to
    63%, so it earns a slot in the rotation rather than a tick."""
    collector, _session, _state, commands = wired
    for _ in range(4):
        collector.collect_medium()
        time.sleep(0.25)
    assert "show_power_inline" in commands
    assert "show_processes_cpu" in commands
    # One command per medium tick, not four.
    assert len(commands) == 4


def test_a_failing_tier_does_not_stop_the_loop(wired):
    collector, session, _state, _commands = wired

    def explode(_client):
        raise RuntimeError("collection failed")

    collector._submit("live-fast", explode, JobPriority.FAST)
    time.sleep(0.3)
    # The collector is still usable.
    assert collector._submit("live-fast", lambda c: c.run("show_interfaces_status"), JobPriority.FAST)
