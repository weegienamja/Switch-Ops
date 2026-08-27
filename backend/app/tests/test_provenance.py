"""Provenance exists so a stale backend is provable rather than invisible.

The desktop shell and a development backend both bind 127.0.0.1:8765. These
tests pin the contract the shell relies on to tell them apart, and pin the
privacy boundary the endpoint must not cross.
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

from backend.app import main
from backend.app import provenance


client = TestClient(main.app, raise_server_exceptions=False)


def _provenance() -> dict:
    response = client.get("/api/system/provenance")
    assert response.status_code == 200
    return response.json()


def test_provenance_reports_the_running_build_and_schema():
    body = _provenance()
    assert body["buildId"] == provenance.BUILD_ID
    assert body["apiSchemaVersion"] == provenance.API_SCHEMA_VERSION
    expected_mode = "frozen-sidecar" if provenance.FROZEN else "development"
    assert body["runtimeMode"] == expected_mode
    assert body["startedAt"].endswith("Z")


def test_provenance_confirms_the_management_path_endpoint_is_served():
    # The stale backend that shipped the original incident answered 404 here.
    # If this flips to False the desktop shell must be able to say so.
    assert _provenance()["managementPathAvailable"] is True
    assert client.get("/api/management-path").status_code != 404


def test_provenance_echoes_only_the_token_the_shell_supplied(monkeypatch):
    monkeypatch.setenv(provenance.SIDECAR_TOKEN_ENV, "nonce-from-the-shell")
    assert _provenance()["sidecarToken"] == "nonce-from-the-shell"

    monkeypatch.delenv(provenance.SIDECAR_TOKEN_ENV, raising=False)
    # A manually started backend must not claim to be a spawned sidecar.
    assert _provenance()["sidecarToken"] is None


def test_provenance_discloses_no_local_paths_user_name_or_process_id():
    body = _provenance()
    serialized = repr(body)
    user = os.environ.get("USERNAME") or os.environ.get("USER")
    if user:
        assert user.lower() not in serialized.lower()
    for leaked in ("C:", "/Users/", "AppData", "\\\\"):
        assert leaked not in serialized
    # Neither the executable name nor the process id had a consumer, so they
    # were removed rather than reported "safely".
    assert "executableName" not in body
    assert "processId" not in body
    assert str(os.getpid()) not in serialized


def test_development_build_id_tracks_source_edits(tmp_path, monkeypatch):
    # A build id that ignored source changes would not have caught the
    # incident this endpoint exists to prevent.
    first = provenance._development_build_id()
    assert first == provenance._development_build_id()

    target = provenance._APP_ROOT / "provenance.py"
    original = target.stat()
    try:
        os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000_000))
        assert provenance._development_build_id() != first
    finally:
        os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert provenance._development_build_id() == first
