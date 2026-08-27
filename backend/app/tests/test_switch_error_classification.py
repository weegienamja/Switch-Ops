from __future__ import annotations

import errno

import netmiko
import paramiko
import pytest

from backend.app import switch_client as sc
from backend.app.credential_store import SwitchCredentials
from backend.app.errors import (
    HostKeyChangedError,
    LegacySshNegotiationError,
    SwitchAuthenticationError,
    SwitchConnectionError,
    SwitchUnreachableError,
    classify_connection_exception,
)


CREDS = SwitchCredentials(
    switch_host="192.0.2.10",
    switch_username="synthetic-operator",
    switch_password="synthetic-password",
    switch_enable_secret="",
)


def _connect_with(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    def fail(**_params):
        raise error

    monkeypatch.setattr(netmiko, "ConnectHandler", fail)
    monkeypatch.setattr(sc, "is_host_pinned", lambda _host: False)
    sc.NetmikoSwitchClient(CREDS, legacy_algorithms=False).connect()


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("connect timed out"),
        ConnectionRefusedError(errno.ECONNREFUSED, "connection refused"),
        OSError(errno.ENETUNREACH, "network unreachable"),
    ],
)
def test_timeout_refusal_and_network_unreachable_are_switch_unreachable(monkeypatch, error):
    with pytest.raises(SwitchUnreachableError) as captured:
        _connect_with(monkeypatch, error)
    assert captured.value.code == "switch_unreachable"


def test_authentication_failure_is_not_misclassified_as_negotiation(monkeypatch):
    error = paramiko.AuthenticationException("rejected synthetic-password")
    with pytest.raises(SwitchAuthenticationError) as captured:
        _connect_with(monkeypatch, error)
    assert captured.value.code == "switch_auth_failed"
    assert "synthetic-password" not in captured.value.public_copy().message


def test_host_key_change_remains_fail_closed(monkeypatch):
    expected = paramiko.RSAKey.generate(1024)
    received = paramiko.RSAKey.generate(1024)
    error = paramiko.BadHostKeyException("192.0.2.10", received, expected)
    with pytest.raises(HostKeyChangedError) as captured:
        _connect_with(monkeypatch, error)
    assert captured.value.code == "host_key_changed"


def test_ssh_protocol_failure_is_negotiation_failure(monkeypatch):
    with pytest.raises(LegacySshNegotiationError) as captured:
        _connect_with(monkeypatch, paramiko.SSHException("no shared kex"))
    assert captured.value.code == "ssh_negotiation_failed"


def test_legacy_paramiko_authentication_subclass_is_classified_before_ssh_base():
    error = classify_connection_exception(paramiko.AuthenticationException("rejected"))
    assert isinstance(error, SwitchAuthenticationError)


def test_unknown_connect_failure_does_not_invent_a_specific_cause():
    error = classify_connection_exception(RuntimeError("unexpected library failure"))
    assert type(error) is SwitchConnectionError
    assert error.code == "switch_connection_failed"
