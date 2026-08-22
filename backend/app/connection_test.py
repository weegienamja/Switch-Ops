"""Bounded, read-only connection diagnostics.

The test answers one question - "can SwitchOps talk to this switch, and how far
does it get?" - and answers it only with things it actually proved. It runs the
same allowlisted read commands the dashboard already uses, changes nothing, and
never returns a credential, a raw exception string, or a claim it cannot
support (Internet reachability, privilege level, device health).

Failure detail is classified into a fixed vocabulary rather than echoed, so a
server-side error message can never leak a username, path, or secret into an
API response.
"""
from __future__ import annotations

import logging
import socket
import time
from datetime import datetime, timezone
from typing import Optional

from .command_registry import resolve_read_command
from .config import get_settings
from .credential_store import get_credential_store
from .errors import (
    CredentialsMissingError,
    SwitchOpsError,
    HostKeyChangedError,
    LegacySshNegotiationError,
    SwitchConnectionError,
)
from .host_key_store import is_host_pinned
from .models import ConnectionCheck, ConnectionTestResult
from .parsers.interfaces import parse_interface_status
from .parsers.version import parse_version
from .switch_client import SwitchClient
from .tools.read_only import run_and_audit

logger = logging.getLogger(__name__)

SSH_PORT = 22
TCP_TIMEOUT_SECONDS = 4.0

# Ordered: each check only runs if the previous one could succeed.
CHECK_LABELS: tuple[tuple[str, str], ...] = (
    ("credentials", "Stored credentials available"),
    ("reachable", "Host reachable on TCP 22"),
    ("ssh", "SSH session established"),
    ("host_key", "SSH host key matched"),
    ("auth", "Authentication succeeded"),
    ("platform", "Cisco IOS detected"),
    ("read_ops", "Read-only operations available"),
)


def _pending_checks() -> dict[str, ConnectionCheck]:
    return {
        key: ConnectionCheck(id=key, label=label, status="skipped", detail="Not reached.")
        for key, label in CHECK_LABELS
    }


def _classify(exc: BaseException) -> str:
    """Map an exception chain to a safe failure code. Never returns detail."""
    names: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(names) < 8:
        names.append(type(current).__name__)
        current = current.__cause__
    joined = " ".join(names).lower()
    if "hostkeychanged" in joined:
        return "host_key_changed"
    if "authentication" in joined or "auth" in joined:
        return "authentication_failed"
    if "timeout" in joined:
        return "timed_out"
    if "kex" in joined or "negotiation" in joined or "legacyssh" in joined:
        return "ssh_negotiation_failed"
    return "connection_failed"


FAILURE_TEXT: dict[str, str] = {
    "host_key_changed": (
        "The switch presented a different SSH host key than the one SwitchOps pinned. "
        "The connection was refused. This is either a rebuilt switch or an interception "
        "attempt, and it must be resolved deliberately."
    ),
    "authentication_failed": (
        "The switch was reachable and accepted an SSH session, but rejected the stored "
        "username or password."
    ),
    "timed_out": "The switch accepted a TCP connection but did not complete the SSH exchange in time.",
    "ssh_negotiation_failed": (
        "No shared SSH algorithm could be negotiated, even with legacy compatibility enabled."
    ),
    "connection_failed": "The SSH session could not be established.",
    "credentials_missing": "No switch credentials are stored yet. Run the setup wizard.",
    "host_unreachable": (
        "Nothing answered on TCP port 22 at the configured address. The switch may be off, "
        "on a different address, or unreachable from this PC."
    ),
    "unsupported_platform": (
        "A session was established, but the device did not identify itself as Cisco IOS."
    ),
    "read_ops_unavailable": (
        "The session works, but the read-only commands SwitchOps depends on returned nothing "
        "usable. The account may lack the privilege these show commands need."
    ),
}


def _probe_tcp(host: str, port: int = SSH_PORT, timeout: float = TCP_TIMEOUT_SECONDS) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _classify_session_failure(
    checks: dict[str, ConnectionCheck],
    error: Optional[SwitchOpsError],
    pinned_before: bool,
    now: datetime,
    started: float,
) -> ConnectionTestResult:
    """Describe why the operational session is unavailable."""
    code = _classify(error) if error is not None else "connection_failed"
    if code == "host_key_changed":
        checks["ssh"].status = "pass"
        checks["ssh"].detail = "The switch answered the SSH handshake."
        checks["host_key"].status = "fail"
        checks["host_key"].detail = FAILURE_TEXT[code]
        summary = "Blocked for safety: the SSH host key changed."
    elif code == "authentication_failed":
        checks["ssh"].status = "pass"
        checks["ssh"].detail = "The switch answered the SSH handshake."
        checks["host_key"].status = "pass"
        checks["host_key"].detail = (
            "The presented host key matched the pinned key."
            if pinned_before
            else "First connection; no pinned key to compare against yet."
        )
        checks["auth"].status = "fail"
        checks["auth"].detail = FAILURE_TEXT[code]
        summary = "Authentication was rejected by the switch."
    else:
        checks["ssh"].status = "fail"
        checks["ssh"].detail = FAILURE_TEXT.get(code, FAILURE_TEXT["connection_failed"])
        summary = "The SSH session could not be established."
    return _finish(
        checks,
        ok=False,
        summary=summary,
        failure_code=code,
        now=now,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _mock_result(now: datetime) -> ConnectionTestResult:
    checks = _pending_checks()
    for check in checks.values():
        check.status = "skipped"
        check.detail = "Mock mode is active; no device was contacted."
    return ConnectionTestResult(
        ok=True,
        mode="mock",
        summary="Mock mode — SwitchOps is serving recorded sample output and did not contact a device.",
        checks=list(checks.values()),
        testedAt=now,
    )


def _finish(
    checks: dict[str, ConnectionCheck],
    *,
    ok: bool,
    summary: str,
    failure_code: str | None,
    now: datetime,
    duration_ms: int,
) -> ConnectionTestResult:
    return ConnectionTestResult(
        ok=ok,
        mode="real",
        summary=summary,
        checks=list(checks.values()),
        failureCode=failure_code,
        testedAt=now,
        durationMs=duration_ms,
    )


def run_connection_test(
    *,
    client: Optional[SwitchClient] = None,
    session_error: Optional[SwitchOpsError] = None,
) -> ConnectionTestResult:
    """Run the bounded diagnostic against the operational session.

    ``client`` is the persistent session handed over by the device worker. When
    it is None the worker could not provide one, and ``session_error`` carries
    why - which is more useful than opening a throwaway connection that tells
    the user nothing about the session SwitchOps actually uses.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    started = time.monotonic()

    if settings.mock_mode:
        return _mock_result(now)

    checks = _pending_checks()

    status = get_credential_store().status()
    if not status.get("configured"):
        checks["credentials"].status = "fail"
        checks["credentials"].detail = FAILURE_TEXT["credentials_missing"]
        return _finish(
            checks,
            ok=False,
            summary="No credentials are stored, so nothing was contacted.",
            failure_code="credentials_missing",
            now=now,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    host = str(status.get("switch_host") or settings.switch_host)
    storage = str(status.get("storage"))
    checks["credentials"].status = "pass"
    checks["credentials"].detail = (
        "Windows Credential Manager holds a username and password for this switch."
        if storage == "keyring"
        else f"Credentials are available from the {storage} store."
    )

    if not _probe_tcp(host):
        checks["reachable"].status = "fail"
        checks["reachable"].detail = FAILURE_TEXT["host_unreachable"]
        return _finish(
            checks,
            ok=False,
            summary="The switch did not answer on TCP port 22.",
            failure_code="host_unreachable",
            now=now,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    checks["reachable"].status = "pass"
    checks["reachable"].detail = f"{host} accepted a TCP connection on port {SSH_PORT}."

    pinned_before = is_host_pinned(host)
    if client is None:
        # The worker holds the only session, and it does not have one.
        return _classify_session_failure(
            checks, session_error, pinned_before, now, started
        )
    try:
        checks["ssh"].status = "pass"
        checks["ssh"].detail = "An SSH session was negotiated and opened."
        checks["host_key"].status = "pass"
        checks["host_key"].detail = (
            "The presented host key matched the key SwitchOps pinned on first use."
            if pinned_before
            else "First connection to this host. The presented key has now been pinned; a future "
                 "change will be refused."
        )
        checks["auth"].status = "pass"
        checks["auth"].detail = "The stored account was accepted by the switch."

        version_output = run_and_audit(client, symbol="show_version", actor="connection-test")
        version = parse_version(version_output)
        ios_version = version.get("ios_version")
        looks_like_ios = "cisco ios" in version_output.lower() or bool(ios_version)
        if not looks_like_ios:
            checks["platform"].status = "fail"
            checks["platform"].detail = FAILURE_TEXT["unsupported_platform"]
            return _finish(
                checks,
                ok=False,
                summary="The device responded but is not recognisably Cisco IOS.",
                failure_code="unsupported_platform",
                now=now,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        model = version.get("model") or "an unnamed model"
        checks["platform"].status = "pass"
        checks["platform"].detail = (
            f"Reported as {model} running IOS {ios_version or 'of an unstated version'}."
        )

        interfaces_output = run_and_audit(
            client, symbol="show_interfaces_status", actor="connection-test"
        )
        interfaces = parse_interface_status(interfaces_output)
        if not interfaces:
            checks["read_ops"].status = "fail"
            checks["read_ops"].detail = FAILURE_TEXT["read_ops_unavailable"]
            return _finish(
                checks,
                ok=False,
                summary="Read-only commands did not return usable output.",
                failure_code="read_ops_unavailable",
                now=now,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        checks["read_ops"].status = "pass"
        checks["read_ops"].detail = (
            f"`{resolve_read_command('show_interfaces_status')}` returned {len(interfaces)} "
            "interface rows."
        )
        return _finish(
            checks,
            ok=True,
            summary=(
                "Connection healthy. SwitchOps can authenticate and read this switch. "
                "Nothing was changed."
            ),
            failure_code=None,
            now=now,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:
        code = _classify(exc)
        logger.warning("Connection test read step failed (%s)", type(exc).__name__)
        target = checks["read_ops"] if checks["platform"].status == "pass" else checks["platform"]
        target.status = "fail"
        target.detail = FAILURE_TEXT.get(code, FAILURE_TEXT["connection_failed"])
        return _finish(
            checks,
            ok=False,
            summary="The session opened but a required read-only command failed.",
            failure_code=code,
            now=now,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    finally:
        # The session belongs to the device worker; never close it here.
        pass
