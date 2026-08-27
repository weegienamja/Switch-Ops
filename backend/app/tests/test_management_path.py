from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main
from app import management_path
from app.management_path import (
    LastKnownManagementPath,
    ManagementPathDiagnosis,
    ManagementPathObservation,
    ManagementPathResponse,
    ManagementPathService,
    ManagementPathStore,
    ManagementRoute,
    diagnose_management_path,
)
from app.meraki_management import MerakiManagementEvidence
from app.recovery_plan import build_recovery_plan


NOW = datetime(2026, 8, 25, 10, 39, 13, tzinfo=timezone.utc)


def observation(
    *,
    source: str | None = "198.18.20.5",
    prefix: int | None = 24,
    route_kind: str = "default",
    next_hop: str | None = "198.18.20.1",
    tcp22: str = "timed_out",
    on_prefix: bool | None = False,
) -> ManagementPathObservation:
    return ManagementPathObservation(
        observedAt=NOW,
        adapterId="adapter-stable",
        adapterName="Ethernet",
        interfaceIndex=16,
        adapterState="Up",
        sourceIp=source,
        prefixLength=prefix,
        connectedPrefix=f"{source.rsplit('.', 1)[0]}.0/{prefix}" if source and prefix else None,
        targetOnConnectedPrefix=on_prefix,
        dhcpEnabled=True,
        dhcpServer="198.18.20.1",
        dhcpLeaseObtained=NOW,
        defaultGateway=next_hop,
        route=ManagementRoute(
            destinationPrefix=("0.0.0.0/0" if route_kind == "default" else None),
            nextHop=next_hop,
            kind=route_kind,
        ),
        windowsConnectivity="Internet",
        tcp22=tcp22,
        icmpReachable=False,
    )


def last_good(*, same_adapter: bool = True) -> LastKnownManagementPath:
    return LastKnownManagementPath(
        observedAt=NOW - timedelta(minutes=1),
        lastDeviceSuccessAt=NOW - timedelta(seconds=10),
        adapterId="adapter-stable",
        adapterName="Ethernet",
        sourceIp="198.18.10.95",
        prefixLength=24,
        connectedPrefix="198.18.10.0/24",
        defaultGateway="198.18.10.1",
        catalystInterface="Gi0/2",
        sameAdapterAsCurrent=same_adapter,
        provenance=["management-path-history"],
    )


def diagnose(current: ManagementPathObservation, historical=None, **status):
    return diagnose_management_path(
        target="198.18.10.10",
        current=current,
        last_known_good=historical,
        session_status={"state": "offline", "errorCode": "switch_unreachable", **status},
    )


def test_dhcp_subnet_change_on_same_adapter_is_high_confidence():
    result = diagnose(observation(), last_good())
    assert result.conclusion == "HOST_NETWORK_CHANGED"
    assert result.confidence == "HIGH"
    assert any("10 seconds" in item for item in result.evidence)


def test_changed_subnet_without_timeline_is_not_overstated():
    historical = last_good(same_adapter=False)
    historical.last_device_success_at = None
    result = diagnose(observation(), historical)
    assert result.conclusion == "HOST_NETWORK_CHANGED"
    assert result.confidence == "MEDIUM"


def test_explicitly_restored_connected_path_and_live_session_are_healthy():
    current = observation(
        source="198.18.10.95",
        route_kind="connected",
        next_hop="0.0.0.0",
        tcp22="reachable",
        on_prefix=True,
    )
    result = diagnose_management_path(
        target="198.18.10.10",
        current=current,
        last_known_good=last_good(),
        session_status={"state": "live", "errorCode": None},
    )
    assert result.conclusion == "MANAGEMENT_PATH_HEALTHY"
    assert result.confidence == "HIGH"


def test_no_selected_route_is_distinct_from_device_failure():
    result = diagnose(
        observation(source=None, prefix=None, route_kind="none", next_hop=None)
    )
    assert result.conclusion == "HOST_ROUTE_MISSING"
    assert result.confidence == "HIGH"


def test_tcp_refusal_proves_a_routed_response_not_device_offline():
    result = diagnose(observation(tcp22="refused"), last_good())
    assert result.conclusion == "SSH_SERVICE_UNAVAILABLE"
    assert result.confidence == "HIGH"


def test_stable_direct_path_timeout_remains_indeterminate():
    current = observation(
        source="198.18.10.95",
        route_kind="connected",
        next_hop="0.0.0.0",
        on_prefix=True,
    )
    result = diagnose(current, last_good())
    assert result.conclusion == "DEVICE_OR_PATH_UNREACHABLE"
    assert result.confidence == "INDETERMINATE"


def test_no_history_reports_degraded_path_without_inventing_a_cause():
    result = diagnose(observation(), None)
    assert result.conclusion == "HOST_PATH_DEGRADED"
    assert result.confidence == "MEDIUM"
    assert any("last-known-good" in item for item in result.missing_evidence)


def test_typed_session_evidence_remains_authoritative():
    for code, conclusion in (
        ("switch_auth_failed", "AUTHENTICATION_FAILED"),
        ("host_key_changed", "HOST_KEY_CHANGED"),
        ("ssh_negotiation_failed", "SSH_NEGOTIATION_FAILED"),
    ):
        result = diagnose_management_path(
            target="198.18.10.10",
            current=observation(tcp22="reachable"),
            last_known_good=last_good(),
            session_status={"state": "offline", "errorCode": code},
        )
        assert result.conclusion == conclusion
        assert result.confidence == "HIGH"


def test_management_path_store_retains_last_known_good_across_restart(tmp_path):
    path = tmp_path / "management.sqlite"
    first = ManagementPathStore(path)
    first.record("192.0.2.10", observation(), known_good=True)
    first.record(
        "192.0.2.10",
        observation(source="198.51.100.5", tcp22="timed_out"),
        known_good=False,
    )

    restored = ManagementPathStore(path).last_known_good("192.0.2.10")
    assert restored is not None
    assert restored.source_ip == "198.18.20.5"


def test_management_path_store_coalesces_unchanged_observations(tmp_path):
    path = tmp_path / "management.sqlite"
    store = ManagementPathStore(path)
    store.record("192.0.2.10", observation(), known_good=False)
    later = observation().model_copy(update={"observed_at": NOW + timedelta(minutes=1)})
    store.record("192.0.2.10", later, known_good=False)

    import sqlite3

    with sqlite3.connect(path) as connection:
        assert connection.execute("select count(*) from management_path_observations").fetchone()[0] == 1


def test_legacy_catalyst_and_local_host_history_bootstrap_the_incident(tmp_path):
    current = observation()
    observer = SimpleNamespace(
        observe=lambda _target: SimpleNamespace(
            public=current,
            adapter_mac="d8bbc1d9f507",
        )
    )
    telemetry = SimpleNamespace(
        latest_successful_observation_at=lambda _device: NOW - timedelta(seconds=10)
    )
    configuration = SimpleNamespace(
        management_context_for_target=lambda _target: {
            "device_id": "switch-physical-fixture",
            "management_mask": "255.255.255.0",
            "gateway": "198.18.10.1",
        }
    )
    discovery = SimpleNamespace(
        latest_local_host=lambda _device: {
            "last_seen": NOW - timedelta(minutes=1),
            "interface": "Gi0/2",
            "ip": "198.18.10.95",
            "mac": "d8bb.c1d9.f507",
        }
    )
    service = ManagementPathService(
        observer=observer,
        store=ManagementPathStore(tmp_path / "management.sqlite"),
        telemetry_store=telemetry,
        configuration_store=configuration,
        discovery_store=discovery,
    )

    result = service.assess(
        "198.18.10.10",
        {"state": "offline", "errorCode": "switch_unreachable"},
    )
    assert result.last_known_good is not None
    assert result.last_known_good.catalyst_interface == "Gi0/2"
    assert result.last_known_good.prefix_length is None
    assert result.last_known_good.connected_prefix is None
    assert result.last_known_good.default_gateway is None
    assert result.last_known_good.management_prefix == "198.18.10.0/24"
    assert result.last_known_good.catalyst_gateway == "198.18.10.1"
    assert result.last_known_good.same_adapter_as_current is True
    assert result.diagnosis.conclusion == "HOST_NETWORK_CHANGED"
    assert result.diagnosis.confidence == "HIGH"


def test_optional_history_failures_degrade_without_losing_current_evidence():
    current = observation()
    observer = SimpleNamespace(
        observe=lambda _target: SimpleNamespace(public=current, adapter_mac=None)
    )
    broken = SimpleNamespace(
        last_known_good=lambda _target: (_ for _ in ()).throw(RuntimeError("broken")),
        record=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broken")),
    )
    telemetry = SimpleNamespace(
        latest_successful_observation_at=lambda _device: (_ for _ in ()).throw(RuntimeError("broken"))
    )
    configuration = SimpleNamespace(
        management_context_for_target=lambda _target: (_ for _ in ()).throw(RuntimeError("broken"))
    )
    service = ManagementPathService(
        observer=observer,
        store=broken,
        telemetry_store=telemetry,
        configuration_store=configuration,
        discovery_store=SimpleNamespace(),
    )

    result = service.assess(
        "198.18.10.10",
        {"state": "offline", "errorCode": "switch_unreachable"},
    )

    assert result.last_known_good is None
    assert result.current.source_ip == "198.18.20.5"
    assert result.diagnosis.conclusion == "HOST_PATH_DEGRADED"


def test_api_uses_only_the_stored_target(monkeypatch):
    seen: list[str] = []
    current = observation()
    diagnosis = ManagementPathDiagnosis(
        conclusion="HOST_PATH_DEGRADED",
        confidence="MEDIUM",
        headline="Management target is no longer on-link",
        summary="Bounded fixture diagnosis.",
    )
    meraki = MerakiManagementEvidence.unavailable(
        checked_at=NOW,
        state="not-configured",
        detail="Synthetic Meraki evidence is not configured.",
    )
    response = ManagementPathResponse(
        current=current,
        diagnosis=diagnosis,
        merakiEvidence=meraki,
        recoveryPlan=build_recovery_plan(
            target="198.18.10.10",
            current=current,
            last_known_good=None,
            diagnosis=diagnosis,
            meraki=meraki,
            now=NOW,
        ),
        remediationAvailable=False,
    )
    service = SimpleNamespace(
        assess=lambda target, _status: seen.append(target) or response
    )
    monkeypatch.setattr(main, "_current_device_host", lambda: "198.18.10.10")
    monkeypatch.setattr(main, "get_management_path_service", lambda: service)
    monkeypatch.setattr(
        main,
        "get_device_session",
        lambda: SimpleNamespace(status=lambda: {"state": "offline"}),
    )

    result = TestClient(main.app).get(
        "/api/management-path?targetAddress=203.0.113.77"
    )
    assert result.status_code == 200
    assert seen == ["198.18.10.10"]
    assert result.json()["remediationAvailable"] is False
    architecture = result.json()["recoveryPlan"]["executionArchitecture"]
    assert architecture["mode"] == "PLANNING_ONLY"
    assert architecture["executorImplemented"] is False
    assert architecture["approvalAvailable"] is False
    assert "EXECUTOR_NOT_IMPLEMENTED" in architecture["gate"]["reasons"]


def test_windows_observer_uses_a_fixed_read_only_program(monkeypatch):
    calls = []

    def fixed_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "interfaceIndex": 7,
                "adapterName": "Fixture Ethernet",
                "adapterGuid": "fixture-guid",
                "adapterMac": "00-11-22-33-44-55",
                "adapterState": "Up",
                "interfaceMetric": 25,
                "sourceIp": "192.0.2.95",
                "prefixLength": 24,
                "adapterAddresses": [{
                    "address": "192.0.2.95",
                    "prefixLength": 24,
                    "prefixOrigin": "Dhcp",
                    "addressState": "Preferred",
                    "skipAsSource": False,
                }],
                "routePrefix": "192.0.2.0/24",
                "nextHop": "0.0.0.0",
                "routeMetric": 0,
                "routeProtocol": "NetMgmt",
                "dhcpEnabled": True,
                "dhcpStaticCoexistence": False,
                "windowsConnectivity": "Internet",
            }),
        )

    monkeypatch.setattr(management_path.os, "name", "nt")
    monkeypatch.setattr(management_path.subprocess, "run", fixed_run)
    monkeypatch.setattr(management_path, "_tcp_probe", lambda *_args: "reachable")
    monkeypatch.setattr(management_path, "_icmp_probe", lambda *_args: True)

    result = management_path.WindowsManagementPathObserver().observe("192.0.2.10")
    assert result.public.source_ip == "192.0.2.95"
    assert result.public.interface_metric == 25
    assert result.public.route.route_metric == 0
    assert result.public.adapter_addresses[0].address_state == "Preferred"
    assert result.public.dhcp_static_coexistence is False
    assert len(calls) == 1
    args, kwargs = calls[0]
    script = args[-1]
    assert "192.0.2.10" not in script
    assert kwargs["env"]["SWITCHOPS_MANAGEMENT_TARGET"] == "192.0.2.10"
    assert kwargs["shell"] is False
    assert "Find-NetRoute" in script
    assert "Get-NetIPAddress" in script
    assert "netsh.exe interface ipv4 show interface" in script
    netsh_lines = [line.strip().casefold() for line in script.splitlines() if "netsh.exe" in line]
    assert netsh_lines and all(" show " in line for line in netsh_lines)
    for forbidden in (
        "Invoke-Expression",
        "Start-Process",
        "New-NetIPAddress",
        "Set-NetIPAddress",
        "Set-NetIPInterface",
        "Remove-NetIPAddress",
        "New-NetRoute",
        "Remove-NetRoute",
    ):
        assert forbidden not in script


def test_windows_collection_failure_is_indeterminate_not_a_missing_route(monkeypatch):
    monkeypatch.setattr(management_path.os, "name", "nt")
    monkeypatch.setattr(
        management_path.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
    )

    current = management_path.WindowsManagementPathObserver().observe("192.0.2.10").public
    result = diagnose_management_path(
        target="192.0.2.10",
        current=current,
        last_known_good=None,
        session_status={"state": "offline", "errorCode": "switch_unreachable"},
    )

    assert current.collection_error == "windows_observation_failed"
    assert current.route.kind == "unknown"
    assert result.conclusion == "INDETERMINATE"
    assert result.confidence == "INDETERMINATE"
