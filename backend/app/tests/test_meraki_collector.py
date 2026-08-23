from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.identity_protection import IdentityProtector
from app.meraki_client import MerakiApiError, MerakiApiResult
from app.meraki_collector import MerakiEvidenceCollector
from app.meraki_models import MerakiSelection


FIXTURES = Path(__file__).parent / "fixtures" / "unified_lab"
NOW = datetime(2026, 1, 15, 12, 5, tzinfo=timezone.utc)
PROTECTOR = IdentityProtector(key=b"synthetic-test-key-that-is-at-least-32-bytes")


class FakeClient:
    def __init__(self, responses: dict[str, list[dict] | dict], failures: set[str] | None = None) -> None:
        self.responses = responses
        self.failures = failures or set()
        self.calls: list[str] = []

    def get(self, operation: str, **_kwargs) -> MerakiApiResult:
        self.calls.append(operation)
        if operation in self.failures:
            raise MerakiApiError("server", operation, 503)
        return MerakiApiResult(data=self.responses.get(operation, []), complete=True, pages=1)


def _payloads() -> tuple[dict, dict]:
    mx = json.loads((FIXTURES / "meraki_mx.json").read_text(encoding="utf-8"))
    mr = json.loads((FIXTURES / "meraki_mr.json").read_text(encoding="utf-8"))
    return mx, mr


def _collector(*, failures: set[str] | None = None) -> tuple[MerakiEvidenceCollector, FakeClient]:
    mx, mr = _payloads()
    mx_device = {**mx["device"], "hugeRawField": "must-not-survive" * 1000}
    mr_device = {**mr["device"], "hugeRawField": "must-not-survive" * 1000}
    responses = {
        "organization_devices": [mx_device, mr_device],
        "device_availabilities": [
            {"serial": mx["device"]["serial"], "status": "online", "lastReportedAt": mx["observedAt"]},
            {"serial": mr["device"]["serial"], "status": "online", "lastReportedAt": mr["observedAt"]},
        ],
        "appliance_uplinks": [
            {"serial": mx["device"]["serial"], "uplinks": mx["uplinks"]}
        ],
        "appliance_ports": mx["ports"],
        "device_lldp_cdp": {
            "ports": {
                "eth0": {
                    "lldp": {
                        "systemName": "synthetic-catalyst-01",
                        "portId": "Gi0/4",
                        "chassisId": "00:00:5e:00:53:10"
                    }
                }
            }
        },
        "network_clients": mr["clients"],
    }
    client = FakeClient(responses, failures)
    selection = MerakiSelection(
        organizationId="ORG_SYNTHETIC",
        organizationName="Synthetic organization",
        networkId="N_SYNTHETIC_001",
        networkName="Synthetic lab",
    )
    return (
        MerakiEvidenceCollector(
            client, selection, protector=PROTECTOR, now=lambda: NOW
        ),
        client,
    )


def test_collector_normalizes_inventory_availability_uplinks_ports_lldp_and_clients() -> None:
    collector, client = _collector()

    result = collector.collect()

    categories = {entity.category for entity in result.entities}
    fields = {item.field for item in result.claims}
    assert {"security-appliance", "access-point", "client"}.issubset(categories)
    assert {"existence", "availability", "uplink", "port", "relationship", "attachment"}.issubset(fields)
    assert result.source_health.state == "healthy"
    assert "switch_port_statuses" not in client.calls
    assert client.calls.count("device_lldp_cdp") == 2


def test_normalized_envelope_retains_no_raw_serial_mac_ip_client_or_unknown_fields() -> None:
    collector, _ = _collector()
    result = collector.collect()
    serialized = json.dumps(
        {
            "entities": [item.model_dump(by_alias=True, mode="json") for item in result.entities],
            "claims": [item.model_dump(by_alias=True, mode="json") for item in result.claims],
        },
        sort_keys=True,
    )

    for forbidden in (
        "SYNTH-MX-0001",
        "SYNTH-MR-0001",
        "00:00:5e:00:53:68",
        "00:00:5e:00:53:44",
        "02:00:00:00:00:01",
        "192.0.2.44",
        "198.51.100.20",
        "must-not-survive",
        "hugeRawField",
    ):
        assert forbidden not in serialized


def test_optional_operation_failure_preserves_normalized_partial_evidence() -> None:
    collector, _ = _collector(failures={"appliance_uplinks", "network_clients"})

    result = collector.collect()

    assert result.entities
    assert result.source_health.state == "partial"
    assert result.source_health.complete is False
    assert result.source_health.failed_operations == ["appliance_uplinks", "network_clients"]


def test_inventory_failure_is_unavailable_and_returns_no_provider_state() -> None:
    collector, _ = _collector(failures={"organization_devices"})

    result = collector.collect()

    assert result.entities == []
    assert result.claims == []
    assert result.source_health.state == "unavailable"
    assert result.source_health.failed_operations == ["organization_devices"]
