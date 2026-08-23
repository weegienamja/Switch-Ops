"""Persistent device session.

The invariants under test are the ones that make a live console safe on a
single-session device: one connection, one command at a time, nothing
interleaved into a transaction, and no unbounded backlog of stale polling.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future

import pytest

from backend.app import device_session as ds
from backend.app.device_session import (
    LIVE,
    OFFLINE,
    STALE,
    DeviceSessionManager,
    JobPriority,
)
from backend.app.errors import (
    CredentialsMissingError,
    HostKeyChangedError,
    SwitchConnectionError,
)


class FakeClient:
    """Records the order and overlap of every command it is asked to run."""

    def __init__(self, tracker: "Tracker", *, alive: bool = True) -> None:
        self.tracker = tracker
        self._alive = alive
        self.closed = False

    def run(self, symbol: str) -> str:
        with self.tracker.lock:
            self.tracker.concurrent += 1
            self.tracker.max_concurrent = max(
                self.tracker.max_concurrent, self.tracker.concurrent
            )
            self.tracker.order.append(symbol)
        time.sleep(0.01)
        with self.tracker.lock:
            self.tracker.concurrent -= 1
        return f"output:{symbol}"

    def is_alive(self) -> bool:
        return self._alive

    def refresh_prompt(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True
        self._alive = False


class Tracker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.order: list[str] = []
        self.concurrent = 0
        self.max_concurrent = 0
        self.connects = 0


@pytest.fixture
def tracker():
    return Tracker()


@pytest.fixture
def manager(monkeypatch, tracker):
    """A started manager whose connections are fakes."""
    clients: list[FakeClient] = []

    def factory():
        tracker.connects += 1
        client = FakeClient(tracker)
        clients.append(client)
        return client

    monkeypatch.setattr(ds, "get_switch_client", factory)
    instance = DeviceSessionManager()
    instance.start()
    instance.clients = clients  # type: ignore[attr-defined]
    yield instance
    instance.stop()


# --- one session, one command at a time ------------------------------------


def test_only_one_connection_is_ever_opened(manager, tracker):
    for index in range(12):
        manager.submit(f"job{index}", lambda client: client.run("show_x")).result(timeout=10)
    assert tracker.connects == 1


def test_commands_never_overlap_even_under_concurrent_submission(manager, tracker):
    """Twenty callers, one channel."""
    futures = [
        manager.submit("parallel", lambda client: client.run("show_interfaces_status"))
        for _ in range(20)
    ]
    for future in futures:
        future.result(timeout=15)
    assert tracker.max_concurrent == 1, "two commands ran on the channel at once"
    assert len(tracker.order) == 20


def test_a_transaction_cannot_be_interleaved_by_telemetry(manager, tracker):
    """A transaction is one job, so nothing can land inside it."""

    def transaction(client):
        client.run("backup")
        time.sleep(0.05)
        client.run("configure")
        time.sleep(0.05)
        client.run("verify")
        return "done"

    pending = manager.submit("txn", transaction, priority=JobPriority.TRANSACTION)
    # Fire telemetry at it while the transaction is in flight.
    for _ in range(5):
        manager.submit("fast", lambda client: client.run("poll"), priority=JobPriority.FAST)
    pending.result(timeout=15)
    for _ in range(5):
        manager.submit("drain", lambda client: client.run("drain")).result(timeout=10)

    order = tracker.order
    start = order.index("backup")
    end = order.index("verify")
    between = order[start : end + 1]
    assert between == ["backup", "configure", "verify"], f"telemetry interleaved: {between}"


def test_higher_priority_work_overtakes_queued_polling(manager, tracker):
    """A blocker holds the worker while a mixed queue builds behind it."""
    release = threading.Event()

    manager.submit("blocker", lambda client: release.wait(timeout=5), priority=JobPriority.TRANSACTION)
    time.sleep(0.05)  # let the blocker start

    deep = manager.submit("deep", lambda c: c.run("deep"), priority=JobPriority.DEEP)
    slow = manager.submit("slow", lambda c: c.run("slow"), priority=JobPriority.SLOW)
    urgent = manager.submit("urgent", lambda c: c.run("urgent"), priority=JobPriority.TRANSACTION)
    diag = manager.submit("diag", lambda c: c.run("diag"), priority=JobPriority.DIAGNOSTIC)

    release.set()
    for future in (deep, slow, urgent, diag):
        future.result(timeout=15)

    ordered = [item for item in tracker.order if item in {"deep", "slow", "urgent", "diag"}]
    assert ordered.index("urgent") < ordered.index("diag") < ordered.index("slow") < ordered.index("deep")


# --- no unbounded backlog ---------------------------------------------------


def test_pending_work_of_a_kind_is_visible_so_collectors_can_skip(manager):
    release = threading.Event()
    manager.submit("blocker", lambda client: release.wait(timeout=5), priority=JobPriority.TRANSACTION)
    time.sleep(0.05)

    manager.submit("fast-tier", lambda c: c.run("poll"), priority=JobPriority.FAST)
    assert manager.has_pending("fast-tier") is True
    assert manager.has_pending("medium-tier") is False

    release.set()
    time.sleep(0.3)
    assert manager.has_pending("fast-tier") is False


# --- connection lifecycle ---------------------------------------------------


def test_a_dead_transport_is_reconnected_transparently(manager, tracker):
    manager.submit("first", lambda c: c.run("a")).result(timeout=10)
    assert tracker.connects == 1

    # Kill the session underneath the worker.
    manager.clients[-1]._alive = False  # type: ignore[attr-defined]

    manager.submit("second", lambda c: c.run("b")).result(timeout=10)
    assert tracker.connects == 2
    assert manager.metrics.reconnects == 1
    assert manager.state == LIVE


def test_a_failed_connection_backs_off_and_reports_offline(monkeypatch, tracker):
    def failing():
        tracker.connects += 1
        raise SwitchConnectionError("Netmiko connection failed")

    monkeypatch.setattr(ds, "get_switch_client", failing)
    instance = DeviceSessionManager()
    instance.start()
    try:
        with pytest.raises(Exception):
            instance.submit("job", lambda c: c.run("a")).result(timeout=10)
        assert instance.state == OFFLINE
        # The backoff window suppresses an immediate second attempt.
        attempts_after_first = tracker.connects
        with pytest.raises(Exception):
            instance.submit("job", lambda c: c.run("a")).result(timeout=10)
        assert tracker.connects == attempts_after_first
    finally:
        instance.stop()


def test_a_changed_host_key_stops_automatic_reconnection(monkeypatch, tracker):
    """Fail closed: a changed key is a decision for a human."""

    def failing():
        tracker.connects += 1
        raise HostKeyChangedError("The switch SSH host key changed; connection refused.")

    monkeypatch.setattr(ds, "get_switch_client", failing)
    instance = DeviceSessionManager()
    instance.start()
    try:
        for _ in range(4):
            with pytest.raises(Exception):
                instance.submit("job", lambda c: c.run("a")).result(timeout=10)
        assert tracker.connects == 1, "SwitchOps retried into a changed host key"
        assert instance.state == OFFLINE
        assert instance.status()["errorCode"] == "host_key_changed"
    finally:
        instance.stop()


def test_missing_credentials_do_not_spin(monkeypatch, tracker):
    def failing():
        tracker.connects += 1
        raise CredentialsMissingError("No switch credentials configured.")

    monkeypatch.setattr(ds, "get_switch_client", failing)
    instance = DeviceSessionManager()
    instance.start()
    try:
        for _ in range(3):
            with pytest.raises(CredentialsMissingError):
                instance.submit("job", lambda c: c.run("a")).result(timeout=10)
        assert tracker.connects == 1
        assert instance.status()["errorCode"] == "credentials_missing"
    finally:
        instance.stop()


def test_a_transport_failure_marks_the_session_stale(manager, tracker):
    def explode(client):
        client._alive = False
        raise SwitchConnectionError("socket closed")

    with pytest.raises(Exception):
        manager.submit("boom", explode).result(timeout=10)
    assert manager.state == STALE


def test_a_parser_error_does_not_tear_down_a_healthy_session(manager, tracker):
    with pytest.raises(ValueError):
        manager.submit("bad", lambda c: (_ for _ in ()).throw(ValueError("parse"))).result(timeout=10)
    # The session is fine; the job was not.
    assert manager.state == LIVE
    manager.submit("after", lambda c: c.run("still works")).result(timeout=10)
    assert tracker.connects == 1


# --- shutdown ---------------------------------------------------------------


def test_shutdown_closes_the_session_and_releases_waiters(monkeypatch, tracker):
    clients: list[FakeClient] = []

    def factory():
        tracker.connects += 1
        client = FakeClient(tracker)
        clients.append(client)
        return client

    monkeypatch.setattr(ds, "get_switch_client", factory)
    instance = DeviceSessionManager()
    instance.start()
    instance.submit("warm", lambda c: c.run("a")).result(timeout=10)
    instance.stop()

    assert clients[-1].closed is True
    # Nothing submitted after shutdown may block forever.
    future = instance.submit("late", lambda c: c.run("b"))
    with pytest.raises(Exception):
        future.result(timeout=5)


def test_metrics_record_timings_per_kind(manager):
    for _ in range(3):
        manager.submit("fast-tier", lambda c: c.run("poll"), priority=JobPriority.FAST).result(timeout=10)
    snapshot = manager.metrics.snapshot()
    assert snapshot["jobsRun"] >= 3
    assert "fast-tier" in snapshot["byKind"]
    assert snapshot["byKind"]["fast-tier"]["count"] == 3
    assert snapshot["byKind"]["fast-tier"]["medianMs"] >= 0


def test_status_never_contains_a_secret(manager):
    manager.submit("job", lambda c: c.run("a")).result(timeout=10)
    blob = repr(manager.status()).lower()
    for forbidden in ("password", "secret", "credential="):
        assert forbidden not in blob
