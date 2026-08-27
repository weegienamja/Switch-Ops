"""Persistent, serialized access to the managed switch.

One thread owns the SSH connection for the lifetime of the process. Everything
else - telemetry collectors, diagnostics, write transactions - submits a typed
job and waits for the result. Nothing outside the worker ever touches the
client, which is what makes the following invariants hold by construction
rather than by convention:

    one physical session at a time
    no concurrent use of the channel
    no interleaved commands
    no telemetry inside a configuration transaction

The last one deserves a note. A write transaction is not a sequence of jobs; it
is *one* job whose body performs precheck, backup, execute and verify. Because
the worker runs one job to completion before taking the next, a collector
cannot land between a change and its verification even in principle.

Why persistent at all: measured against the lab Catalyst, opening a session
costs 4.16 s (connect, legacy-SSH negotiation, authenticate, enable, terminal
length 0) against 7.59 s for the thirteen commands of a full observation. A
third of every observation was setup that a persistent session pays once.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from queue import Empty, PriorityQueue
from typing import Any, Callable, Optional

from .config import get_settings
from .errors import (
    CredentialsMissingError,
    DeviceSessionLostError,
    HostKeyChangedError,
    SwitchConnectionError,
    SwitchOpsError,
    is_device_transport_exception,
    session_lost_error,
)
from .switch_client import SwitchClient, get_switch_client


logger = logging.getLogger(__name__)


class JobPriority(IntEnum):
    """Lower runs first.

    A user waiting on a change or a diagnostic must never queue behind routine
    polling, and deep discovery must never starve interactive work.
    """

    TRANSACTION = 0
    DIAGNOSTIC = 1
    FAST = 2
    MEDIUM = 3
    SLOW = 4
    DEEP = 5


# Connection states surfaced to the UI. "stale" exists so the interface can say
# that what it is showing was true a while ago, rather than presenting old
# telemetry as current.
ConnectionState = str  # "offline" | "connecting" | "live" | "stale" | "reconnecting"

OFFLINE: ConnectionState = "offline"
CONNECTING: ConnectionState = "connecting"
LIVE: ConnectionState = "live"
STALE: ConnectionState = "stale"
RECONNECTING: ConnectionState = "reconnecting"


@dataclass(order=True)
class _Job:
    priority: int
    sequence: int
    kind: str = field(compare=False)
    run: Callable[[SwitchClient], Any] = field(compare=False)
    future: "Future[Any]" = field(compare=False)
    submitted_at: float = field(compare=False, default_factory=time.monotonic)


@dataclass
class CommandTiming:
    kind: str
    duration_ms: float
    queue_wait_ms: float
    at: datetime


class SessionMetrics:
    """Small ring of recent job timings, for Settings and for tuning cadence."""

    def __init__(self, capacity: int = 200) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._timings: list[CommandTiming] = []
        self.jobs_run = 0
        self.jobs_failed = 0
        self.reconnects = 0
        self.connected_since: Optional[datetime] = None

    def record(self, timing: CommandTiming, *, failed: bool = False) -> None:
        with self._lock:
            self._timings.append(timing)
            if len(self._timings) > self._capacity:
                del self._timings[: len(self._timings) - self._capacity]
            self.jobs_run += 1
            if failed:
                self.jobs_failed += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            timings = list(self._timings)
        by_kind: dict[str, list[float]] = {}
        for timing in timings:
            by_kind.setdefault(timing.kind, []).append(timing.duration_ms)
        summary = {
            kind: {
                "count": len(values),
                "medianMs": round(sorted(values)[len(values) // 2], 1),
                "maxMs": round(max(values), 1),
            }
            for kind, values in sorted(by_kind.items())
        }
        uptime = None
        if self.connected_since is not None:
            uptime = int((datetime.now(timezone.utc) - self.connected_since).total_seconds())
        return {
            "jobsRun": self.jobs_run,
            "jobsFailed": self.jobs_failed,
            "reconnects": self.reconnects,
            "connectedSeconds": uptime,
            "byKind": summary,
        }


class DeviceSessionManager:
    """Owns the switch connection and runs every device job on one thread."""

    # Bounded exponential backoff. Reconnecting harder than this on an old
    # switch that is genuinely down helps nobody.
    _BACKOFF_START = 2.0
    _BACKOFF_MAX = 60.0
    # A session with nothing to do still needs proof it is alive.
    _IDLE_KEEPALIVE_SECONDS = 45.0

    def __init__(self) -> None:
        self._queue: "PriorityQueue[_Job]" = PriorityQueue()
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._client: Optional[SwitchClient] = None  # worker thread only
        self._state: ConnectionState = OFFLINE
        self._state_lock = threading.Lock()
        self._last_error: Optional[str] = None
        self._last_error_code: Optional[str] = None
        self._last_exception: Optional[SwitchOpsError] = None
        self._backoff = self._BACKOFF_START
        self._next_attempt_at: float = 0.0
        self._last_success: Optional[datetime] = None
        self.metrics = SessionMetrics()
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    # --- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._worker, name="switchops-device", daemon=True
        )
        self._thread.start()
        logger.info("Device session worker started.")

    def stop(self, timeout: float = 6.0) -> None:
        self._stopping.set()
        # Unblock the queue wait.
        self._submit_raw(_Job(
            priority=JobPriority.TRANSACTION,
            sequence=self._next_sequence(),
            kind="shutdown",
            run=lambda _client: None,
            future=Future(),
        ))
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None
        logger.info("Device session worker stopped.")

    # --- state ------------------------------------------------------------

    def _next_sequence(self) -> int:
        with self._sequence_lock:
            self._sequence += 1
            return self._sequence

    def _set_state(self, state: ConnectionState, *, error: Optional[SwitchOpsError] = None) -> None:
        with self._state_lock:
            previous_state = self._state
            previous_error = self._last_error
            previous_code = self._last_error_code
            self._state = state
            if error is not None:
                self._last_error = error.message
                self._last_error_code = error.code
                self._last_exception = error.public_copy()
            elif state == LIVE:
                self._last_error = None
                self._last_error_code = None
                self._last_exception = None
            changed = (
                previous_state != state
                or previous_error != self._last_error
                or previous_code != self._last_error_code
            )
        if changed:
            logger.info("Device session state: %s", state)
            self._notify()

    @property
    def state(self) -> ConnectionState:
        with self._state_lock:
            return self._state

    def add_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(listener)

    def _notify(self) -> None:
        payload = self.status()
        for listener in list(self._listeners):
            try:
                listener(payload)
            except Exception:  # pragma: no cover - listener must not break the worker
                logger.warning("Session listener raised; ignoring.")

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            state = self._state
            error = self._last_error
            code = self._last_error_code
            last_success = self._last_success
        return {
            "state": state,
            "error": error,
            "errorCode": code,
            "queueDepth": self._queue.qsize(),
            "lastSuccessAt": last_success.isoformat() if last_success else None,
            "metrics": self.metrics.snapshot(),
        }

    def restore_last_success(self, observed_at: datetime | None) -> None:
        """Hydrate durable continuity without changing current session state."""
        if observed_at is None:
            return
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        with self._state_lock:
            if self._last_success is None or observed_at > self._last_success:
                self._last_success = observed_at

    # --- submission -------------------------------------------------------

    def _submit_raw(self, job: _Job) -> "Future[Any]":
        self._queue.put(job)
        return job.future

    def submit(
        self,
        kind: str,
        run: Callable[[SwitchClient], Any],
        *,
        priority: JobPriority = JobPriority.DIAGNOSTIC,
    ) -> "Future[Any]":
        """Queue work for the device. The callable runs on the worker thread."""
        if self._stopping.is_set():
            future: "Future[Any]" = Future()
            future.set_exception(SwitchOpsError("SwitchOps is shutting down."))
            return future
        return self._submit_raw(_Job(
            priority=int(priority),
            sequence=self._next_sequence(),
            kind=kind,
            run=run,
            future=Future(),
        ))

    def run_sync(
        self,
        kind: str,
        run: Callable[[SwitchClient], Any],
        *,
        priority: JobPriority = JobPriority.DIAGNOSTIC,
        timeout: float = 180.0,
    ) -> Any:
        """Submit and wait. Used by request handlers."""
        return self.submit(kind, run, priority=priority).result(timeout=timeout)

    def has_pending(self, kind: str) -> bool:
        """Whether a job of this kind is already queued.

        Collectors use this to skip a tick rather than stacking up behind a
        busy device - twenty stale telemetry requests help nobody.
        """
        with self._queue.mutex:
            return any(job.kind == kind for job in self._queue.queue)

    # --- worker -----------------------------------------------------------

    def _ensure_client(self) -> Optional[SwitchClient]:
        """Return a live client, connecting or reconnecting as needed."""
        if self._client is not None and self._client.is_alive():
            return self._client

        if self._client is not None:
            # The transport died underneath us.
            try:
                self._client.close()
            except Exception:  # pragma: no cover
                pass
            self._client = None
            self.metrics.reconnects += 1
            self._set_state(RECONNECTING)

        now = time.monotonic()
        if now < self._next_attempt_at:
            return None

        self._set_state(CONNECTING if self.metrics.reconnects == 0 else RECONNECTING)
        try:
            client = get_switch_client()
        except HostKeyChangedError as exc:
            # Fail closed and stop retrying: a changed host key is a security
            # decision for a human, not something to retry into.
            self._next_attempt_at = now + self._BACKOFF_MAX
            self._set_state(OFFLINE, error=exc)
            logger.error("Host key changed; refusing to reconnect automatically.")
            return None
        except CredentialsMissingError as exc:
            self._next_attempt_at = now + self._BACKOFF_MAX
            self._set_state(OFFLINE, error=exc)
            return None
        except SwitchOpsError as exc:
            self._backoff = min(self._BACKOFF_MAX, self._backoff * 2)
            self._next_attempt_at = now + self._backoff
            self._set_state(OFFLINE, error=exc)
            logger.warning(
                "Device connection failed (%s); next attempt in %.0fs", exc.code, self._backoff
            )
            return None
        except Exception as exc:  # pragma: no cover - defensive
            self._backoff = min(self._BACKOFF_MAX, self._backoff * 2)
            self._next_attempt_at = now + self._backoff
            error = SwitchConnectionError(
                "The Catalyst connection could not be established.",
                safe_detail="The failure did not prove a more specific device-side cause.",
            )
            self._set_state(OFFLINE, error=error)
            logger.error("Unexpected device connection failure (%s).", type(exc).__name__)
            return None

        self._client = client
        self._backoff = self._BACKOFF_START
        self._next_attempt_at = 0.0
        self.metrics.connected_since = datetime.now(timezone.utc)
        self._set_state(LIVE)
        return client

    def _worker(self) -> None:
        last_activity = time.monotonic()
        while not self._stopping.is_set():
            try:
                job = self._queue.get(timeout=1.0)
            except Empty:
                # Idle: prove the session is still usable, or notice it is not.
                if (
                    self._client is not None
                    and time.monotonic() - last_activity > self._IDLE_KEEPALIVE_SECONDS
                ):
                    if not self._client.is_alive():
                        logger.info("Idle keepalive found a dead session.")
                        self._ensure_client()
                    last_activity = time.monotonic()
                continue

            if job.kind == "shutdown":
                job.future.set_result(None)
                break

            queue_wait_ms = (time.monotonic() - job.submitted_at) * 1000
            client = self._ensure_client()
            if client is None:
                with self._state_lock:
                    unavailable = (
                        self._last_exception.public_copy()
                        if self._last_exception is not None
                        else SwitchConnectionError(
                            "The Catalyst session is not available.",
                            safe_detail="No device-side cause was proven.",
                        )
                    )
                job.future.set_exception(unavailable)
                self._queue.task_done()
                continue

            started = time.monotonic()
            failed = False
            try:
                result = job.run(client)
                job.future.set_result(result)
                with self._state_lock:
                    self._last_success = datetime.now(timezone.utc)
            except HostKeyChangedError as exc:
                failed = True
                job.future.set_exception(exc)
                self._drop_client(exc)
            except Exception as exc:
                failed = True
                # A transport-level failure invalidates the session; a parser or
                # logic error does not.
                if is_device_transport_exception(exc):
                    lost = (
                        exc.public_copy()
                        if isinstance(exc, DeviceSessionLostError)
                        else session_lost_error(exc)
                    )
                    job.future.set_exception(lost)
                    self._drop_client(lost)
                else:
                    job.future.set_exception(exc)
                    if self._client is not None and not self._client.is_alive():
                        self._drop_client(None)
            finally:
                duration_ms = (time.monotonic() - started) * 1000
                self.metrics.record(
                    CommandTiming(
                        kind=job.kind,
                        duration_ms=duration_ms,
                        queue_wait_ms=queue_wait_ms,
                        at=datetime.now(timezone.utc),
                    ),
                    failed=failed,
                )
                last_activity = time.monotonic()
                self._queue.task_done()

        # Drain anything still queued so no caller waits forever.
        self._close_client()
        while True:
            try:
                pending = self._queue.get_nowait()
            except Empty:
                break
            if not pending.future.done():
                pending.future.set_exception(SwitchOpsError("SwitchOps is shutting down."))
            self._queue.task_done()

    def _drop_client(self, error: Optional[SwitchOpsError]) -> None:
        had_client = self._client is not None
        self._close_client()
        if had_client:
            self.metrics.reconnects += 1
            self.metrics.connected_since = None
        self._set_state(STALE, error=error)

    def _close_client(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # pragma: no cover
                pass
            self._client = None


_manager: Optional[DeviceSessionManager] = None
_manager_lock = threading.Lock()


def get_device_session() -> DeviceSessionManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = DeviceSessionManager()
        return _manager


def reset_device_session() -> None:
    """Test hook: stop and forget the singleton."""
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.stop()
        _manager = None


def run_on_device(
    kind: str,
    run: Callable[[SwitchClient], Any],
    *,
    priority: JobPriority = JobPriority.DIAGNOSTIC,
    timeout: float = 180.0,
) -> Any:
    """Run one unit of work against the switch and wait for the result."""
    manager = get_device_session()
    manager.start()
    return manager.run_sync(kind, run, priority=priority, timeout=timeout)
