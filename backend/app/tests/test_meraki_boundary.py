from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.meraki_client import BASE_URL, MerakiApiError, MerakiClient
from app.meraki_credentials import ACCOUNT_NAME, SERVICE_NAME, MerakiCredentialStore
from app.meraki_models import MerakiSelection
from app.meraki_selection import MerakiSelectionStore


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


def _client(handler, **kwargs) -> MerakiClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(
        base_url=BASE_URL,
        transport=transport,
        headers={"X-Cisco-Meraki-API-Key": "synthetic-secret-key"},
    )
    return MerakiClient("synthetic-secret-key", client=http_client, **kwargs)


def test_api_key_is_keyring_only_and_never_returned() -> None:
    keyring = FakeKeyring()
    store = MerakiCredentialStore(keyring)

    store.save("synthetic-secret-key")

    assert keyring.values[(SERVICE_NAME, ACCOUNT_NAME)] == "synthetic-secret-key"
    assert store.load() == "synthetic-secret-key"
    assert store.status() == {
        "configured": True,
        "keyring_available": True,
        "storage": "keyring",
    }
    assert "synthetic-secret-key" not in repr(store.status())

    store.clear()
    assert store.load() is None


def test_key_save_fails_closed_when_os_keyring_is_unavailable() -> None:
    store = MerakiCredentialStore(keyring_module=None)
    store._keyring = None

    with pytest.raises(RuntimeError, match="Credential Manager"):
        store.save("synthetic-secret-key")


def test_selection_store_contains_scope_only(tmp_path: Path) -> None:
    path = tmp_path / "selection.json"
    store = MerakiSelectionStore(path)
    selection = MerakiSelection(
        organizationId="ORG_SYNTHETIC",
        organizationName="Synthetic organization",
        networkId="NET_SYNTHETIC",
        networkName="Synthetic lab",
    )

    store.save(selection)

    assert store.load() == selection
    assert "api" not in path.read_text(encoding="utf-8").lower()


def test_only_named_get_operations_are_available() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json=[{"id": "ORG_SYNTHETIC", "name": "Synthetic"}])

    client = _client(handler)
    result = client.get("organizations")

    assert result.complete is True
    assert result.data == [{"id": "ORG_SYNTHETIC", "name": "Synthetic"}]
    assert methods == ["GET"]
    with pytest.raises(MerakiApiError, match="operation-not-allowed"):
        client.get("generic_proxy")
    with pytest.raises(MerakiApiError, match="query-not-allowed"):
        client.get("organizations", query={"url": "https://example.invalid"})


@pytest.mark.parametrize("operation,path", [
    ("appliance_vlan_settings", "/api/v1/networks/NET_SYNTH/appliance/vlans/settings"),
    ("appliance_vlans", "/api/v1/networks/NET_SYNTH/appliance/vlans"),
    ("appliance_single_lan", "/api/v1/networks/NET_SYNTH/appliance/singleLan"),
    ("appliance_ports", "/api/v1/networks/NET_SYNTH/appliance/ports"),
])
def test_management_configuration_operations_are_fixed_get_only(operation, path) -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json={})

    result = _client(handler).get(
        operation,
        path_parameters={"network_id": "NET_SYNTH"},
    )

    assert result.complete is True
    assert requests == [("GET", path)]


def test_pagination_follows_only_same_origin_allowlisted_queries() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(
                200,
                json=[{"serial": "SYNTH-1"}],
                headers={
                    "Link": f'<{BASE_URL}/organizations/ORG_SYNTH/devices?startingAfter=SYNTH-1&perPage=1>; rel="next"'
                },
            )
        return httpx.Response(200, json=[{"serial": "SYNTH-2"}])

    result = _client(handler).get(
        "organization_devices",
        path_parameters={"organization_id": "ORG_SYNTH"},
        query={"perPage": 1},
    )

    assert result.complete is True
    assert result.pages == 2
    assert [item["serial"] for item in result.data] == ["SYNTH-1", "SYNTH-2"]


def test_off_origin_pagination_is_rejected_without_contacting_it() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=[{"serial": "SYNTH-1"}],
            headers={"Link": '<https://attacker.invalid/steal>; rel="next"'},
        )

    result = _client(handler).get(
        "organization_devices",
        path_parameters={"organization_id": "ORG_SYNTH"},
    )
    assert calls == 1
    assert result.complete is False
    assert result.data == [{"serial": "SYNTH-1"}]
    assert result.failure_code == "pagination-origin-not-allowed"


def test_rate_limit_honors_bounded_retry_after() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "60"})
        return httpx.Response(200, json=[])

    result = _client(
        handler,
        sleep=sleeps.append,
        random_value=lambda: 0.0,
        max_retry_after=2.0,
    ).get("organizations")

    assert result.complete is True
    assert calls == 2
    assert sleeps == [2.0]


def test_later_page_failure_returns_safe_partial_result() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json=[{"serial": "SYNTH-1"}],
                headers={
                    "Link": f'<{BASE_URL}/organizations/ORG_SYNTH/devices?startingAfter=SYNTH-1>; rel="next"'
                },
            )
        return httpx.Response(503, text="synthetic upstream detail must not escape")

    result = _client(handler, max_retries=0).get(
        "organization_devices",
        path_parameters={"organization_id": "ORG_SYNTH"},
    )

    assert result.complete is False
    assert result.data == [{"serial": "SYNTH-1"}]
    assert result.failure_code in {"partial-failure", "server"}


def test_errors_never_include_api_key_or_response_body() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="body synthetic-secret-key private-detail")

    client = _client(handler, max_retries=0)
    with pytest.raises(MerakiApiError) as captured:
        client.get("organizations")

    message = str(captured.value)
    assert "synthetic-secret-key" not in message
    assert "private-detail" not in message
    assert captured.value.code == "authentication"
