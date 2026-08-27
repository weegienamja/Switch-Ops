"""Typed errors raised by the backend.

These translate to structured HTTP errors in ``main.py``.
"""
from __future__ import annotations

import errno
import socket


class SwitchOpsError(Exception):
    code: str = "switchops_error"
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        detail: str | None = None,
        safe_detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        # ``detail`` is diagnostic-only and may contain a library exception.
        # It must never cross the API boundary for a server/device failure.
        self.detail = detail
        # ``safe_detail`` is deliberately authored for the local UI.
        self.safe_detail = safe_detail

    def public_copy(self) -> "SwitchOpsError":
        """Copy only fields that are safe to hand to another request caller."""
        return type(self)(self.message, safe_detail=self.safe_detail)


class CommandNotAllowedError(SwitchOpsError):
    code = "command_not_allowed"
    http_status = 400


class ProtectedInterfaceError(SwitchOpsError):
    code = "protected_interface"
    http_status = 403


class WriteActionsDisabledError(SwitchOpsError):
    code = "write_actions_disabled"
    http_status = 403


class CredentialsMissingError(SwitchOpsError):
    code = "credentials_missing"
    http_status = 412


class SwitchConnectionError(SwitchOpsError):
    code = "switch_connection_failed"
    http_status = 502


class SwitchUnreachableError(SwitchConnectionError):
    code = "switch_unreachable"


class SwitchAuthenticationError(SwitchConnectionError):
    code = "switch_auth_failed"


class HostKeyChangedError(SwitchConnectionError):
    code = "host_key_changed"


class LegacySshNegotiationError(SwitchConnectionError):
    code = "ssh_negotiation_failed"
    http_status = 502


class DeviceSessionLostError(SwitchConnectionError):
    code = "switch_session_lost"


_NETWORK_ERRNOS = {
    errno.ECONNABORTED,
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.EHOSTDOWN,
    errno.EHOSTUNREACH,
    errno.ENETDOWN,
    errno.ENETRESET,
    errno.ENETUNREACH,
    errno.ENOTCONN,
    errno.ETIMEDOUT,
}


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(chain) < 8:
        chain.append(current)
        current = current.__cause__
    return chain


def classify_connection_exception(exc: BaseException) -> SwitchOpsError:
    """Classify a failed connect from exception types, never message guesses."""
    chain = _exception_chain(exc)
    names = {type(item).__name__.lower() for item in chain}

    if any("badhostkey" in name or "hostkeychanged" in name for name in names):
        return HostKeyChangedError(
            "The Catalyst SSH host key changed; the connection was refused for safety.",
            detail=str(exc),
            safe_detail="Review the pinned host key deliberately before reconnecting.",
        )
    if any("authentication" in name or "authfailure" in name for name in names):
        return SwitchAuthenticationError(
            "The Catalyst rejected the stored SSH credentials.",
            detail=str(exc),
            safe_detail="The device was reached, but authentication did not succeed.",
        )

    for item in chain:
        if isinstance(item, SwitchOpsError):
            return item

    unreachable_names = {
        "connectionrefusederror",
        "connectionreseterror",
        "netmikotimeoutexception",
        "novalidconnectionserror",
        "sockettimeout",
        "timeouterror",
    }
    if names & unreachable_names or any(
        isinstance(item, (TimeoutError, socket.timeout, ConnectionError))
        or (isinstance(item, OSError) and item.errno in _NETWORK_ERRNOS)
        for item in chain
    ):
        return SwitchUnreachableError(
            "The configured Catalyst could not be reached.",
            detail=str(exc),
            safe_detail=(
                "No device-side cause was assumed. The PC network may have changed, "
                "or the device may be offline."
            ),
        )

    negotiation_names = {
        "eoferror",
        "incompatiblepeer",
        "messageordererror",
        "sshexception",
    }
    if names & negotiation_names or any(
        "kex" in name or "negotiation" in name for name in names
    ):
        return LegacySshNegotiationError(
            "SSH negotiation with the Catalyst failed.",
            detail=str(exc),
            safe_detail="The peers did not complete a compatible SSH handshake.",
        )

    return SwitchConnectionError(
        "The Catalyst connection could not be established.",
        detail=str(exc),
        safe_detail="The failure did not prove a more specific device-side cause.",
    )


def is_device_transport_exception(exc: BaseException) -> bool:
    """True only for a confirmed socket/SSH transport failure during a job."""
    for item in _exception_chain(exc):
        if isinstance(item, (DeviceSessionLostError, SwitchUnreachableError)):
            return True
        if isinstance(item, SwitchConnectionError) and not isinstance(
            item, (SwitchAuthenticationError, HostKeyChangedError, LegacySshNegotiationError)
        ):
            return True
        if isinstance(item, (TimeoutError, socket.timeout, ConnectionError)):
            return True
        if isinstance(item, OSError) and item.errno in _NETWORK_ERRNOS:
            return True
        name = type(item).__name__.lower()
        if name in {
            "channelexception",
            "eoferror",
            "netmikotimeoutexception",
            "proxycommandfailure",
            "readexception",
            "sshexception",
            "writeexception",
        }:
            return True
    return False


def session_lost_error(exc: BaseException) -> DeviceSessionLostError:
    return DeviceSessionLostError(
        "The active Catalyst session was lost.",
        detail=str(exc),
        safe_detail="The failed SSH client was discarded; the next device job will reconnect cleanly.",
    )


def public_error_message(exc: BaseException) -> str:
    """Return an audit/API-safe summary without serializing library diagnostics."""
    if isinstance(exc, SwitchOpsError):
        return exc.message
    return "The device operation failed."
