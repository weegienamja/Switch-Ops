"""Lifecycle and local API orchestration for Unified Lab evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import threading
from typing import Callable

from .catalyst_evidence_adapter import normalize_catalyst_dashboard
from .identity_protection import IdentityProtector
from .credential_store import get_credential_store
from .meraki_client import MerakiApiError, MerakiClient
from .meraki_collector import MerakiEvidenceCollector
from .meraki_credentials import MerakiCredentialStore, get_meraki_credential_store
from .meraki_models import (
    MerakiConnectionTestResult,
    MerakiNetwork,
    MerakiOrganization,
    MerakiSelection,
    MerakiSetupStatus,
)
from .meraki_management import MerakiManagementEvidence
from .meraki_selection import MerakiSelectionStore, get_meraki_selection_store
from .models import DashboardResponse
from .unified_models import SourceHealth, UnifiedLabState
from .unified_reconciliation import reconcile_unified_state
from .unified_store import UnifiedLabStore, get_unified_lab_store


class UnifiedLabService:
    def __init__(
        self,
        *,
        store: UnifiedLabStore | None = None,
        credential_store: MerakiCredentialStore | None = None,
        selection_store: MerakiSelectionStore | None = None,
        protector: IdentityProtector | None = None,
        client_factory: Callable[[str], MerakiClient] = MerakiClient,
        management_target_provider: Callable[[], str | None] | None = None,
        refresh_seconds: float = 300.0,
    ) -> None:
        self._store = store or get_unified_lab_store()
        self._credentials = credential_store or get_meraki_credential_store()
        self._selections = selection_store or get_meraki_selection_store()
        self._protector = protector or IdentityProtector()
        self._client_factory = client_factory
        self._management_target_provider = management_target_provider or (lambda: None)
        self._refresh_seconds = max(60.0, refresh_seconds)
        self._collection_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self._credentials.load() or not self._selections.load():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._background_loop,
            name="switchops-meraki-evidence",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None

    def _background_loop(self) -> None:
        while not self._stop_event.is_set():
            self.refresh_meraki()
            if self._stop_event.wait(self._refresh_seconds):
                break

    def update_catalyst(self, dashboard: DashboardResponse) -> None:
        entities, claims, health = normalize_catalyst_dashboard(
            dashboard, protector=self._protector
        )
        self._store.save_provider_state("catalyst-ios", entities, claims, health)

    def state(self) -> UnifiedLabState:
        entities, claims, health = self._store.load_provider_states()
        providers = {item.provider for item in health}
        now = datetime.now(timezone.utc)
        if "catalyst-ios" not in providers:
            health.append(SourceHealth(
                provider="catalyst-ios", state="not-configured",
                detail="No Catalyst dashboard observation has been normalized yet.",
                checkedAt=now, complete=False,
            ))
        if "meraki-dashboard" not in providers:
            health.append(self._unconfigured_meraki_health(now))
        return reconcile_unified_state(
            entities,
            claims,
            health,
            identity_overrides=self._store.load_identity_decisions(),
            generated_at=now,
        )

    def setup_status(self) -> MerakiSetupStatus:
        raw = self._credentials.status()
        selection = self._selections.load()
        health = next(
            (item for item in self.state().source_health if item.provider == "meraki-dashboard"),
            self._unconfigured_meraki_health(datetime.now(timezone.utc)),
        )
        return MerakiSetupStatus(
            configured=bool(raw["configured"]),
            keyringAvailable=bool(raw["keyring_available"]),
            storage=str(raw["storage"]),
            selection=selection,
            sourceHealth=health,
        )

    def save_api_key(self, api_key: str) -> MerakiSetupStatus:
        self._credentials.save(api_key)
        self.start()
        return self.setup_status()

    def clear_api_key(self) -> MerakiSetupStatus:
        self.stop()
        self._credentials.clear()
        self._store.save_source_health(
            self._unconfigured_meraki_health(datetime.now(timezone.utc))
        )
        return self.setup_status()

    def save_selection(self, selection: MerakiSelection) -> MerakiSetupStatus:
        self._selections.save(selection)
        self.start()
        return self.setup_status()

    def test_connection(self) -> MerakiConnectionTestResult:
        checked_at = datetime.now(timezone.utc)
        key = self._credentials.load()
        if not key:
            health = self._unconfigured_meraki_health(checked_at)
            return MerakiConnectionTestResult(
                ok=False,
                summary="Store a Meraki API key in Windows Credential Manager first.",
                checkedAt=checked_at,
                organizationsVisible=0,
                sourceHealth=health,
            )
        try:
            with self._client_factory(key) as client:
                result = client.get("organizations")
            count = len(result.data) if isinstance(result.data, list) else 0
            health = SourceHealth(
                provider="meraki-dashboard",
                state="healthy" if result.complete else "partial",
                detail="Meraki accepted the read-only connection test.",
                checkedAt=checked_at,
                lastSuccessAt=checked_at,
                complete=result.complete,
                failedOperations=[] if result.complete else ["organizations"],
            )
            self._store.save_source_health(health)
            return MerakiConnectionTestResult(
                ok=True,
                summary="Meraki connection succeeded.",
                checkedAt=checked_at,
                organizationsVisible=count,
                sourceHealth=health,
            )
        except MerakiApiError as exc:
            health = self._failure_health(checked_at, "organizations", exc.code)
            self._store.save_source_health(health)
            return MerakiConnectionTestResult(
                ok=False,
                summary="Meraki connection failed. Check the key and Dashboard API access.",
                checkedAt=checked_at,
                organizationsVisible=0,
                sourceHealth=health,
            )

    def organizations(self) -> list[MerakiOrganization]:
        result = self._scoped_get("organizations")
        if not isinstance(result, list):
            return []
        return [
            MerakiOrganization(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or "Unnamed organization")[:160],
            )
            for item in result
            if item.get("id")
        ]

    def networks(self, organization_id: str) -> list[MerakiNetwork]:
        result = self._scoped_get(
            "networks", path_parameters={"organization_id": organization_id},
            query={"perPage": 1000},
        )
        if not isinstance(result, list):
            return []
        return [
            MerakiNetwork(
                id=str(item.get("id") or ""),
                organizationId=str(item.get("organizationId") or organization_id),
                name=str(item.get("name") or "Unnamed network")[:160],
                productTypes=[str(value)[:40] for value in item.get("productTypes", [])],
            )
            for item in result
            if item.get("id")
        ]

    def _scoped_get(self, operation: str, **kwargs: object) -> list[dict] | dict:
        key = self._credentials.load()
        if not key:
            raise RuntimeError("Meraki API key is not configured.")
        with self._client_factory(key) as client:
            result = client.get(operation, **kwargs)
        return result.data

    def refresh_meraki(self) -> SourceHealth:
        with self._collection_lock:
            now = datetime.now(timezone.utc)
            key = self._credentials.load()
            selection = self._selections.load()
            if not key or not selection:
                health = self._unconfigured_meraki_health(now)
                self._store.save_source_health(health)
                return health
            try:
                with self._client_factory(key) as client:
                    collection = MerakiEvidenceCollector(
                        client,
                        selection,
                        protector=self._protector,
                        management_target=self._management_target_provider(),
                    ).collect()
            except Exception:
                health = self._failure_health(now, "collection", "unavailable")
                self._store.save_source_health(health)
                return health
            if collection.entities or collection.source_health.state == "healthy":
                self._store.save_provider_state(
                    "meraki-dashboard",
                    collection.entities,
                    collection.claims,
                    collection.source_health,
                )
            else:
                self._store.save_source_health(collection.source_health)
            if collection.management_evidence.observed_at is not None:
                self._store.save_meraki_management_evidence(
                    collection.management_evidence
                )
            return collection.source_health

    def management_path_evidence(
        self, *, now: datetime | None = None
    ) -> MerakiManagementEvidence:
        checked_at = now or datetime.now(timezone.utc)
        stored = self._store.load_meraki_management_evidence()
        status = self.setup_status()
        health = status.source_health
        state = health.state
        normalized_state = (
            state
            if state in {"not-configured", "healthy", "partial", "unavailable"}
            else "unavailable"
        )
        if stored is None:
            return MerakiManagementEvidence.unavailable(
                checked_at=health.checked_at,
                state=normalized_state,  # type: ignore[arg-type]
                detail=health.detail,
                failed_operations=health.failed_operations,
            )
        management_operations = {
            "appliance_vlan_settings",
            "appliance_vlans",
            "appliance_single_lan",
            "appliance_ports",
            "appliance_lan_mode",
            "collection",
            "connection",
        }
        relevant_failures = sorted(
            operation
            for operation in health.failed_operations
            if operation in management_operations
        )
        if normalized_state == "not-configured":
            runtime_state = "not-configured"
            runtime_detail = health.detail
            runtime_complete = False
            runtime_failures = relevant_failures
        elif relevant_failures:
            runtime_state = "partial" if stored.observed_at else "unavailable"
            runtime_detail = health.detail
            runtime_complete = False
            runtime_failures = relevant_failures
        else:
            # Inventory, client, or uplink failures do not invalidate a compact
            # LAN/port snapshot that completed independently.
            runtime_state = stored.state
            runtime_detail = stored.detail
            runtime_complete = stored.complete
            runtime_failures = stored.failed_operations
        return stored.with_runtime_health(
            state=runtime_state,  # type: ignore[arg-type]
            checked_at=health.checked_at,
            detail=runtime_detail,
            complete=runtime_complete,
            failed_operations=runtime_failures,
            now=checked_at,
        )

    def decide_identity(self, link_id: str, decision: str) -> UnifiedLabState:
        current = self.state()
        link = next((item for item in current.identity_links if item.id == link_id), None)
        if decision == "clear":
            self._store.clear_identity_decision(link_id)
            return self.state()
        if not link:
            raise ValueError("Identity candidate was not found.")
        if link.state == "conflicted":
            raise ValueError("A strong identifier conflict cannot be manually merged.")
        if link.state != "candidate":
            raise ValueError("Only an ambiguous identity candidate can be confirmed or rejected.")
        self._store.set_identity_decision(link_id, decision)
        return self.state()

    @staticmethod
    def _unconfigured_meraki_health(at: datetime) -> SourceHealth:
        return SourceHealth(
            provider="meraki-dashboard",
            state="not-configured",
            detail="Meraki is optional and has not been fully configured.",
            checkedAt=at,
            complete=False,
        )

    @staticmethod
    def _failure_health(at: datetime, operation: str, code: str) -> SourceHealth:
        return SourceHealth(
            provider="meraki-dashboard",
            state="rate-limited" if code == "rate-limited" else "unavailable",
            detail="Meraki evidence is currently unavailable; Catalyst operation is unaffected.",
            checkedAt=at,
            complete=False,
            failedOperations=[operation],
        )


_service: UnifiedLabService | None = None


def get_unified_lab_service() -> UnifiedLabService:
    global _service
    if _service is None:
        def configured_target() -> str | None:
            credentials = get_credential_store().load()
            return credentials.host if credentials else None

        _service = UnifiedLabService(management_target_provider=configured_target)
    return _service
