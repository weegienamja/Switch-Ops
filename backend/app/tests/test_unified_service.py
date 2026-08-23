from __future__ import annotations

from datetime import datetime, timezone

from app.identity_protection import IdentityProtector
from app.meraki_credentials import MerakiCredentialStore
from app.meraki_selection import MerakiSelectionStore
from app.unified_models import ProviderEntity, ProviderIdentifier, SourceHealth
from app.unified_service import UnifiedLabService
from app.unified_store import UnifiedLabStore


NOW = datetime(2026, 1, 15, 12, 30, tzinfo=timezone.utc)
PROTECTOR = IdentityProtector(key=b"synthetic-test-key-that-is-at-least-32-bytes")


class EmptyKeyring:
    def __init__(self) -> None:
        self.value: str | None = None

    def get_password(self, _service: str, _account: str) -> str | None:
        return self.value

    def set_password(self, _service: str, _account: str, value: str) -> None:
        self.value = value

    def delete_password(self, _service: str, _account: str) -> None:
        self.value = None


def _service(tmp_path, *, client_factory=lambda _key: None):
    keyring = EmptyKeyring()
    store = UnifiedLabStore(tmp_path / "unified.sqlite")
    service = UnifiedLabService(
        store=store,
        credential_store=MerakiCredentialStore(keyring),
        selection_store=MerakiSelectionStore(tmp_path / "selection.json"),
        protector=PROTECTOR,
        client_factory=client_factory,
    )
    return service, store, keyring


def test_optional_meraki_source_starts_no_thread_or_network_when_unconfigured(tmp_path) -> None:
    calls: list[str] = []
    service, store, _ = _service(
        tmp_path,
        client_factory=lambda key: calls.append(key),
    )

    service.start()
    result = service.test_connection()

    assert calls == []
    assert service._thread is None
    assert result.ok is False
    assert result.source_health.state == "not-configured"
    store.close()


def test_meraki_failure_health_does_not_delete_or_interrupt_catalyst_state(tmp_path) -> None:
    service, store, _ = _service(tmp_path)
    catalyst = ProviderEntity(
        id="cat-root",
        provider="catalyst-ios",
        providerRef="cat-root",
        label="Synthetic Catalyst",
        category="switch",
        identifiers=[],
        observedAt=NOW,
    )
    catalyst_health = SourceHealth(
        provider="catalyst-ios",
        state="healthy",
        detail="Catalyst is healthy.",
        checkedAt=NOW,
        lastSuccessAt=NOW,
        complete=True,
    )
    store.save_provider_state("catalyst-ios", [catalyst], [], catalyst_health)

    meraki_health = service.refresh_meraki()
    state = service.state()

    assert meraki_health.state == "not-configured"
    assert any(item.label == "Synthetic Catalyst" for item in state.entities)
    assert next(item for item in state.source_health if item.provider == "catalyst-ios").state == "healthy"
    store.close()


def test_local_candidate_confirmation_persists_and_merges_without_provider_write(tmp_path) -> None:
    service, store, _ = _service(tmp_path)
    name = PROTECTOR.protect("name", "synthetic-ap")
    catalyst = ProviderEntity(
        id="cat-ap", provider="catalyst-ios", providerRef="cat-ap",
        label="Synthetic AP", category="access-point", observedAt=NOW,
        identifiers=[ProviderIdentifier(
            kind="name", protectedValue=name, strength="weak", provenanceRef="cat-ap"
        )],
    )
    meraki = ProviderEntity(
        id="meraki-ap", provider="meraki-dashboard", providerRef="meraki-ap",
        label="Synthetic AP", category="access-point", observedAt=NOW,
        identifiers=[ProviderIdentifier(
            kind="name", protectedValue=name, strength="weak", provenanceRef="meraki-ap"
        )],
    )
    store.save_provider_state("catalyst-ios", [catalyst], [], SourceHealth(
        provider="catalyst-ios", state="healthy", detail="Synthetic", checkedAt=NOW, complete=True
    ))
    store.save_provider_state("meraki-dashboard", [meraki], [], SourceHealth(
        provider="meraki-dashboard", state="healthy", detail="Synthetic", checkedAt=NOW, complete=True
    ))
    initial = service.state()
    candidate = initial.identity_links[0]

    confirmed = service.decide_identity(candidate.id, "confirm")

    assert len(initial.entities) == 2
    assert len(confirmed.entities) == 1
    assert confirmed.identity_links[0].automatic is False
    assert store.load_identity_decisions()[candidate.id][0] == "confirm"
    reconsidered = service.decide_identity(candidate.id, "clear")
    assert len(reconsidered.entities) == 2
    assert store.load_identity_decisions() == {}
    store.close()
