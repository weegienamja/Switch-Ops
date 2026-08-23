from __future__ import annotations

from datetime import datetime, timezone

from app.identity_protection import IdentityProtector
from app.normalized_evidence import claim, provenance
from app.unified_models import ProviderEntity, ProviderIdentifier, SourceHealth
from app.unified_reconciliation import reconcile_unified_state
from app.unified_store import UnifiedLabStore


NOW = datetime(2026, 1, 15, 12, 10, tzinfo=timezone.utc)
PROTECTOR = IdentityProtector(key=b"synthetic-test-key-that-is-at-least-32-bytes")


def _entity(
    entity_id: str,
    provider: str,
    label: str,
    identifiers: list[ProviderIdentifier],
) -> ProviderEntity:
    return ProviderEntity(
        id=entity_id,
        provider=provider,
        providerRef=entity_id,
        label=label,
        category="access-point",
        model="MR44",
        identifiers=identifiers,
        observedAt=NOW,
    )


def _identifier(kind: str, token: str, strength: str, ref: str, **kwargs) -> ProviderIdentifier:
    return ProviderIdentifier(
        kind=kind,
        protectedValue=token,
        strength=strength,
        provenanceRef=ref,
        **kwargs,
    )


def _health(provider: str) -> SourceHealth:
    return SourceHealth(
        provider=provider,
        state="healthy",
        detail="Synthetic provider is healthy.",
        checkedAt=NOW,
        lastSuccessAt=NOW,
        complete=True,
    )


def _claim(provider: str, subject: str, field: str, value):
    source = provenance(
        provider=provider,
        source_kind="synthetic",
        source_object_ref=subject,
        observed_at=NOW,
        collected_at=NOW,
    )
    return claim(
        provider=provider,
        subject_ref=subject,
        field=field,
        value=value,
        strength="supporting",
        provenance_record=source,
    )


def test_confirmed_identity_merges_but_attributes_reconcile_independently() -> None:
    serial = PROTECTOR.serial("SYNTH-MR-0001")
    assert serial
    catalyst = _entity(
        "cat-mr",
        "catalyst-ios",
        "AP on Gi0/4",
        [_identifier("serial", serial, "strong", "cat-mr")],
    )
    meraki = _entity(
        "meraki-mr",
        "meraki-dashboard",
        "Synthetic MR44",
        [_identifier("serial", serial, "strong", "meraki-mr")],
    )
    claims = [
        _claim("catalyst-ios", "cat-mr", "availability", "online"),
        _claim("meraki-dashboard", "meraki-mr", "availability", "online"),
        _claim("catalyst-ios", "cat-mr", "name", "AP on Gi0/4"),
        _claim("meraki-dashboard", "meraki-mr", "name", "Synthetic MR44"),
    ]

    state = reconcile_unified_state(
        [catalyst, meraki],
        claims,
        [_health("catalyst-ios"), _health("meraki-dashboard")],
        generated_at=NOW,
    )

    assert len(state.entities) == 1
    unified = state.entities[0]
    assert unified.identity_state == "AGREED"
    attributes = {item.field: item for item in unified.attributes}
    assert attributes["identity"].state == "AGREED"
    assert attributes["availability"].state == "AGREED"
    assert attributes["name"].state == "CONFLICT"
    assert unified.label.startswith("Unified device")


def test_candidate_records_stay_separate_until_local_operator_confirmation() -> None:
    name = PROTECTOR.protect("name", "synthetic-mr44")
    catalyst = _entity(
        "cat-mr",
        "catalyst-ios",
        "Synthetic MR44",
        [_identifier("name", name, "weak", "cat-mr")],
    )
    meraki = _entity(
        "meraki-mr",
        "meraki-dashboard",
        "Synthetic MR44",
        [_identifier("name", name, "weak", "meraki-mr")],
    )
    initial = reconcile_unified_state(
        [catalyst, meraki], [], [], generated_at=NOW
    )

    assert len(initial.entities) == 2
    assert {item.identity_state for item in initial.entities} == {"AMBIGUOUS"}
    assert len(initial.identity_links) == 1
    assert initial.identity_links[0].state == "candidate"

    confirmed = reconcile_unified_state(
        [catalyst, meraki],
        [],
        [],
        generated_at=NOW,
        identity_overrides={initial.identity_links[0].id: ("confirm", NOW)},
    )

    assert len(confirmed.entities) == 1
    assert confirmed.identity_links[0].automatic is False


def test_strong_conflict_retains_two_entities_and_neither_provider_wins() -> None:
    address = PROTECTOR.management_address("192.0.2.44")
    assert address
    catalyst = _entity(
        "cat-mr",
        "catalyst-ios",
        "Synthetic MR44",
        [
            _identifier("serial", PROTECTOR.protect("serial", "LEFT"), "strong", "cat-mr"),
            _identifier("management-address", address, "supporting", "cat-mr"),
        ],
    )
    meraki = _entity(
        "meraki-mr",
        "meraki-dashboard",
        "Synthetic MR44",
        [
            _identifier("serial", PROTECTOR.protect("serial", "RIGHT"), "strong", "meraki-mr"),
            _identifier("management-address", address, "supporting", "meraki-mr"),
        ],
    )

    state = reconcile_unified_state([catalyst, meraki], [], [], generated_at=NOW)

    assert len(state.entities) == 2
    assert {item.identity_state for item in state.entities} == {"CONFLICT"}
    assert state.identity_links[0].state == "conflicted"
    assert len(state.conflicts) == 1


def test_normalized_store_round_trips_provider_state_and_local_decision(tmp_path) -> None:
    store = UnifiedLabStore(tmp_path / "unified.sqlite")
    serial = PROTECTOR.serial("SYNTH-MR-0001")
    assert serial
    entity = _entity(
        "meraki-mr",
        "meraki-dashboard",
        "Synthetic MR44",
        [_identifier("serial", serial, "strong", "meraki-mr")],
    )
    item = _claim("meraki-dashboard", entity.id, "availability", "online")
    entity.claim_ids = [item.id]

    store.save_provider_state(
        "meraki-dashboard", [entity], [item], _health("meraki-dashboard")
    )
    store.set_identity_decision("identity-link-synthetic", "confirm", decided_at=NOW)
    entities, claims, health = store.load_provider_states()

    assert entities == [entity]
    assert claims == [item]
    assert health == [_health("meraki-dashboard")]
    assert store.load_identity_decisions() == {
        "identity-link-synthetic": ("confirm", NOW)
    }
    store.close()
