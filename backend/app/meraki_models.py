"""Typed local API contracts for the read-only Meraki evidence source."""
from __future__ import annotations

from datetime import datetime
import re

from pydantic import BaseModel, Field, field_validator

from .unified_models import SourceHealth


_SCOPE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class MerakiCredentialRequest(BaseModel):
    api_key: str = Field(alias="apiKey", min_length=8, max_length=512)

    model_config = {"populate_by_name": True}

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise ValueError("API key is invalid.")
        return value


class MerakiSelection(BaseModel):
    organization_id: str = Field(alias="organizationId")
    organization_name: str = Field(alias="organizationName", min_length=1, max_length=160)
    network_id: str = Field(alias="networkId")
    network_name: str = Field(alias="networkName", min_length=1, max_length=160)

    model_config = {"populate_by_name": True}

    @field_validator("organization_id", "network_id")
    @classmethod
    def validate_scope_id(cls, value: str) -> str:
        value = value.strip()
        if not _SCOPE_ID.fullmatch(value):
            raise ValueError("Provider scope identifier is invalid.")
        return value

    @field_validator("organization_name", "network_name")
    @classmethod
    def validate_scope_name(cls, value: str) -> str:
        value = value.strip()
        if any(ord(char) < 32 for char in value):
            raise ValueError("Provider scope name is invalid.")
        return value


class MerakiOrganization(BaseModel):
    id: str
    name: str


class MerakiNetwork(BaseModel):
    id: str
    organization_id: str = Field(alias="organizationId")
    name: str
    product_types: list[str] = Field(default_factory=list, alias="productTypes")

    model_config = {"populate_by_name": True}


class MerakiSetupStatus(BaseModel):
    configured: bool
    keyring_available: bool = Field(alias="keyringAvailable")
    storage: str = "none"
    selection: MerakiSelection | None = None
    source_health: SourceHealth = Field(alias="sourceHealth")

    model_config = {"populate_by_name": True}


class MerakiConnectionTestResult(BaseModel):
    ok: bool
    summary: str
    checked_at: datetime = Field(alias="checkedAt")
    organizations_visible: int = Field(default=0, alias="organizationsVisible")
    source_health: SourceHealth = Field(alias="sourceHealth")

    model_config = {"populate_by_name": True}


class MerakiRefreshResult(BaseModel):
    accepted: bool
    summary: str
    source_health: SourceHealth = Field(alias="sourceHealth")

    model_config = {"populate_by_name": True}
