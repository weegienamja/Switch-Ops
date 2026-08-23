from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.identity_protection import (
    IdentityProtector,
    is_globally_administered_device_mac,
)
from app.identity_resolution import (
    resolve_cross_provider_identities,
    resolve_identity_pair,
)
from app.unified_models import ProviderEntity, ProviderIdentifier


FIXTURES = Path(__file__).parent / "fixtures" / "unified_lab"
NOW = datetime(2026, 1, 15, 12, 1, tzinfo=timezone.utc)
PROTECTOR = IdentityProtector(key=b"synthetic-test-key-that-is-at-least-32-bytes")


def _identifier(
    kind: str,
    value: str,
    strength: str,
    provenance: str,
    *,
    global_mac: bool | None = None,
) -> ProviderIdentifier:
    return ProviderIdentifier(
        kind=kind,
        protectedValue=PROTECTOR.protect(kind, value),
        strength=strength,
        globallyAdministered=global_mac,
        provenanceRef=provenance,
    )


def _entity(
    entity_id: str,
    provider: str,
    identifiers: list[ProviderIdentifier],
    *,
    label: str = "Synthetic device",
) -> ProviderEntity:
    return ProviderEntity(
        id=entity_id,
        provider=provider,
        providerRef=f"fixture:{entity_id}",
        label=label,
        identifiers=identifiers,
        observedAt=NOW,
    )


def test_all_unified_lab_fixtures_are_explicitly_synthetic() -> None:
    for path in sorted(FIXTURES.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["fixture"] == "synthetic-unified-lab-v1"
        assert "example" not in path.read_text(encoding="utf-8").lower()


def test_exact_serial_confirms_cross_provider_identity() -> None:
    protected = PROTECTOR.serial("SYNTH-MR-0001")
    assert protected
    left = _entity(
        "catalyst-neighbor-mr",
        "catalyst-ios",
        [
            ProviderIdentifier(
                kind="serial",
                protectedValue=protected,
                strength="strong",
                provenanceRef="catalyst:cdp:Gi0/4",
            )
        ],
    )
    right = _entity(
        "meraki-mr",
        "meraki-dashboard",
        [
            ProviderIdentifier(
                kind="serial",
                protectedValue=protected,
                strength="strong",
                provenanceRef="meraki:inventory:SYNTH-MR",
            )
        ],
    )

    link, conflicts = resolve_identity_pair(left, right, evaluated_at=NOW)

    assert link.state == "confirmed"
    assert conflicts == []
    assert link.reasons[0].strength == "strong"


def test_chassis_mac_and_device_mac_are_one_strong_identity_family() -> None:
    protected = PROTECTOR.protect("hardware", "00:00:5e:00:53:44")
    left = _entity(
        "catalyst-neighbor-mr",
        "catalyst-ios",
        [
            ProviderIdentifier(
                kind="chassis-mac",
                protectedValue=protected,
                strength="strong",
                globallyAdministered=True,
                provenanceRef="catalyst:lldp:Gi0/4",
            )
        ],
    )
    right = _entity(
        "meraki-mr",
        "meraki-dashboard",
        [
            ProviderIdentifier(
                kind="device-mac",
                protectedValue=protected,
                strength="strong",
                globallyAdministered=True,
                provenanceRef="meraki:inventory:SYNTH-MR",
            )
        ],
    )

    link, conflicts = resolve_identity_pair(left, right, evaluated_at=NOW)

    assert link.state == "confirmed"
    assert conflicts == []


def test_management_address_and_reciprocal_adjacency_remain_candidate_only() -> None:
    address = PROTECTOR.management_address("192.0.2.44")
    adjacency = PROTECTOR.protect("reciprocal-adjacency", "cat:gi0/4|mr:eth0")
    assert address
    left = _entity(
        "catalyst-neighbor-mr",
        "catalyst-ios",
        [
            ProviderIdentifier(
                kind="management-address",
                protectedValue=address,
                strength="supporting",
                provenanceRef="catalyst:lldp:Gi0/4",
            ),
            ProviderIdentifier(
                kind="reciprocal-adjacency",
                protectedValue=adjacency,
                strength="supporting",
                provenanceRef="catalyst:lldp:Gi0/4",
            ),
        ],
    )
    right = _entity(
        "meraki-mr",
        "meraki-dashboard",
        [
            ProviderIdentifier(
                kind="management-address",
                protectedValue=address,
                strength="supporting",
                provenanceRef="meraki:inventory:SYNTH-MR",
            ),
            ProviderIdentifier(
                kind="reciprocal-adjacency",
                protectedValue=adjacency,
                strength="supporting",
                provenanceRef="meraki:lldp:SYNTH-MR",
            ),
        ],
    )

    link, conflicts = resolve_identity_pair(left, right, evaluated_at=NOW)

    assert link.state == "candidate"
    assert conflicts == []


def test_name_hint_never_auto_merges() -> None:
    hint = PROTECTOR.protect("name", "synthetic-mr44")
    left = _entity(
        "left",
        "catalyst-ios",
        [_identifier("name", hint, "weak", "catalyst:lldp:Gi0/4")],
    )
    right = _entity(
        "right",
        "meraki-dashboard",
        [_identifier("name", hint, "weak", "meraki:inventory:mr")],
    )

    link, _ = resolve_identity_pair(left, right, evaluated_at=NOW)

    assert link.state == "candidate"


def test_strong_disagreement_blocks_merge_when_pair_is_otherwise_plausible() -> None:
    name = PROTECTOR.protect("name", "same-label")
    left = _entity(
        "left",
        "catalyst-ios",
        [
            _identifier("serial", "serial-left", "strong", "catalyst:inventory"),
            _identifier("name", name, "weak", "catalyst:lldp"),
        ],
    )
    right = _entity(
        "right",
        "meraki-dashboard",
        [
            _identifier("serial", "serial-right", "strong", "meraki:inventory"),
            _identifier("name", name, "weak", "meraki:inventory"),
        ],
    )

    link, conflicts = resolve_identity_pair(left, right, evaluated_at=NOW)

    assert link.state == "conflicted"
    assert len(conflicts) == 1
    assert conflicts[0].field == "serial"


def test_unrelated_serialized_devices_do_not_create_conflicts() -> None:
    left = _entity(
        "left",
        "catalyst-ios",
        [_identifier("serial", "serial-left", "strong", "catalyst:inventory")],
    )
    right = _entity(
        "right",
        "meraki-dashboard",
        [_identifier("serial", "serial-right", "strong", "meraki:inventory")],
    )

    links, conflicts = resolve_cross_provider_identities([left, right], evaluated_at=NOW)

    assert links == []
    assert conflicts == []


def test_duplicate_strong_identifier_is_ambiguous_not_an_automatic_merge() -> None:
    serial = PROTECTOR.serial("SYNTH-DUPLICATE")
    assert serial
    catalyst = _entity(
        "cat",
        "catalyst-ios",
        [ProviderIdentifier(
            kind="serial", protectedValue=serial, strength="strong", provenanceRef="cat"
        )],
    )
    meraki_a = _entity(
        "meraki-a",
        "meraki-dashboard",
        [ProviderIdentifier(
            kind="serial", protectedValue=serial, strength="strong", provenanceRef="meraki-a"
        )],
    )
    meraki_b = _entity(
        "meraki-b",
        "meraki-dashboard",
        [ProviderIdentifier(
            kind="serial", protectedValue=serial, strength="strong", provenanceRef="meraki-b"
        )],
    )

    links, _ = resolve_cross_provider_identities(
        [catalyst, meraki_a, meraki_b], evaluated_at=NOW
    )

    assert len(links) == 2
    assert {link.state for link in links} == {"candidate"}


def test_locally_administered_mac_is_not_a_durable_identifier() -> None:
    assert is_globally_administered_device_mac("00:00:5e:00:53:44") is True
    assert is_globally_administered_device_mac("02:00:00:00:00:01") is False
    assert PROTECTOR.hardware_mac("02:00:00:00:00:01") is None
