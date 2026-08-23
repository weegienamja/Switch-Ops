"""Live network state: tiered collection, an in-memory cache, and a hub that
pushes changes to the UI.

Three ideas hold this together.

**Tiers.** A full observation is thirteen commands and ~1.9 s even on a warm
session. Watching a cable get unplugged needs one command. The collectors are
therefore split by cost and value, with intervals chosen from measured command
durations on the lab Catalyst rather than from guesses:

    fast     5 s   show interfaces status            46 ms   0.9% duty cycle
    medium  20 s   rotating: PoE / errors / env / load
    slow    60 s   MAC table, ARP, neighbours
    deep    manual full observation and reconciliation

**Skip, never queue.** If the device is busy when a tick fires, that tick is
dropped. Twenty stale telemetry requests stacked behind a transaction help
nobody, and the next tick is only seconds away.

**Memory is not history.** Fast samples live in this cache and are never
written to SQLite - a 5 s sample rate would add ~17k rows a day to answer a
question nobody asks. Historical observations stay on their own slower cadence,
and transitions are persisted as events the moment they happen.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .device_session import DeviceSessionManager, JobPriority, get_device_session
from .credential_store import get_credential_store
from .interface_policy import get_interface_policy_store
from .models import InterfaceStatus, PoePort
from .parsers.interfaces import parse_interface_status
from .discovery import inspect_lldp
from .parsers.arp import parse_arp
from .parsers.cdp import parse_cdp
from .parsers.mac_table import parse_mac_table
from .parsers.poe import parse_poe
from .switch_client import SwitchClient
from .tools.read_only import run_and_audit


logger = logging.getLogger(__name__)


# --- tier configuration -----------------------------------------------------
#
# Bounded so a mistyped setting cannot turn SwitchOps into a load generator on
# a 2010-era switch. The lower bounds are roughly 20x the measured command
# duration, leaving the channel overwhelmingly idle.
FAST_BOUNDS = (2.0, 60.0)
MEDIUM_BOUNDS = (10.0, 300.0)
SLOW_BOUNDS = (30.0, 900.0)
HISTORY_BOUNDS = (30.0, 3600.0)

DEFAULT_FAST = 5.0
DEFAULT_MEDIUM = 20.0
DEFAULT_SLOW = 60.0
DEFAULT_HISTORY = 60.0


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    low, high = bounds
    return max(low, min(high, float(value)))


@dataclass
class TierConfig:
    fast_seconds: float = DEFAULT_FAST
    medium_seconds: float = DEFAULT_MEDIUM
    slow_seconds: float = DEFAULT_SLOW
    history_seconds: float = DEFAULT_HISTORY

    def clamped(self) -> "TierConfig":
        return TierConfig(
            fast_seconds=_clamp(self.fast_seconds, FAST_BOUNDS),
            medium_seconds=_clamp(self.medium_seconds, MEDIUM_BOUNDS),
            slow_seconds=_clamp(self.slow_seconds, SLOW_BOUNDS),
            history_seconds=_clamp(self.history_seconds, HISTORY_BOUNDS),
        )


# --- normalised live state --------------------------------------------------


@dataclass
class LiveInterface:
    port: str
    description: str = ""
    status: str = ""          # connected | notconnect | disabled
    admin_state: str = "unknown"
    oper_state: str = "unknown"
    speed: str = ""
    duplex: str = ""
    vlan: str = ""
    poe_state: str = ""
    poe_watts: float = 0.0
    protected: bool = False
    policy_state: str = "UNMANAGED"

    def comparable(self) -> tuple:
        """The fields whose change is worth telling somebody about."""
        return (
            self.status, self.admin_state, self.oper_state,
            self.speed, self.duplex, self.vlan, self.description,
        )


@dataclass
class Freshness:
    """When each class of data was last successfully collected.

    Kept per domain because pretending slow-tier data is as fresh as fast-tier
    data is exactly the dishonesty this release exists to remove.
    """

    fast: Optional[str] = None
    medium: Optional[str] = None
    slow: Optional[str] = None
    deep: Optional[str] = None


class LiveHub:
    """Fan-out of live events to connected UI clients.

    Subscribers are asyncio queues owned by the request that created them; the
    collector thread publishes through ``call_soon_threadsafe`` so nothing
    blocks the event loop. Queues are bounded and drop their oldest entry under
    pressure: a slow client must never stall collection.
    """

    MAX_PENDING = 64

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[tuple[Any, Any]] = []  # (loop, asyncio.Queue)

    def subscribe(self, loop: Any, queue: Any) -> None:
        with self._lock:
            self._subscribers.append((loop, queue))

    def unsubscribe(self, queue: Any) -> None:
        with self._lock:
            self._subscribers = [item for item in self._subscribers if item[1] is not queue]

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        message = {
            "type": event_type,
            "at": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(self._offer, queue, message)
            except RuntimeError:
                # The loop has gone; the request's own cleanup will unsubscribe.
                continue

    @staticmethod
    def _offer(queue: Any, message: dict[str, Any]) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except Exception:  # pragma: no cover - queue raced empty
                pass
        try:
            queue.put_nowait(message)
        except Exception:  # pragma: no cover - defensive
            pass


class LiveState:
    """In-memory view of the network, updated by the collectors."""

    def __init__(self, hub: Optional[LiveHub] = None) -> None:
        self._lock = threading.RLock()
        self.hub = hub or LiveHub()
        self.interfaces: dict[str, LiveInterface] = {}
        self.poe_used_w: float = 0.0
        self.poe_available_w: float = 0.0
        self.freshness = Freshness()
        self.device_id: Optional[str] = None
        # Set when a change transaction is running, so the UI can explain why
        # polling paused rather than looking frozen.
        self.operation_in_progress: Optional[str] = None
        self.lldp: dict[str, Any] = {
            "state": "unknown",
            "supported": False,
            "enabled": None,
            "neighbors": [],
            "detail": "LLDP has not been collected yet.",
        }
        self.topology: Optional[dict[str, Any]] = None
        # Cached deep-observation context used only for read-only Change
        # Assurance impact analysis. It never authorizes an interface.
        self.health: Optional[dict[str, Any]] = None
        self.reconciliation: Optional[dict[str, Any]] = None

    # -- updates ----------------------------------------------------------

    def apply_interfaces(self, parsed: list[InterfaceStatus], *, at: datetime) -> list[dict[str, Any]]:
        """Merge a fast-tier sample and return the transitions it revealed."""
        changes: list[dict[str, Any]] = []
        with self._lock:
            for item in parsed:
                incoming = LiveInterface(
                    port=item.port,
                    description=item.name or "",
                    status=item.status,
                    admin_state="down" if item.status.strip().lower() == "disabled" else "up",
                    oper_state="up" if item.status.strip().lower() == "connected" else "down",
                    speed=item.speed,
                    duplex=item.duplex,
                    vlan=item.vlan,
                    protected=item.protected,
                    policy_state=item.policy_state,
                )
                existing = self.interfaces.get(item.port)
                if existing is not None:
                    # PoE arrives on a different tier; do not lose it here.
                    incoming.poe_state = existing.poe_state
                    incoming.poe_watts = existing.poe_watts
                    if existing.comparable() != incoming.comparable():
                        changes.append({
                            "port": item.port,
                            "before": asdict(existing),
                            "after": asdict(incoming),
                        })
                self.interfaces[item.port] = incoming
            self.freshness.fast = at.isoformat()
        return changes

    def apply_poe(self, poe_ports: list[PoePort], used: float, available: float, *, at: datetime) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        with self._lock:
            for port in poe_ports:
                existing = self.interfaces.get(port.interface)
                if existing is None:
                    continue
                before_state, before_watts = existing.poe_state, existing.poe_watts
                existing.poe_state = port.oper
                existing.poe_watts = port.power_watts
                if before_state != port.oper:
                    changes.append({
                        "port": port.interface,
                        "before": before_state,
                        "after": port.oper,
                        "watts": port.power_watts,
                    })
            self.poe_used_w = used
            self.poe_available_w = available
            self.freshness.medium = at.isoformat()
        return changes

    def mark_fresh(self, tier: str, at: datetime) -> None:
        with self._lock:
            setattr(self.freshness, tier, at.isoformat())

    def apply_lldp(self, status: Any) -> None:
        with self._lock:
            self.lldp = status.model_dump(by_alias=True)

    def apply_topology(self, payload: dict[str, Any]) -> None:
        """Replace the authoritative deep topology and notify SSE clients."""
        with self._lock:
            self.topology = payload
        self.hub.publish("topology_state", payload)

    def apply_assurance_context(
        self,
        *,
        health: dict[str, Any],
        reconciliation: dict[str, Any],
    ) -> None:
        with self._lock:
            self.health = health
            self.reconciliation = reconciliation

    # -- reads ------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "deviceId": self.device_id,
                "interfaces": [asdict(item) for item in self.interfaces.values()],
                "poe": {"usedW": self.poe_used_w, "availableW": self.poe_available_w},
                "freshness": asdict(self.freshness),
                "operationInProgress": self.operation_in_progress,
                "discovery": {"lldp": dict(self.lldp)},
                "topology": dict(self.topology) if self.topology else None,
                "health": dict(self.health) if self.health else None,
                "reconciliation": (
                    dict(self.reconciliation) if self.reconciliation else None
                ),
            }


# --- collectors -------------------------------------------------------------


class LiveCollector:
    """Drives the telemetry tiers against the device worker."""

    def __init__(
        self,
        *,
        state: LiveState,
        session: Optional[DeviceSessionManager] = None,
        config: Optional[TierConfig] = None,
        on_fast_change: Optional[Callable[[list[dict[str, Any]], datetime], None]] = None,
        on_poe_change: Optional[Callable[[list[dict[str, Any]], datetime], None]] = None,
        on_history_tick: Optional[Callable[[datetime], None]] = None,
        on_slow_discovery: Optional[Callable[[dict[str, Any], datetime], None]] = None,
        on_slow_failure: Optional[Callable[[datetime], None]] = None,
    ) -> None:
        self.state = state
        self.session = session or get_device_session()
        self.config = (config or TierConfig()).clamped()
        self.on_fast_change = on_fast_change
        self.on_poe_change = on_poe_change
        self.on_history_tick = on_history_tick
        self.on_slow_discovery = on_slow_discovery
        self.on_slow_failure = on_slow_failure
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._paused = threading.Event()
        # Rotating medium-tier slots. `show processes cpu` costs 147 ms *and*
        # measurably spikes this switch's own CPU to 63%, so it must not run
        # every medium cycle; it earns a slot, not a tick.
        self._medium_slots = ("poe", "errors", "environment", "load")
        self._medium_index = 0
        self.ticks_skipped = 0
        self.ticks_run = 0

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._loop, name="switchops-collectors", daemon=True
        )
        self._thread.start()
        logger.info(
            "Live collectors started (fast %.0fs, medium %.0fs, slow %.0fs).",
            self.config.fast_seconds, self.config.medium_seconds, self.config.slow_seconds,
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stopping.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    def pause(self) -> None:
        """Hold collection while a change transaction runs."""
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def apply_config(self, config: TierConfig) -> TierConfig:
        self.config = config.clamped()
        return self.config

    # -- tiers ------------------------------------------------------------

    def _submit(self, kind: str, run: Callable[[SwitchClient], Any], priority: JobPriority) -> bool:
        """Submit a tick unless one of this kind is already waiting."""
        if self.session.has_pending(kind):
            self.ticks_skipped += 1
            return False
        self.ticks_run += 1
        future = self.session.submit(kind, run, priority=priority)
        future.add_done_callback(lambda f: self._settle(kind, f))
        return True

    def _settle(self, kind: str, future: Any) -> None:
        try:
            future.result()
        except Exception as exc:  # collection failure must not kill the loop
            logger.debug("Live tier %s failed (%s)", kind, type(exc).__name__)
            if kind == "live-slow" and self.on_slow_failure:
                self.on_slow_failure(datetime.now(timezone.utc))

    def collect_fast(self) -> None:
        def run(client: SwitchClient) -> None:
            output = run_and_audit(client, symbol="show_interfaces_status", actor="live-fast")
            parsed = parse_interface_status(output)
            host = get_credential_store().status().get("switch_host")
            get_interface_policy_store().annotate(host, parsed)
            at = datetime.now(timezone.utc)
            changes = self.state.apply_interfaces(parsed, at=at)
            self.state.hub.publish("interface_state", {
                "interfaces": self.state.snapshot()["interfaces"],
                "freshness": asdict(self.state.freshness),
            })
            if changes and self.on_fast_change:
                self.on_fast_change(changes, at)

        self._submit("live-fast", run, JobPriority.FAST)

    def collect_medium(self) -> None:
        slot = self._medium_slots[self._medium_index % len(self._medium_slots)]
        self._medium_index += 1

        def run(client: SwitchClient) -> None:
            at = datetime.now(timezone.utc)
            if slot == "poe":
                poe = parse_poe(run_and_audit(client, symbol="show_power_inline", actor="live-medium"))
                changes = self.state.apply_poe(
                    poe.ports, poe.used_watts, poe.available_watts, at=at
                )
                self.state.hub.publish("poe_state", {
                    "usedW": poe.used_watts,
                    "availableW": poe.available_watts,
                    "interfaces": self.state.snapshot()["interfaces"],
                })
                if changes and self.on_poe_change:
                    self.on_poe_change(changes, at)
            else:
                symbol = {
                    "errors": "show_interfaces_counters_errors",
                    "environment": "show_env_all",
                    "load": "show_processes_cpu",
                }[slot]
                run_and_audit(client, symbol=symbol, actor="live-medium")
                self.state.mark_fresh("medium", at)
            self.state.hub.publish("freshness", asdict(self.state.freshness))

        self._submit(f"live-medium-{slot}", run, JobPriority.MEDIUM)

    def collect_slow(self) -> None:
        def run(client: SwitchClient) -> None:
            mac_output = run_and_audit(
                client, symbol="show_mac_address_table", actor="live-slow"
            )
            arp_output = run_and_audit(client, symbol="show_ip_arp", actor="live-slow")
            cdp_output = run_and_audit(
                client, symbol="show_cdp_neighbors_detail", actor="live-slow"
            )
            lldp_output = run_and_audit(
                client, symbol="show_lldp_neighbors_detail", actor="live-slow"
            )
            lldp = inspect_lldp(
                running_config=None, summary_output="", detail_output=lldp_output
            )
            self.state.apply_lldp(lldp)
            at = datetime.now(timezone.utc)
            self.state.mark_fresh("slow", at)
            if self.on_slow_discovery:
                self.on_slow_discovery(
                    {
                        "macEntries": parse_mac_table(mac_output),
                        "arpEntries": parse_arp(arp_output),
                        "cdpNeighbors": parse_cdp(cdp_output),
                        "lldpNeighbors": lldp.neighbors,
                    },
                    at,
                )
            self.state.hub.publish("discovery_state", {"lldp": self.state.lldp})
            self.state.hub.publish("freshness", asdict(self.state.freshness))

        self._submit("live-slow", run, JobPriority.SLOW)

    # -- loop -------------------------------------------------------------

    def _loop(self) -> None:
        next_fast = next_medium = next_slow = next_history = time.monotonic()
        while not self._stopping.is_set():
            now = time.monotonic()
            if self._paused.is_set():
                # A transaction owns the device; do not compete with it.
                time.sleep(0.25)
                continue
            try:
                if now >= next_fast:
                    self.collect_fast()
                    next_fast = now + self.config.fast_seconds
                if now >= next_medium:
                    self.collect_medium()
                    next_medium = now + self.config.medium_seconds
                if now >= next_slow:
                    self.collect_slow()
                    next_slow = now + self.config.slow_seconds
                if now >= next_history:
                    if self.on_history_tick:
                        self.on_history_tick(datetime.now(timezone.utc))
                    next_history = now + self.config.history_seconds
            except Exception:  # pragma: no cover - the loop must survive
                logger.warning("Collector tick raised; continuing.", exc_info=False)
            self._stopping.wait(0.25)


_live_state: Optional[LiveState] = None
_collector: Optional[LiveCollector] = None
_lock = threading.Lock()


def get_live_state() -> LiveState:
    global _live_state
    with _lock:
        if _live_state is None:
            _live_state = LiveState()
        return _live_state


def get_collector() -> Optional[LiveCollector]:
    return _collector


def set_collector(collector: Optional[LiveCollector]) -> None:
    global _collector
    _collector = collector


def reset_live_state() -> None:
    """Test hook."""
    global _live_state, _collector
    with _lock:
        if _collector is not None:
            _collector.stop()
        _collector = None
        _live_state = None
