"""Credential store and API safety tests."""
from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ["SWITCH_MOCK_MODE"] = "true"

from app.credential_store import CredentialStore, SwitchCredentials  # noqa: E402
from app.main import app  # noqa: E402


def test_credential_store_save_and_load(tmp_path, monkeypatch):
    cred_file = tmp_path / "credentials.json"
    monkeypatch.setattr("app.credential_store.CRED_FILE", cred_file)
    store = CredentialStore()
    # Force file path even if keyring exists in env
    store._keyring = None  # type: ignore[attr-defined]
    creds = SwitchCredentials(
        switch_host="192.0.2.190",
        switch_username="operator",
        switch_password="__REPLACE_WITH_LOCAL_SECRET__",
        switch_enable_secret="__REPLACE_WITH_LOCAL_SECRET__",
        switch_device_type="cisco_ios",
    )
    backend = store.save(creds)
    assert backend == "file"
    loaded = store.load()
    assert loaded is not None
    assert loaded.switch_username == "operator"
    safe = store.load(safe=True)
    assert safe is not None
    assert safe.switch_password == "***"
    assert safe.switch_enable_secret == "***"


def test_setup_status_never_returns_password():
    client = TestClient(app)
    r = client.get("/api/setup/status")
    assert r.status_code == 200
    body = r.json()
    # Boolean presence only
    assert "hasPassword" in body
    assert "hasEnableSecret" in body
    # No raw password / secret keys anywhere in the payload
    assert "password" not in body
    assert "switchPassword" not in body
    assert "enableSecret" not in body
    assert "switchEnableSecret" not in body
