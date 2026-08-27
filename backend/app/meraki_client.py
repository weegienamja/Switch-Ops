"""Strict allowlisted GET-only Meraki Dashboard API client."""
from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import random
import re
import time
from typing import Callable
from urllib.parse import parse_qsl, urlparse

import httpx

from .logging_config import register_secret


BASE_URL = "https://api.meraki.com/api/v1"
_BASE = urlparse(BASE_URL)
_PATH_VALUE = re.compile(r"[A-Za-z0-9_-]{1,128}")


@dataclass(frozen=True)
class OperationSpec:
    path: str
    path_parameters: frozenset[str]
    query_parameters: frozenset[str]


OPERATIONS: dict[str, OperationSpec] = {
    "organizations": OperationSpec("/organizations", frozenset(), frozenset()),
    "networks": OperationSpec(
        "/organizations/{organization_id}/networks",
        frozenset({"organization_id"}),
        frozenset({"perPage", "startingAfter", "endingBefore"}),
    ),
    "organization_devices": OperationSpec(
        "/organizations/{organization_id}/devices",
        frozenset({"organization_id"}),
        frozenset({"perPage", "startingAfter", "endingBefore", "networkIds[]", "productTypes[]"}),
    ),
    "device_availabilities": OperationSpec(
        "/organizations/{organization_id}/devices/availabilities",
        frozenset({"organization_id"}),
        frozenset({"perPage", "startingAfter", "endingBefore", "networkIds[]", "productTypes[]", "serials[]"}),
    ),
    "device_lldp_cdp": OperationSpec(
        "/devices/{serial}/lldpCdp",
        frozenset({"serial"}),
        frozenset(),
    ),
    "appliance_uplinks": OperationSpec(
        "/organizations/{organization_id}/appliance/uplink/statuses",
        frozenset({"organization_id"}),
        frozenset({"perPage", "startingAfter", "endingBefore", "networkIds[]", "serials[]", "iccids[]"}),
    ),
    "appliance_ports": OperationSpec(
        "/networks/{network_id}/appliance/ports",
        frozenset({"network_id"}),
        frozenset(),
    ),
    "appliance_vlan_settings": OperationSpec(
        "/networks/{network_id}/appliance/vlans/settings",
        frozenset({"network_id"}),
        frozenset(),
    ),
    "appliance_vlans": OperationSpec(
        "/networks/{network_id}/appliance/vlans",
        frozenset({"network_id"}),
        frozenset(),
    ),
    "appliance_single_lan": OperationSpec(
        "/networks/{network_id}/appliance/singleLan",
        frozenset({"network_id"}),
        frozenset(),
    ),
    "switch_port_statuses": OperationSpec(
        "/devices/{serial}/switch/ports/statuses",
        frozenset({"serial"}),
        frozenset({"t0", "timespan"}),
    ),
    "network_clients": OperationSpec(
        "/networks/{network_id}/clients",
        frozenset({"network_id"}),
        frozenset({"timespan", "perPage", "startingAfter", "endingBefore", "statuses[]"}),
    ),
}


class MerakiApiError(Exception):
    """Safe fixed-vocabulary error; response bodies and keys are never retained."""

    def __init__(self, code: str, operation: str, status_code: int | None = None) -> None:
        self.code = code
        self.operation = operation
        self.status_code = status_code
        super().__init__(f"Meraki operation {operation} failed ({code}).")


@dataclass(frozen=True)
class MerakiApiResult:
    data: list[dict] | dict
    complete: bool
    pages: int
    failure_code: str | None = None


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            return max(0.0, parsed.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


class MerakiClient:
    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
        max_retries: int = 3,
        max_retry_after: float = 30.0,
    ) -> None:
        register_secret(api_key)
        self._sleep = sleep
        self._random = random_value
        self._max_retries = max(0, max_retries)
        self._max_retry_after = max(0.0, max_retry_after)
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=BASE_URL,
            headers={
                "X-Cisco-Meraki-API-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "SwitchOps/0.7 read-only-evidence",
            },
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "MerakiClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def get(
        self,
        operation: str,
        *,
        path_parameters: dict[str, str] | None = None,
        query: dict[str, str | int | float | list[str]] | None = None,
    ) -> MerakiApiResult:
        spec = OPERATIONS.get(operation)
        if spec is None:
            raise MerakiApiError("operation-not-allowed", operation)
        path_parameters = path_parameters or {}
        query = query or {}
        if set(path_parameters) != set(spec.path_parameters):
            raise MerakiApiError("invalid-path-parameters", operation)
        if not set(query).issubset(spec.query_parameters):
            raise MerakiApiError("query-not-allowed", operation)
        for value in path_parameters.values():
            if not _PATH_VALUE.fullmatch(str(value)):
                raise MerakiApiError("invalid-path-parameters", operation)
        path = spec.path.format(**path_parameters)
        return self._get_paginated(operation, spec, path, query)

    def _get_paginated(
        self,
        operation: str,
        spec: OperationSpec,
        path: str,
        query: dict[str, str | int | float | list[str]],
    ) -> MerakiApiResult:
        data: list[dict] = []
        first_dict: dict | None = None
        pages = 0
        next_url: str | None = path
        next_query: dict[str, object] | None = dict(query)
        while next_url:
            try:
                response = self._request_with_retry(operation, next_url, next_query)
            except MerakiApiError as exc:
                if pages:
                    return MerakiApiResult(
                        data=data if first_dict is None else first_dict,
                        complete=False,
                        pages=pages,
                        failure_code=exc.code,
                    )
                raise
            try:
                payload = response.json()
                if not isinstance(payload, (list, dict)):
                    raise MerakiApiError("invalid-response", operation, response.status_code)
            except (MerakiApiError, ValueError) as exc:
                if pages:
                    return MerakiApiResult(
                        data=data if first_dict is None else first_dict,
                        complete=False,
                        pages=pages,
                        failure_code=(exc.code if isinstance(exc, MerakiApiError) else "invalid-response"),
                    )
                raise MerakiApiError("invalid-response", operation) from None
            pages += 1
            if isinstance(payload, list):
                data.extend(item for item in payload if isinstance(item, dict))
            else:
                if pages > 1:
                    return MerakiApiResult(data=data, complete=False, pages=pages, failure_code="invalid-pagination")
                first_dict = payload
            candidate = response.links.get("next", {}).get("url")
            if not candidate:
                next_url = None
                continue
            try:
                next_url = self._validate_next_url(operation, spec, str(candidate))
            except MerakiApiError as exc:
                return MerakiApiResult(
                    data=data if first_dict is None else first_dict,
                    complete=False,
                    pages=pages,
                    failure_code=exc.code,
                )
            next_query = None
        return MerakiApiResult(data=first_dict if first_dict is not None else data, complete=True, pages=pages)

    def _request_with_retry(
        self,
        operation: str,
        url: str,
        query: dict[str, object] | None,
    ) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request("GET", url, params=query)
            except httpx.HTTPError:
                if attempt >= self._max_retries:
                    raise MerakiApiError("transport", operation) from None
                self._sleep(min(8.0, (2**attempt) + self._random()))
                continue
            if 200 <= response.status_code < 300:
                return response
            if response.status_code in {401, 403}:
                raise MerakiApiError("authentication", operation, response.status_code)
            if response.status_code == 404:
                raise MerakiApiError("not-found", operation, response.status_code)
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt >= self._max_retries:
                code = "rate-limited" if response.status_code == 429 else "server" if response.status_code >= 500 else "request"
                raise MerakiApiError(code, operation, response.status_code)
            if response.status_code == 429:
                delay = _retry_after_seconds(response.headers.get("Retry-After"))
                delay = min(self._max_retry_after, delay if delay is not None else 1.0 + self._random())
            else:
                delay = min(8.0, (2**attempt) + self._random())
            self._sleep(delay)
        raise MerakiApiError("request", operation)

    @staticmethod
    def _validate_next_url(operation: str, spec: OperationSpec, candidate: str) -> str:
        parsed = urlparse(candidate)
        if parsed.scheme != _BASE.scheme or parsed.netloc != _BASE.netloc:
            raise MerakiApiError("pagination-origin-not-allowed", operation)
        if not parsed.path.startswith(f"{_BASE.path}/"):
            raise MerakiApiError("pagination-path-not-allowed", operation)
        query_keys = {key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        if not query_keys.issubset(spec.query_parameters):
            raise MerakiApiError("pagination-query-not-allowed", operation)
        return candidate
