from pathlib import Path

from app.config import Settings
from app.credential_store import CredentialStore


def test_source_defaults_have_no_device_and_fixture_mode_is_off(monkeypatch):
    for name in (
        "SWITCH_MOCK_MODE",
        "SWITCH_HOST",
        "SWITCH_USERNAME",
        "SWITCH_PASSWORD",
        "SWITCH_ENABLE_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)
    assert settings.mock_mode is False
    assert settings.switch_host == ""
    assert settings.switch_username == ""
    assert settings.switch_password is None


def test_packaged_build_cannot_enable_fixture_mode(monkeypatch):
    monkeypatch.setenv("SWITCH_MOCK_MODE", "true")
    monkeypatch.setattr("app.config.sys.frozen", True, raising=False)

    settings = Settings(_env_file=None)
    assert settings.mock_mode is False


def test_clean_credential_store_is_unconfigured(tmp_path, monkeypatch):
    from app import credential_store as credential_module

    for name in ("SWITCH_HOST", "SWITCH_USERNAME", "SWITCH_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(credential_module, "CRED_FILE", tmp_path / "credentials.json")
    store = CredentialStore()
    store._keyring = None  # type: ignore[attr-defined]

    assert store.storage_backend() == "none"
    assert store.is_configured() is False
    assert store.status()["switch_host"] is None


def test_incomplete_environment_credentials_do_not_configure_a_device(tmp_path, monkeypatch):
    from app import credential_store as credential_module

    monkeypatch.setattr(credential_module, "CRED_FILE", tmp_path / "credentials.json")
    monkeypatch.setenv("SWITCH_PASSWORD", "synthetic-password")
    monkeypatch.delenv("SWITCH_HOST", raising=False)
    monkeypatch.delenv("SWITCH_USERNAME", raising=False)
    store = CredentialStore()
    store._keyring = None  # type: ignore[attr-defined]

    assert store.storage_backend() == "none"
    assert store.load() is None


def test_incomplete_keyring_entry_does_not_shadow_complete_file(tmp_path, monkeypatch):
    from app import credential_store as credential_module

    class PartialKeyring:
        @staticmethod
        def get_password(_service, account):
            return "orphaned-password" if account == "switch_password" else None

    monkeypatch.setattr(credential_module, "CRED_FILE", tmp_path / "credentials.json")
    store = CredentialStore()
    store._keyring = None  # type: ignore[attr-defined]
    store.save(
        credential_module.SwitchCredentials(
            switch_host="192.0.2.20",
            switch_username="operator",
            switch_password="file-password",
            switch_enable_secret="",
        )
    )
    store._keyring = PartialKeyring()  # type: ignore[attr-defined]

    loaded = store.load()
    assert loaded is not None
    assert loaded.switch_host == "192.0.2.20"
    assert loaded.switch_password == "file-password"


def test_production_sidecar_build_does_not_bundle_sample_outputs():
    script = (
        Path(__file__).resolve().parents[3]
        / "desktop"
        / "scripts"
        / "build-backend-sidecar.ps1"
    ).read_text(encoding="utf-8")
    assert "--add-data" not in script
    assert "sample_outputs" not in script
