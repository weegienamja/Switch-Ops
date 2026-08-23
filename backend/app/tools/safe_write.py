"""Legacy bounded write helper using the same local policy gates as operations."""
from __future__ import annotations

from contextlib import nullcontext
import time
from typing import Optional

from ..audit_store import get_audit_store
from ..command_registry import build_write_action
from ..credential_store import get_credential_store
from ..errors import WriteActionsDisabledError
from ..interface_policy import get_interface_policy_store
from ..models import WriteActionResult
from ..switch_client import SwitchClient, MockSwitchClient
from .backup import backup_running_config


def _capture_interface_state(client: SwitchClient, interface: Optional[str]) -> str:
    if interface is None:
        return ""
    try:
        out = client.run("show_interfaces_status")
    except Exception as exc:  # pragma: no cover
        return f"<failed to read state: {type(exc).__name__}>"
    # Slim to lines mentioning this interface short form (Gi0/x).
    short = interface.replace("GigabitEthernet", "Gi")
    relevant = [ln for ln in out.splitlines() if short in ln]
    return "\n".join(relevant) if relevant else out[:400]


def execute_safe_write(
    client: SwitchClient,
    *,
    action: str,
    interface: Optional[str] = None,
    value: Optional[str] = None,
    actor: str = "user",
) -> WriteActionResult:
    # Keep the session-level approval stable too. The local import avoids a
    # module cycle while ensuring this legacy helper cannot bypass the lock.
    from ..operations import get_write_lock

    policy = get_interface_policy_store()
    if not policy.controlled_writes_enabled():
        raise WriteActionsDisabledError(
            "Controlled writes are disabled in local SwitchOps settings."
        )
    host = get_credential_store().status().get("switch_host")
    guard = policy.operation_guard(host, interface) if interface else nullcontext()
    with get_write_lock().operation_guard():
        with guard:
            return _execute_safe_write_authorized(
                client,
                action=action,
                interface=interface,
                value=value,
                actor=actor,
            )


def _execute_safe_write_authorized(
    client: SwitchClient,
    *,
    action: str,
    interface: Optional[str] = None,
    value: Optional[str] = None,
    actor: str = "user",
) -> WriteActionResult:

    plan = build_write_action(action, interface=interface, value=value)

    # 1. Backup first.
    backup = backup_running_config(client)

    # 2. Snapshot before.
    before = _capture_interface_state(client, plan.interface)

    # 3. Execute.
    start = time.monotonic()
    err_type: str | None = None
    err_msg: str | None = None
    try:
        if isinstance(client, MockSwitchClient):
            client.run_command_sequence(plan.commands)
        else:
            client.run_raw_action(plan.commands)  # type: ignore[attr-defined]
        success = True
    except Exception as exc:
        success = False
        err_type = type(exc).__name__
        err_msg = str(exc)
        raise
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)

        after = _capture_interface_state(client, plan.interface) if success else ""

        get_audit_store().record(
            actor=actor,
            action=f"write:{action}",
            commands=plan.commands,
            success=success,
            duration_ms=duration_ms,
            output_path=backup.path,
            error_type=err_type,
            error_message=err_msg,
            before_state=before,
            after_state=after,
        )

    return WriteActionResult(
        action=action,
        interface=plan.interface,
        success=True,
        before=before,
        after=after,
        backupPath=backup.path,
        durationMs=duration_ms,
    )
