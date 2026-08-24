"""Stateful orchestration for multi-device Lab Assurance."""
from __future__ import annotations

import hashlib
from threading import RLock

from .credential_store import get_credential_store
from .config import get_settings
from .interface_policy import device_key
from .lab_assurance import build_lab_assurance_state
from .lab_collector import LAB_COMMANDS, LabDeviceObservation, collect_lab_device
from .lab_device_store import get_lab_device_store
from .models import (
    ConfiguredLabDevice,
    LabAssuranceState,
    LabDeviceCreateRequest,
    LabDeviceList,
    PerformanceObservation,
)
from .performance_probes import run_bounded_probe
from .switch_client import connect_switch_client


class LabAssuranceService:
    def __init__(self) -> None:
        self._lock = RLock()
        self._performance: list[PerformanceObservation] = []
        self._route_signatures: dict[str, str] = {}
        self._state = build_lab_assurance_state([])

    def state(self) -> LabAssuranceState:
        with self._lock:
            return self._state.model_copy(deep=True)

    def configured_devices(self) -> LabDeviceList:
        extras = get_lab_device_store().list()
        status = get_credential_store().status()
        devices: list[ConfiguredLabDevice] = []
        if status.get("configured") or get_settings().mock_mode:
            host = str(status.get("switch_host") or "")
            devices.append(
                ConfiguredLabDevice(
                    id=(f"primary-{device_key(host)[:16]}" if host else "primary-mock"),
                    label="Primary Catalyst",
                    primary=True,
                    deviceType=str(status.get("switch_device_type") or "cisco_ios"),
                    storage="legacy",
                    configured=True,
                )
            )
        return LabDeviceList(
            keyringAvailable=extras.keyring_available,
            devices=devices + extras.devices,
        )

    def add_device(self, request: LabDeviceCreateRequest) -> ConfiguredLabDevice:
        primary_host = str(get_credential_store().status().get("switch_host") or "")
        if primary_host and request.host.casefold() == primary_host.casefold():
            raise RuntimeError("This target is already configured as the primary Catalyst.")
        store = get_lab_device_store()
        for item in store.list().devices:
            loaded = store.credentials(item.id)
            if loaded and loaded[1].switch_host.casefold() == request.host.casefold():
                raise RuntimeError("This target is already configured for Lab Assurance.")
        return store.add(request)

    def remove_device(self, device_id: str) -> bool:
        return get_lab_device_store().remove(device_id)

    def refresh(self, primary: LabDeviceObservation | None) -> LabAssuranceState:
        observations: list[LabDeviceObservation] = [primary] if primary else []
        store = get_lab_device_store()
        for item in store.list().devices:
            loaded = store.credentials(item.id)
            if loaded is None:
                failed = LabDeviceObservation(
                    device_id=item.id,
                    configured_label=item.label,
                    primary=False,
                    observed_at=self._state.generated_at,
                    outputs={symbol: "" for symbol in LAB_COMMANDS},
                    command_state={symbol: "failed" for symbol in LAB_COMMANDS},
                )
                observations.append(failed)
                continue
            label, credentials = loaded
            client = None
            try:
                client = connect_switch_client(credentials)
                observations.append(
                    collect_lab_device(
                        client,
                        device_id=item.id,
                        label=label,
                        primary=False,
                    )
                )
            except Exception:
                observations.append(
                    LabDeviceObservation(
                        device_id=item.id,
                        configured_label=label,
                        primary=False,
                        observed_at=self._state.generated_at,
                        outputs={symbol: "" for symbol in LAB_COMMANDS},
                        command_state={symbol: "failed" for symbol in LAB_COMMANDS},
                    )
                )
            finally:
                if client is not None:
                    client.close()
        with self._lock:
            performance = list(self._performance)
        next_state = build_lab_assurance_state(observations, performance=performance)
        with self._lock:
            self._state = next_state
            return self._state.model_copy(deep=True)

    def probe(self, target: str, label: str, count: int) -> PerformanceObservation:
        token = hashlib.sha256(target.strip().casefold().encode("utf-8")).hexdigest()[:16]
        observation, signature = run_bounded_probe(
            target,
            label=label,
            count=count,
            previous_route_signature=self._route_signatures.get(token),
        )
        with self._lock:
            if signature:
                self._route_signatures[token] = signature
            self._performance = (self._performance + [observation])[-50:]
            self._state.performance = list(self._performance)
        return observation


_service: LabAssuranceService | None = None


def get_lab_assurance_service() -> LabAssuranceService:
    global _service
    if _service is None:
        _service = LabAssuranceService()
    return _service
