"""Transactional port operations.

A configuration change is never "send the command and hope". Every operation
runs the same pipeline on the device worker, as a single job so nothing can be
interleaved into it:

    precheck -> backup -> execute -> classify IOS response -> verify -> audit
                                                                 |
                                                            rollback on failure

Three rules the pipeline exists to enforce:

**SSH success is not command success.** Netmiko returning without raising only
means bytes went down the wire. IOS reports rejection in the output text, so
the response is classified before anything is called successful.

**Success means the intended state was observed.** After executing, the switch
is asked what the interface looks like now, and the operation only succeeds if
the answer matches the intent. Note that enabling a port with no cable in it
correctly leaves it notconnect - the postcondition is the *administrative*
state, not the link.

**Writes need independent gates.** A persisted local opt-in, an explicit
device/interface policy, and an ephemeral session unlock must all agree. The
lock starts engaged on every launch and is never persisted, so a restart is
always safe.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .audit_store import get_audit_store
from .command_registry import (
    sanitize_description,
)
from .credential_store import get_credential_store
from .errors import (
    CommandNotAllowedError,
    SwitchOpsError,
    WriteActionsDisabledError,
)
from .models import (
    InterfaceStatus,
    OperationKind,
    OperationResult,
    OperationStage,
)
from .interface_policy import get_interface_policy_store
from .parsers.interfaces import parse_interface_status
from .parsers.poe import parse_poe
from .switch_client import SwitchClient
from .tools.backup import backup_running_config
from .tools.read_only import run_and_audit


logger = logging.getLogger(__name__)


# --- write lock -------------------------------------------------------------


class WriteLock:
    """Ephemeral, per-process authorisation to change the device.

    Deliberately not persisted: SwitchOps starts locked every single launch, so
    forgetting to re-lock cannot carry over into the next session.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._unlocked = False
        self._unlocked_at: Optional[datetime] = None

    def unlock(self) -> None:
        if not get_interface_policy_store().controlled_writes_enabled():
            raise WriteActionsDisabledError(
                "Controlled writes are disabled for this installation."
            )
        with self._lock:
            self._unlocked = True
            self._unlocked_at = datetime.now(timezone.utc)
        logger.info("Device control unlocked for this session.")

    def lock(self) -> None:
        with self._lock:
            self._unlocked = False
            self._unlocked_at = None
        logger.info("Device control locked.")

    @property
    def unlocked(self) -> bool:
        with self._lock:
            return (
                self._unlocked
                and get_interface_policy_store().controlled_writes_enabled()
            )

    def require_unlocked(self) -> None:
        if not get_interface_policy_store().controlled_writes_enabled():
            raise WriteActionsDisabledError(
                "Controlled writes are disabled for this installation."
            )
        if not self.unlocked:
            raise WriteActionsDisabledError(
                "Device control is locked. Unlock controlled writes for this session first."
            )

    @contextmanager
    def operation_guard(self):
        """Keep the ephemeral session approval stable for one transaction."""
        with self._lock:
            if (
                not self._unlocked
                or not get_interface_policy_store().controlled_writes_enabled()
            ):
                raise WriteActionsDisabledError(
                    "Device control is locked. Unlock controlled writes for this session first."
                )
            yield

    def status(self) -> dict[str, Any]:
        return {
            "capability": get_interface_policy_store().controlled_writes_enabled(),
            "unlocked": self.unlocked,
            "unlockedAt": self._unlocked_at.isoformat() if self._unlocked_at else None,
        }


_write_lock = WriteLock()


def get_write_lock() -> WriteLock:
    return _write_lock


# --- operation catalogue ----------------------------------------------------


@dataclass(frozen=True)
class OperationSpec:
    kind: OperationKind
    label: str
    #: Config lines, with {iface} and {value} substituted from validated input.
    config_lines: tuple[str, ...]
    #: True when the inverse operation is known and rollback is possible.
    reversible: bool
    needs_value: bool = False


OPERATIONS: dict[str, OperationSpec] = {
    "admin_up": OperationSpec(
        kind="admin_up",
        label="Enable interface",
        config_lines=("interface {iface}", "no shutdown"),
        reversible=True,
    ),
    "admin_down": OperationSpec(
        kind="admin_down",
        label="Disable interface",
        config_lines=("interface {iface}", "shutdown"),
        reversible=True,
    ),
    "poe_auto": OperationSpec(
        kind="poe_auto",
        label="Enable Power over Ethernet",
        config_lines=("interface {iface}", "power inline auto"),
        reversible=True,
    ),
    "poe_never": OperationSpec(
        kind="poe_never",
        label="Disable Power over Ethernet",
        config_lines=("interface {iface}", "power inline never"),
        reversible=True,
    ),
    "set_description": OperationSpec(
        kind="set_description",
        label="Set interface description",
        config_lines=("interface {iface}", "description {value}"),
        reversible=True,
        needs_value=True,
    ),
}

# --- IOS response classification --------------------------------------------

# A returned SSH call proves bytes moved, nothing more. IOS reports refusal in
# the output text.
_IOS_ERROR = re.compile(
    r"%\s*(?:"
    r"Invalid input|Incomplete command|Ambiguous command|Unrecognized command|"
    r"Authorization failed|Permission denied|Command (?:authorization )?failed|"
    r"Error|Bad mask|Cannot|Not enough|Unable to"
    r")",
    re.IGNORECASE,
)


def classify_ios_response(output: str) -> Optional[str]:
    """Return the offending line when IOS rejected something, else None."""
    for line in (output or "").splitlines():
        if _IOS_ERROR.search(line):
            return line.strip()
    return None


# --- the transaction --------------------------------------------------------


@dataclass
class _Stages:
    """Progress record, streamed to the UI as the transaction proceeds."""

    stages: list[OperationStage] = field(default_factory=list)
    on_progress: Optional[Callable[[list[OperationStage]], None]] = None

    def begin(self, name: str) -> None:
        self.stages.append(OperationStage(name=name, status="running", detail=""))
        self._emit()

    def finish(self, status: str, detail: str = "") -> None:
        if self.stages:
            self.stages[-1].status = status  # type: ignore[assignment]
            self.stages[-1].detail = detail
        self._emit()

    def skip(self, name: str, detail: str) -> None:
        self.stages.append(OperationStage(name=name, status="skipped", detail=detail))
        self._emit()

    def _emit(self) -> None:
        if self.on_progress:
            try:
                self.on_progress(list(self.stages))
            except Exception:  # pragma: no cover - progress must not break the change
                logger.debug("Progress listener raised; ignoring.")


def assert_interface_operable(interface: str) -> str:
    """Require explicit OPERABLE state for the currently configured device."""
    host = get_credential_store().status().get("switch_host")
    return get_interface_policy_store().assert_operable(host, interface)


@dataclass(frozen=True)
class _InterfaceConfig:
    """The exact reversible properties from one running-config interface block."""

    description: str
    shutdown: bool
    poe_admin: str


def _interface_config(config: str, canonical: str) -> Optional[_InterfaceConfig]:
    """Extract only the bounded fields the operation engine is allowed to restore."""
    block_match = re.search(
        rf"^interface\s+{re.escape(canonical)}\s*$\n(?P<body>.*?)(?=^!\s*$|^interface\s+|\Z)",
        config,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if block_match is None:
        return None
    body = block_match.group("body")
    description = ""
    shutdown = False
    poe_admin = "auto"  # IOS default when no explicit power-inline line exists.
    for raw_line in body.splitlines():
        line = raw_line.strip()
        lower = line.lower()
        if lower.startswith("description "):
            description = line[len("description ") :]
        elif lower == "shutdown":
            shutdown = True
        elif lower == "no shutdown":
            shutdown = False
        elif lower.startswith("power inline "):
            poe_admin = lower[len("power inline ") :].split()[0]
    return _InterfaceConfig(description=description, shutdown=shutdown, poe_admin=poe_admin)


def _rollback_commands(
    kind: OperationKind,
    canonical: str,
    before_config: _InterfaceConfig,
) -> Optional[list[str]]:
    """Build a fixed, property-specific restore sequence from observed state.

    No arbitrary configuration line is replayed from the backup. If the old
    value cannot be represented by the small operation vocabulary, the change
    is refused before execution instead of pretending rollback is possible.
    """
    if kind in {"admin_up", "admin_down"}:
        restore = "shutdown" if before_config.shutdown else "no shutdown"
    elif kind in {"poe_auto", "poe_never"}:
        if before_config.poe_admin not in {"auto", "never"}:
            return None
        restore = f"power inline {before_config.poe_admin}"
    elif kind == "set_description":
        if not before_config.description:
            restore = "no description"
        else:
            try:
                old_description = sanitize_description(before_config.description)
            except SwitchOpsError:
                return None
            restore = f"description {old_description}"
    else:  # pragma: no cover - OperationKind is exhaustive
        return None
    return ["configure terminal", f"interface {canonical}", restore, "end"]


def _rollback_verified(
    kind: OperationKind,
    before_config: _InterfaceConfig,
    restored_interface: Optional[InterfaceStatus],
    restored_poe: Optional[str],
    restored_config: Optional[_InterfaceConfig],
) -> bool:
    if restored_interface is None or restored_config is None:
        return False
    if kind in {"admin_up", "admin_down"}:
        return restored_config.shutdown == before_config.shutdown
    if kind in {"poe_auto", "poe_never"}:
        return (
            (restored_poe or "").strip().lower() == before_config.poe_admin
            and restored_config.poe_admin == before_config.poe_admin
        )
    if kind == "set_description":
        return restored_config.description == before_config.description
    return False


def _read_interface(client: SwitchClient, canonical: str, actor: str) -> Optional[InterfaceStatus]:
    short = canonical.replace("GigabitEthernet", "Gi")
    output = run_and_audit(client, symbol="show_interfaces_status", actor=actor)
    for item in parse_interface_status(output):
        if item.port.lower() == short.lower():
            return item
    return None


def _read_poe_state(client: SwitchClient, canonical: str, actor: str) -> Optional[str]:
    short = canonical.replace("GigabitEthernet", "Gi")
    poe = parse_poe(run_and_audit(client, symbol="show_power_inline", actor=actor))
    for port in poe.ports:
        if port.interface.lower() == short.lower():
            return port.admin
    return None


def _postcondition_met(
    kind: str,
    interface: Optional[InterfaceStatus],
    poe_admin: Optional[str],
    value: Optional[str],
    config_state: Optional[_InterfaceConfig] = None,
) -> tuple[bool, str]:
    """Did the device end up in the state the operation intended?"""
    if interface is None:
        return False, "The switch no longer reports a status row for this interface."
    status = interface.status.strip().lower()

    if kind == "admin_down":
        ok = status == "disabled"
        return ok, (
            "The interface reports administratively disabled."
            if ok
            else f"Expected the interface to be disabled; it reports {status!r}."
        )
    if kind == "admin_up":
        ok = status != "disabled"
        # An enabled port with nothing plugged in is still a successful enable.
        detail = (
            "The interface is administratively enabled."
            + (" It has no link yet, which is expected with nothing attached."
               if status == "notconnect" else "")
        )
        return ok, detail if ok else "The interface still reports administratively disabled."
    if kind in {"poe_auto", "poe_never"}:
        want = "auto" if kind == "poe_auto" else "never"
        ok = (poe_admin or "").strip().lower() == want
        return ok, (
            f"Power inline administrative policy is {want}."
            if ok
            else f"Expected power inline {want}; the switch reports {poe_admin or 'nothing'}."
        )
    if kind == "set_description":
        observed = config_state.description if config_state is not None else (interface.name or "")
        ok = observed.strip() == (value or "").strip()
        return ok, (
            f"The interface description is now {observed!r}."
            if ok
            else f"Expected description {value!r}; the switch reports {observed!r}."
        )
    return False, "Unknown operation."


def run_operation(
    client: SwitchClient,
    *,
    kind: OperationKind,
    interface: str,
    value: Optional[str] = None,
    actor: str = "operator",
    on_progress: Optional[Callable[[list[OperationStage]], None]] = None,
    backup_is_fresh: bool = False,
) -> OperationResult:
    """Hold every write gate stable while one bounded transaction runs."""
    host = get_credential_store().status().get("switch_host")
    with get_write_lock().operation_guard():
        with get_interface_policy_store().operation_guard(host, interface):
            return _run_operation_authorized(
                client,
                kind=kind,
                interface=interface,
                value=value,
                actor=actor,
                on_progress=on_progress,
                backup_is_fresh=backup_is_fresh,
            )


def _run_operation_authorized(
    client: SwitchClient,
    *,
    kind: OperationKind,
    interface: str,
    value: Optional[str] = None,
    actor: str = "operator",
    on_progress: Optional[Callable[[list[OperationStage]], None]] = None,
    backup_is_fresh: bool = False,
) -> OperationResult:
    """Run one bounded configuration change, end to end.

    Called as a single device job, so no telemetry can land inside it.
    """
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    progress = _Stages(on_progress=on_progress)
    spec = OPERATIONS.get(kind)
    operation_id = f"op-{uuid.uuid4().hex}"

    def result(
        status: str,
        *,
        before: Optional[str] = None,
        after: Optional[str] = None,
        commands: Optional[list[str]] = None,
        detail: str = "",
        rolled_back: Optional[bool] = None,
        backup_path: Optional[str] = None,
        requires_save: bool = False,
    ) -> OperationResult:
        duration = int((time.monotonic() - started) * 1000)
        progress.begin("audit")
        try:
            get_audit_store().record(
                actor=actor,
                action=f"operation:{kind}",
                commands=commands or [],
                success=status == "success",
                duration_ms=duration,
                before_state=before,
                after_state=after,
                error_type=None if status == "success" else status,
                error_message=detail or None,
            )
            progress.finish("ok", "Recorded in the audit trail.")
        except Exception:
            # A local audit storage fault must be visible, but it cannot rewrite
            # what physically happened on the switch.
            logger.exception("Could not record operation %s in the audit trail", operation_id)
            progress.finish("failed", "The operation completed, but its audit record failed.")
        outcome = OperationResult(
            operationId=operation_id,
            kind=kind,
            interface=interface,
            status=status,  # type: ignore[arg-type]
            detail=detail,
            stages=progress.stages,
            beforeState=before,
            afterState=after,
            commands=commands or [],
            durationMs=duration,
            rolledBack=rolled_back,
            backupPath=backup_path,
            requiresSave=requires_save,
            at=now,
        )
        return outcome

    # -- precheck ---------------------------------------------------------
    progress.begin("precheck")
    if spec is None:  # pragma: no cover - rejected by OperationKind validation
        progress.finish("failed", f"{kind!r} is not a supported operation.")
        raise CommandNotAllowedError(f"{kind!r} is not a supported operation.")
    try:
        canonical = assert_interface_operable(interface)
    except SwitchOpsError as exc:
        progress.finish("failed", exc.message)
        return result("blocked", detail=exc.message)

    safe_value: Optional[str] = None
    if spec.needs_value:
        try:
            safe_value = sanitize_description(value or "")
        except SwitchOpsError as exc:
            progress.finish("failed", exc.message)
            return result("blocked", detail=exc.message)

    try:
        before_interface = _read_interface(client, canonical, actor)
        before_poe = (
            _read_poe_state(client, canonical, actor) if kind.startswith("poe_") else None
        )
    except Exception as exc:
        progress.finish("failed", f"Could not read the interface state ({type(exc).__name__}).")
        return result(
            "blocked",
            detail="The change was not attempted because its original state could not be read.",
        )
    if before_interface is None:
        progress.finish("failed", f"{canonical} is not present on this switch.")
        return result("blocked", detail=f"{canonical} is not present on this switch.")
    try:
        running_config = run_and_audit(
            client, symbol="show_running_config", actor=actor
        )
    except Exception as exc:
        progress.finish("failed", f"Could not read the running configuration ({type(exc).__name__}).")
        return result(
            "blocked",
            detail="The change was not attempted because its original state could not be captured.",
        )
    before_config = _interface_config(running_config, canonical)
    if before_config is None:
        progress.finish("failed", f"{canonical} has no running-configuration block.")
        return result(
            "blocked",
            detail="The change was not attempted because exact rollback state is unavailable.",
        )
    rollback_commands = _rollback_commands(kind, canonical, before_config)
    if rollback_commands is None:
        progress.finish("failed", "The existing value cannot be restored by the bounded operation engine.")
        return result(
            "blocked",
            detail="The change was not attempted because exact bounded rollback is unavailable.",
        )
    before_state = (
        f"status={before_interface.status} description={before_interface.name!r}"
        + (f" poe={before_poe}" if before_poe is not None else "")
    )
    progress.finish("ok", f"{canonical} is operable. Current state: {before_state}.")

    already_met, already_detail = _postcondition_met(
        kind, before_interface, before_poe, safe_value, before_config
    )
    if already_met:
        progress.skip("backup", "No backup is needed because the intended state already exists.")
        progress.skip("execute", "No command was sent; the operation is already satisfied.")
        progress.begin("verify")
        progress.finish("ok", already_detail)
        return result(
            "success",
            before=before_state,
            after=before_state,
            detail=f"No change was needed. {already_detail}",
            requires_save=False,
        )

    # -- backup -----------------------------------------------------------
    backup_path: Optional[str] = None
    if backup_is_fresh:
        progress.skip("backup", "A backup from this transaction window is already on disk.")
    else:
        progress.begin("backup")
        try:
            backup = backup_running_config(client, actor=actor, config_text=running_config)
            backup_path = backup.path
            progress.finish("ok", f"Running configuration saved to {backup.filename}.")
        except Exception as exc:
            progress.finish("failed", f"Backup failed ({type(exc).__name__}).")
            return result(
                "blocked",
                before=before_state,
                detail="The change was not attempted because the configuration backup failed.",
            )

    # -- execute ----------------------------------------------------------
    progress.begin("execute")
    commands = ["configure terminal"]
    for line in spec.config_lines:
        rendered = line.replace("{iface}", canonical)
        if "{value}" in rendered:
            rendered = rendered.replace("{value}", safe_value or "")
        commands.append(rendered)
    commands.append("end")

    try:
        output = client.run_raw_action(commands)
    except Exception as exc:
        progress.finish("failed", f"The switch session failed during execution ({type(exc).__name__}).")
        return result(
            "failed",
            before=before_state,
            commands=commands,
            backup_path=backup_path,
            detail="The change could not be sent. Verify the interface state before retrying.",
            requires_save=True,
        )

    rejection = classify_ios_response(output)
    if rejection is not None:
        progress.finish("failed", f"IOS rejected the command: {rejection}")
        return result(
            "failed",
            before=before_state,
            commands=commands,
            backup_path=backup_path,
            detail=f"The switch rejected the configuration: {rejection}",
        )
    progress.finish("ok", "Configuration accepted by the device.")

    # -- verify -----------------------------------------------------------
    progress.begin("verify")
    try:
        after_interface = _read_interface(client, canonical, actor)
        after_poe = (
            _read_poe_state(client, canonical, actor) if kind.startswith("poe_") else None
        )
        after_config = (
            _interface_config(
                run_and_audit(client, symbol="show_running_config", actor=actor), canonical
            )
            if kind == "set_description"
            else None
        )
        met, detail = _postcondition_met(
            kind, after_interface, after_poe, safe_value, after_config
        )
    except Exception as exc:
        after_interface = None
        after_poe = None
        after_config = None
        met = False
        detail = f"Verification could not read the new state ({type(exc).__name__})."
    after_state = (
        f"status={after_interface.status} description={after_interface.name!r}"
        + (f" poe={after_poe}" if after_poe is not None else "")
    ) if after_interface else "unavailable"

    if met:
        progress.finish("ok", detail)
        return result(
            "success",
            before=before_state,
            after=after_state,
            commands=commands,
            backup_path=backup_path,
            detail=detail,
            requires_save=True,
        )

    progress.finish("failed", detail)

    # -- rollback ---------------------------------------------------------
    progress.begin("rollback")
    try:
        rollback_output = client.run_raw_action(rollback_commands)
        rollback_rejected = classify_ios_response(rollback_output)
        restored = _read_interface(client, canonical, actor)
        restored_poe = (
            _read_poe_state(client, canonical, actor)
            if kind in {"poe_auto", "poe_never"}
            else None
        )
        restored_config = _interface_config(
            run_and_audit(client, symbol="show_running_config", actor=actor), canonical
        )
        rolled_back = rollback_rejected is None and _rollback_verified(
            kind, before_config, restored, restored_poe, restored_config
        )
    except Exception:
        rolled_back = False

    progress.finish(
        "ok" if rolled_back else "failed",
        "The interface was returned to its previous state."
        if rolled_back
        else "Rollback did not restore the previous state; inspect the interface.",
    )
    return result(
        "rolled_back" if rolled_back else "failed",
        before=before_state,
        after=after_state,
        commands=commands + rollback_commands,
        backup_path=backup_path,
        detail=detail,
        rolled_back=rolled_back,
        requires_save=not rolled_back,
    )


# --- running versus startup configuration -----------------------------------


class ConfigSaveTracker:
    """Tracks whether the running configuration has drifted from startup.

    Two sources, deliberately combined. SwitchOps knows immediately when its
    own operation changed something, and a fingerprint comparison catches
    changes made from anywhere else. Neither sends configuration text anywhere:
    only digests are compared.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending = 0
        self._last_change: Optional[datetime] = None
        self._last_saved: Optional[datetime] = None
        self._fingerprint_differs: Optional[bool] = None

    def record_change(self, at: Optional[datetime] = None) -> None:
        with self._lock:
            self._pending += 1
            self._last_change = at or datetime.now(timezone.utc)
            self._fingerprint_differs = True

    def record_save(self, at: Optional[datetime] = None) -> None:
        with self._lock:
            self._pending = 0
            self._last_saved = at or datetime.now(timezone.utc)
            self._fingerprint_differs = False

    def record_fingerprints(self, running: str, startup: str) -> None:
        with self._lock:
            self._fingerprint_differs = running != startup
            if not self._fingerprint_differs:
                self._pending = 0

    def reset(self) -> None:
        """Return process-local state to its launch condition (also used by tests)."""
        with self._lock:
            self._pending = 0
            self._last_change = None
            self._last_saved = None
            self._fingerprint_differs = None

    def state(self) -> "ConfigSaveState":
        from .models import ConfigSaveState

        with self._lock:
            modified = bool(self._fingerprint_differs) or self._pending > 0
            if modified and self._pending:
                detail = (
                    f"{self._pending} change(s) are in the running configuration only. "
                    "They will be lost if the switch reboots."
                )
            elif modified:
                detail = (
                    "The running configuration differs from the startup configuration. "
                    "The change may have been made outside SwitchOps."
                )
            elif self._fingerprint_differs is None:
                detail = "SwitchOps has not compared the running and startup configurations yet."
            else:
                detail = "The running configuration matches the startup configuration."
            return ConfigSaveState(
                runningModified=modified,
                lastChangeAt=self._last_change,
                lastSavedAt=self._last_saved,
                pendingOperations=self._pending,
                detail=detail,
            )


_save_tracker = ConfigSaveTracker()


def get_save_tracker() -> ConfigSaveTracker:
    return _save_tracker


def config_fingerprints(client: SwitchClient, actor: str = "system") -> tuple[str, str]:
    """SHA-256 of the normalised running and startup configurations.

    Volatile lines are stripped so a timestamp does not read as a change, and
    only the digests ever leave this function.
    """
    import hashlib

    def digest(text: str) -> str:
        lines = []
        for line in (text or "").splitlines():
            stripped = line.strip()
            # Drop the banner and the counters IOS regenerates on every read.
            if not stripped or stripped.startswith("!"):
                continue
            if stripped.startswith(("Current configuration", "Using ", "Building configuration")):
                continue
            if stripped.startswith("ntp clock-period"):
                continue
            lines.append(stripped)
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()

    running = run_and_audit(client, symbol="show_running_config", actor=actor)
    startup = run_and_audit(client, symbol="show_startup_config", actor=actor)
    return digest(running), digest(startup)


def save_running_config(client: SwitchClient, actor: str = "operator") -> tuple[bool, str]:
    """Persist running to startup. Only ever called from an explicit action."""
    with get_write_lock().operation_guard():
        return _save_running_config_authorized(client, actor)


def _save_running_config_authorized(
    client: SwitchClient, actor: str = "operator"
) -> tuple[bool, str]:
    started = time.monotonic()
    output = client.run_raw_action(["write memory"])
    rejection = classify_ios_response(output)
    if rejection is not None:
        get_audit_store().record(
            actor=actor,
            action="operation:save_config",
            commands=["write memory"],
            success=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_type="ios_rejection",
            error_message=rejection,
        )
        return False, f"The switch rejected the save: {rejection}"
    get_audit_store().record(
        actor=actor,
        action="operation:save_config",
        commands=["write memory"],
        success=True,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return True, "The running configuration was written to the startup configuration."
