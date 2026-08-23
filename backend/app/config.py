"""Configuration for the SwitchOps backend.

All values are local to the host. Real credentials are never read from this
settings object — they live in the OS keyring (or a guarded local file) and
are loaded via ``credential_store``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _runtime_root() -> Path:
    """Return a durable, per-user runtime root.

    A PyInstaller one-file application executes from a temporary extraction
    directory, so writing beside ``__file__`` would lose backups, logs, and
    the credential fallback when the sidecar exits.
    """
    configured = os.environ.get("SWITCHOPS_DATA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "SwitchOps"
    return BACKEND_ROOT


RUNTIME_ROOT = _runtime_root()
DATA_DIR = RUNTIME_ROOT / "data"
LOG_DIR = RUNTIME_ROOT / "logs"
BACKUP_DIR = RUNTIME_ROOT / "backups"

# Test fixtures are source-only. Production builds do not bundle this path.
SAMPLE_DIR = Path(__file__).resolve().parent / "sample_outputs"

for _d in (DATA_DIR, LOG_DIR, BACKUP_DIR):
    _d.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    """Runtime settings. Loaded from environment + optional .env file."""

    model_config = SettingsConfigDict(
        env_file=str(RUNTIME_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    host: str = Field(default="127.0.0.1", alias="SWITCHOPS_HOST")
    port: int = Field(default=8765, alias="SWITCHOPS_PORT")
    cors_origins: str = Field(
        default="http://localhost:3000,tauri://localhost,http://tauri.localhost",
        alias="SWITCHOPS_CORS_ORIGINS",
    )

    mock_mode: bool = Field(default=False, alias="SWITCH_MOCK_MODE")
    legacy_ssh: bool = Field(default=True, alias="SWITCH_LEGACY_SSH")
    allow_system_ssh: bool = Field(default=False, alias="SWITCH_ALLOW_SYSTEM_SSH")
    enable_api_docs: bool = Field(default=False, alias="SWITCHOPS_ENABLE_API_DOCS")
    telemetry_retention_days: int = Field(
        default=30,
        ge=1,
        le=3650,
        alias="SWITCHOPS_TELEMETRY_RETENTION_DAYS",
    )

    # Read directly only if keyring + file are both unavailable. Treated as
    # placeholders; never logged.
    switch_host: str = Field(default="", alias="SWITCH_HOST")
    switch_username: str = Field(default="", alias="SWITCH_USERNAME")
    switch_password: str | None = Field(default=None, alias="SWITCH_PASSWORD")
    switch_enable_secret: str | None = Field(default=None, alias="SWITCH_ENABLE_SECRET")
    switch_device_type: str = Field(default="cisco_ios", alias="SWITCH_DEVICE_TYPE")

    backup_dir: Path = BACKUP_DIR
    log_dir: Path = LOG_DIR
    data_dir: Path = DATA_DIR
    sample_dir: Path = SAMPLE_DIR

    @model_validator(mode="after")
    def disable_mock_in_packaged_build(self) -> "Settings":
        # SWITCH_MOCK_MODE remains useful for source-only automated tests and
        # developer harnesses. A packaged sidecar must never present fixtures
        # as the operator's network, even if its environment is manipulated.
        if getattr(sys, "frozen", False):
            self.mock_mode = False
        return self

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
