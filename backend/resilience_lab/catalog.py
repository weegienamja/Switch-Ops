"""Strict scenario catalogue loading and repository-fixture privacy checks."""
from __future__ import annotations

import ipaddress
import json
from pathlib import Path
import re
from typing import Any

from .models import ResilienceScenario, ScenarioCatalog


CATALOG_PATH = Path(__file__).with_name("scenarios") / "catalog.json"
_DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
# IPv4 link-local (RFC 3927). Permitted because an APIPA fallback is a real
# diagnostic state that no documentation prefix can express, and because the
# range is fixed by RFC for every host everywhere: an address inside it
# discloses nothing about the network a fixture was derived from. This is
# deliberately a separate constant so it cannot be mistaken for widening the
# documentation ranges above.
_LINK_LOCAL_NETWORK = ipaddress.ip_network("169.254.0.0/16")
_FORBIDDEN_KEYS = {
    "apikey",
    "credential",
    "password",
    "secret",
    "token",
    "username",
}
_MAC_PATTERN = re.compile(r"(?i)(?:[0-9a-f]{2}[-:.]){5}[0-9a-f]{2}|[0-9a-f]{4}(?:\.[0-9a-f]{4}){2}")
_IP_PATTERN = re.compile(r"(?<![0-9])(?:\d{1,3}\.){3}\d{1,3}(?![0-9])")


def _validate_fixture_privacy(value: Any, *, location: str = "catalog") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).casefold())
            if normalized in _FORBIDDEN_KEYS:
                raise ValueError(f"Private credential field is forbidden at {location}.{key}.")
            _validate_fixture_privacy(child, location=f"{location}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_fixture_privacy(child, location=f"{location}[{index}]")
        return
    if not isinstance(value, str):
        return
    for match in _IP_PATTERN.findall(value):
        address = ipaddress.ip_address(match)
        if address.is_unspecified:
            continue
        if address in _LINK_LOCAL_NETWORK:
            continue
        if not any(address in network for network in _DOCUMENTATION_NETWORKS):
            raise ValueError(
                f"Only RFC documentation IPv4 addresses are allowed at {location}."
            )
    for match in _MAC_PATTERN.findall(value):
        normalized = re.sub(r"[^0-9a-f]", "", match.casefold())
        # RFC 7042 documentation EUI-48 block: 00-00-5E-00-53-00/40.
        if not normalized.startswith("00005e0053"):
            raise ValueError(
                f"Only the documentation MAC block is allowed at {location}."
            )


def load_catalog(path: Path = CATALOG_PATH) -> ScenarioCatalog:
    payload = json.loads(path.read_text(encoding="utf-8"))
    _validate_fixture_privacy(payload)
    return ScenarioCatalog.model_validate(payload)


def scenario_by_id(scenario_id: str, path: Path = CATALOG_PATH) -> ResilienceScenario:
    catalog = load_catalog(path)
    try:
        return next(item for item in catalog.scenarios if item.id == scenario_id)
    except StopIteration as exc:
        raise KeyError(f"Unknown resilience scenario {scenario_id!r}.") from exc
