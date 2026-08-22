"""One fixed, reversible LLDP discovery experiment for lab validation.

This is deliberately not an HTTP operation and accepts no command input. It
exists only to make the explicitly authorised v0.4 hardware experiment safer:
backup, enable global LLDP temporarily, observe, restore, and prove both
running and startup configuration returned to their exact stable state.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from ..audit_store import get_audit_store
from ..discovery import inspect_lldp
from ..operations import classify_ios_response
from ..switch_client import SwitchClient
from .backup import backup_running_config
from .read_only import run_and_audit


ENABLE_LLDP = ["configure terminal", "lldp run", "end"]
DISABLE_LLDP = ["configure terminal", "no lldp run", "end"]


@dataclass(frozen=True)
class LldpExperimentResult:
    status: str
    changed: bool
    neighbors: tuple[str, ...]
    backup_filename: str | None
    running_restored: bool
    startup_unchanged: bool
    detail: str


def _stable_config(text: str) -> tuple[str, ...]:
    """Strip only volatile IOS display headers, retaining configuration lines."""
    stable: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.rstrip()
        if stripped.strip().startswith(("Current configuration", "Using ", "Building configuration")):
            continue
        if stripped.strip().startswith("ntp clock-period"):
            continue
        stable.append(stripped)
    return tuple(stable)


def _audit_action(
    *, action: str, commands: list[str], success: bool, started: float, error: str | None = None
) -> None:
    get_audit_store().record(
        actor="lldp-experiment",
        action=action,
        commands=commands,
        success=success,
        duration_ms=int((time.monotonic() - started) * 1000),
        error_type="ios_rejected" if error else None,
        error_message=error,
    )


def _fixed_action(client: SwitchClient, *, action: str, commands: list[str]) -> None:
    started = time.monotonic()
    try:
        output = client.run_raw_action(commands)
        rejection = classify_ios_response(output)
        if rejection:
            raise RuntimeError(rejection)
    except Exception as exc:
        _audit_action(
            action=action,
            commands=commands,
            success=False,
            started=started,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    _audit_action(action=action, commands=commands, success=True, started=started)


def run_temporary_lldp_experiment(
    client: SwitchClient,
    *,
    wait_seconds: float = 65.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> LldpExperimentResult:
    """Run the one authorised LLDP experiment and restore in ``finally``.

    ``wait_seconds`` is bounded because this helper must never become an
    unbounded device-session owner. Tests inject a no-op sleeper.
    """
    before_running = run_and_audit(
        client, symbol="show_running_config", actor="lldp-experiment"
    )
    before_startup = run_and_audit(
        client, symbol="show_startup_config", actor="lldp-experiment"
    )
    before_summary = run_and_audit(
        client, symbol="show_lldp_neighbors", actor="lldp-experiment"
    )
    before_detail = run_and_audit(
        client, symbol="show_lldp_neighbors_detail", actor="lldp-experiment"
    )
    before = inspect_lldp(
        running_config=before_running,
        summary_output=before_summary,
        detail_output=before_detail,
    )
    if before.state == "unsupported":
        return LldpExperimentResult(
            status="skipped",
            changed=False,
            neighbors=(),
            backup_filename=None,
            running_restored=True,
            startup_unchanged=True,
            detail="Skipped because this IOS image does not support LLDP.",
        )
    if before.enabled:
        return LldpExperimentResult(
            status="already-enabled",
            changed=False,
            neighbors=tuple(item.remote_name for item in before.neighbors),
            backup_filename=None,
            running_restored=True,
            startup_unchanged=True,
            detail="LLDP was already enabled, so no temporary configuration was applied.",
        )
    if before.state != "disabled":
        return LldpExperimentResult(
            status="skipped",
            changed=False,
            neighbors=(),
            backup_filename=None,
            running_restored=True,
            startup_unchanged=True,
            detail="Skipped because the original LLDP state was uncertain.",
        )

    backup = backup_running_config(
        client,
        actor="lldp-experiment",
        config_text=before_running,
    )
    enable_attempted = False
    observed_names: tuple[str, ...] = ()
    experiment_error: BaseException | None = None
    try:
        enable_attempted = True
        _fixed_action(client, action="temporary_lldp_enable", commands=ENABLE_LLDP)
        enabled_config = run_and_audit(
            client, symbol="show_running_config", actor="lldp-experiment"
        )
        if not inspect_lldp(
            running_config=enabled_config,
            summary_output="",
            detail_output="",
        ).enabled:
            raise RuntimeError("LLDP enable verification failed.")
        sleeper(max(0.0, min(float(wait_seconds), 90.0)))
        summary = run_and_audit(
            client, symbol="show_lldp_neighbors", actor="lldp-experiment"
        )
        detail = run_and_audit(
            client, symbol="show_lldp_neighbors_detail", actor="lldp-experiment"
        )
        observed = inspect_lldp(
            running_config=enabled_config,
            summary_output=summary,
            detail_output=detail,
        )
        observed_names = tuple(item.remote_name for item in observed.neighbors)
    except BaseException as exc:
        experiment_error = exc
    finally:
        if enable_attempted:
            _fixed_action(client, action="temporary_lldp_restore", commands=DISABLE_LLDP)

    after_running = run_and_audit(
        client, symbol="show_running_config", actor="lldp-experiment"
    )
    after_startup = run_and_audit(
        client, symbol="show_startup_config", actor="lldp-experiment"
    )
    running_restored = _stable_config(before_running) == _stable_config(after_running)
    startup_unchanged = _stable_config(before_startup) == _stable_config(after_startup)
    if not running_restored or not startup_unchanged:
        raise RuntimeError(
            "LLDP experiment restoration verification failed; configuration differs from the captured state."
        )
    if experiment_error is not None:
        raise experiment_error
    return LldpExperimentResult(
        status="complete",
        changed=True,
        neighbors=observed_names,
        backup_filename=backup.filename,
        running_restored=True,
        startup_unchanged=True,
        detail=(
            f"Observed {len(observed_names)} LLDP neighbour(s); running configuration was restored "
            "and startup configuration was never changed."
        ),
    )
