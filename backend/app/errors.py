"""Typed errors raised by the backend.

These translate to structured HTTP errors in ``main.py``.
"""
from __future__ import annotations


class SwitchOpsError(Exception):
    code: str = "switchops_error"
    http_status: int = 500

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


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
    code = "switch_connection_error"
    http_status = 502


class HostKeyChangedError(SwitchConnectionError):
    code = "host_key_changed"


class LegacySshNegotiationError(SwitchOpsError):
    code = "legacy_ssh_negotiation_failed"
    http_status = 502
