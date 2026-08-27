"""No read-only API payload may disclose where SwitchOps stores its files.

On Windows every application-data path contains the local account name, so a
field like ``dataDir`` leaks the operator's identity to anything that can read
loopback. Storage health is reported as semantic state instead.
"""
from __future__ import annotations

import json
import os
import re

import pytest
from fastapi.testclient import TestClient

from backend.app import main


client = TestClient(main.app, raise_server_exceptions=False)

# Read-only endpoints that need no credentials and no reachable device.
SAFE_ENDPOINTS = [
    "/api/system/info",
    "/api/system/provenance",
    "/api/setup/status",
    "/api/meraki/setup/status",
    "/api/config/state",
    "/api/interface-policy",
    "/api/operations/catalog",
    "/api/guide/operations",
]

# Shapes that indicate a filesystem location rather than a value.
PATH_PATTERNS = (
    # A drive letter is a single letter at a boundary. Requiring the boundary
    # stops "http://" matching as a drive named "p:".
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
    re.compile(r"\\\\[A-Za-z0-9._-]+\\"),    # \\server\share UNC
    re.compile(r"/home/[A-Za-z0-9._-]+"),
    re.compile(r"/Users/[A-Za-z0-9._-]+"),
    re.compile(r"(?i)\bAppData\b"),
    re.compile(r"(?i)\bLOCALAPPDATA\b"),
    re.compile(r"(?i)[\\/]ProgramData[\\/]"),
)


def _bodies() -> list[tuple[str, str]]:
    collected: list[tuple[str, str]] = []
    for endpoint in SAFE_ENDPOINTS:
        response = client.get(endpoint)
        if response.status_code >= 500:
            continue
        collected.append((endpoint, response.text))
    return collected


@pytest.mark.parametrize("pattern", PATH_PATTERNS, ids=lambda p: p.pattern)
def test_no_read_only_endpoint_returns_a_filesystem_path(pattern):
    for endpoint, body in _bodies():
        match = pattern.search(body)
        assert match is None, f"{endpoint} disclosed a path: {match.group(0)!r}"


def test_no_read_only_endpoint_returns_the_local_account_name():
    user = os.environ.get("USERNAME") or os.environ.get("USER")
    if not user or len(user) < 3:
        pytest.skip("No local account name available to check against.")
    for endpoint, body in _bodies():
        assert user.lower() not in body.lower(), endpoint


def test_system_info_reports_storage_as_state_not_location():
    body = client.get("/api/system/info").json()
    assert body["storageMode"] in {"packaged", "development"}
    for field in ("dataStoreAvailable", "loggingAvailable", "backupAvailable"):
        assert isinstance(body[field], bool)
    # The old contract carried absolute paths. They must not come back.
    for removed in ("dataDir", "logDir", "backupDir"):
        assert removed not in body


def test_provenance_stays_minimal():
    body = client.get("/api/system/provenance").json()
    # Every field must earn its place: this endpoint exists only so the desktop
    # shell can prove which backend answered, and so a human can read a build
    # id out of a bug report.
    assert set(body) == {
        "buildId",
        "apiSchemaVersion",
        "runtimeMode",
        "startedAt",
        "sidecarToken",
        "managementPathAvailable",
    }


def test_provenance_never_reports_a_process_id_or_executable_path():
    body = client.get("/api/system/provenance").json()
    assert "processId" not in body
    assert "executableName" not in body


def test_error_responses_do_not_disclose_paths():
    # A stack-trace-shaped detail would carry the repository or install path.
    for endpoint in ("/api/switch/does-not-exist", "/api/meraki/networks"):
        body = client.get(endpoint).text
        for pattern in PATH_PATTERNS:
            assert pattern.search(body) is None, (endpoint, pattern.pattern)


def test_openapi_schema_does_not_embed_paths():
    schema = json.dumps(client.get("/openapi.json").json())
    for pattern in PATH_PATTERNS:
        assert pattern.search(schema) is None, pattern.pattern
