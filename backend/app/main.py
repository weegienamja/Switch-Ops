"""FastAPI sidecar for SwitchOps.

Binds 127.0.0.1 only. Allowlist-driven. No raw CLI endpoint exists.
"""
from __future__ import annotations

import argparse
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal, TypeVar

import asyncio
import json
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .audit_store import get_audit_store
from .change_assurance import get_change_assurance_service
from .change_models import ChangePlanRequest, ChangeSession, ChangeSessionList
from .command_registry import assert_interface_readable, short_interface
from .configuration_history import get_configuration_history_store
from .connection_test import run_connection_test
from .intent_store import get_intent_store
from .config import get_settings
from .credential_store import SwitchCredentials, get_credential_store
from .device_session import JobPriority, get_device_session, run_on_device
from .discovery import correlate_local_endpoint, inspect_lldp, inspect_snmp_config
from .discovery_store import get_discovery_store
from .live_state import (
    LiveCollector,
    TierConfig,
    get_collector,
    get_live_state,
    set_collector,
)
from .errors import CommandNotAllowedError, SwitchOpsError
from .health_logic import build_summary
from .host_key_store import is_host_pinned
from .guide import list_guide_operations, run_guide_operation
from .logging_config import configure_logging, redact, register_secret
from .models import (
    AccessPointPlanRequest,
    ApiError,
    AuditResponse,
    BackupResult,
    CpuStatus,
    ConfigurationHistoryEntry,
    ConfigurationHistoryResponse,
    ConnectionTestResult,
    ExpectedRelationshipRequest,
    ExpectedTopologyResponse,
    CredentialSetupRequest,
    DashboardResponse,
    DeploymentPlan,
    DiscoveryStatus,
    EnvironmentStatus,
    GuideCatalogResponse,
    GuideRunRequest,
    GuideRunResult,
    InterfaceErrorsResponse,
    InterfaceDelta,
    InterfaceStatus,
    InterfaceStatusResponse,
    LogsResponse,
    MacTableResponse,
    MemoryStatus,
    MockScenarioRequest,
    MockScenarioStatus,
    NetworkEventsResponse,
    NetworkEvent,
    PoeResponse,
    PoePort,
    OperationRequest,
    OperationResult,
    ReconciliationSummary,
    RuntimeInfo,
    SetupStatus,
    SwitchSummary,
    TelemetryHistoryResponse,
    TelemetrySnapshotSummary,
    TopologyModel,
    ConfigSaveState,
    ControlledWritesUpdate,
    InterfacePolicyEntry,
    InterfacePolicyResponse,
    InterfacePolicyUpdate,
    WriteLockStatus,
)
from .meraki_client import MerakiApiError
from .meraki_models import (
    MerakiConnectionTestResult,
    MerakiCredentialRequest,
    MerakiNetwork,
    MerakiOrganization,
    MerakiRefreshResult,
    MerakiSelection,
    MerakiSetupStatus,
)
from .interface_policy import device_key, get_interface_policy_store
from .lab_collector import LAB_COMMANDS, LabDeviceObservation, collect_lab_device
from .models import (
    ConfiguredLabDevice,
    FailureScenario,
    LabAssuranceState,
    LabCapability,
    LabDeviceCreateRequest,
    LabDeviceList,
    LabEdge,
    LabFinding,
    LabPath,
    LabRefreshResult,
    PerformanceObservation,
    PerformanceProbeRequest,
)
from .lab_service import get_lab_assurance_service
from .operations import (
    OPERATIONS,
    assert_interface_operable,
    config_fingerprints,
    get_save_tracker,
    get_write_lock,
    run_operation,
    save_running_config,
)
from .parsers.arp import parse_arp
from .parsers.cdp import parse_cdp
from .parsers.config_parser import parse_running_config, redact_config
from .parsers.cpu import parse_cpu
from .parsers.environment import parse_environment
from .parsers.errors import parse_interface_errors
from .parsers.interfaces import parse_interface_status
from .parsers.inventory import parse_inventory
from .parsers.logs import parse_logs
from .parsers.mac_table import parse_mac_table
from .parsers.memory import parse_memory
from .parsers.poe import parse_poe
from .parsers.version import parse_version
from .parsers.vlans import parse_vlans
from .planner import build_access_point_plan
from .reconciliation import (
    CiscoIosEvidenceProvider,
    HistoryProvider,
    IntentProvider,
    PreviousInterfaceState,
    reconcile,
    reconciliation_events,
)
from .switch_client import (
    SwitchClient,
    get_mock_scenario,
    set_mock_scenario,
)
from .telemetry_store import get_telemetry_store
from .topology import build_topology, switch_device_id
from .tools.backup import backup_running_config
from .tools.read_only import run_and_audit
from .unified_models import OperatorIdentityDecisionRequest, UnifiedLabState
from .unified_service import get_unified_lab_service


logger = logging.getLogger("switchops")

settings = get_settings()
configure_logging(settings.log_dir)

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Own the device worker and collectors for the process lifetime."""
    get_write_lock().lock()
    get_save_tracker().reset()
    _start_live_operations()
    get_unified_lab_service().start()
    try:
        yield
    finally:
        get_unified_lab_service().stop()
        _stop_live_operations()
        get_write_lock().lock()


app = FastAPI(
    title="SwitchOps",
    version="0.8.0",
    description="Local-only network operations sidecar. Allowlisted commands only.",
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url=None,
    lifespan=_lifespan,
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "testserver", "tauri.localhost"],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _enforce_mutation_origin(request: Request, call_next):
    """CORS does not prevent cross-site form POSTs; reject their Origin."""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin and origin not in settings.cors_origin_list:
            return JSONResponse(
                status_code=403,
                content=ApiError(
                    code="origin_not_allowed",
                    message="Request origin is not allowed.",
                ).model_dump(),
            )
    return await call_next(request)


@app.exception_handler(SwitchOpsError)
async def _switchops_error_handler(request: Request, exc: SwitchOpsError):  # noqa: ARG001
    safe_detail = redact(exc.detail) if exc.detail and exc.http_status < 500 else None
    return JSONResponse(
        status_code=exc.http_status,
        content=ApiError(code=exc.code, message=exc.message, detail=safe_detail).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):  # noqa: ARG001
    # FastAPI's default validation payload can echo rejected input values.
    locations = [".".join(str(part) for part in error.get("loc", ())) for error in exc.errors()]
    detail = ", ".join(location for location in locations if location) or None
    return JSONResponse(
        status_code=422,
        content=ApiError(
            code="invalid_request",
            message="Request validation failed.",
            detail=detail,
        ).model_dump(),
    )


# --- client lifecycle ------------------------------------------------------
#
# Every device access goes through the persistent session worker. No request
# handler touches the SSH client directly, so two handlers cannot interleave
# commands on the channel even if they arrive at the same instant.


def on_device(
    kind: str,
    run: Callable[[SwitchClient], T],
    *,
    priority: JobPriority = JobPriority.DIAGNOSTIC,
    timeout: float = 180.0,
) -> T:
    """Run one unit of work on the switch, serialized behind the worker."""
    return run_on_device(kind, run, priority=priority, timeout=timeout)


T = TypeVar("T")
_UNSUPPORTED_IOS_OUTPUT = re.compile(
    r"%\s*(?:Invalid input|Ambiguous command|Incomplete command|Unrecognized command)",
    re.IGNORECASE,
)


def _current_device_host() -> str | None:
    host = get_credential_store().status().get("switch_host")
    return str(host) if host else None


def _assurance_context() -> dict:
    payload = get_live_state().snapshot()
    payload["connection"] = get_device_session().status()
    return payload


def _current_change_device_id() -> str:
    host = _current_device_host()
    if not host:
        raise CommandNotAllowedError("Configure a device before creating a change plan.")
    # Bind durable plans to the configured connection target without ever
    # persisting the clear-text address. Cached discovery identity is not an
    # authorization scope and may outlive a credential/device switch.
    return f"device-{device_key(host)[:16]}"


def _annotate_interface_policy(interfaces: list) -> None:
    get_interface_policy_store().annotate(_current_device_host(), interfaces)


def _parse_section(
    section: str,
    parser: Callable[[str], T],
    output: str,
    fallback: T,
    errors: dict[str, str],
) -> T:
    try:
        return parser(output)
    except Exception as exc:
        errors[section] = "parser_error"
        logger.warning("Parser failed for %s (%s)", section, type(exc).__name__)
        return fallback


def _collect_dashboard(client: SwitchClient) -> DashboardResponse:
    """Collect the complete dashboard through one sequential SSH session."""
    commands = {
        "version": "show_version",
        "inventory": "show_inventory",
        "config": "show_running_config",
        "interfaces": "show_interfaces_status",
        "environment": "show_env_all",
        "cpu": "show_processes_cpu",
        "poe": "show_power_inline",
        "errors": "show_interfaces_counters_errors",
        "memory": "show_memory_statistics",
        "macTable": "show_mac_address_table",
        "neighbors": "show_cdp_neighbors_detail",
        "lldpSummary": "show_lldp_neighbors",
        "lldpDetail": "show_lldp_neighbors_detail",
        "arp": "show_ip_arp",
        "logs": "show_logging",
    }
    outputs: dict[str, str] = {}
    section_errors: dict[str, str] = {}
    for section, symbol in commands.items():
        try:
            output = run_and_audit(client, symbol=symbol)
            if _UNSUPPORTED_IOS_OUTPUT.search(output) and not section.startswith("lldp"):
                section_errors[section] = "unsupported_by_ios"
                output = ""
            outputs[section] = output
        except Exception as exc:
            section_errors[section] = "command_failed"
            outputs[section] = ""
            logger.warning("Telemetry command failed for %s (%s)", section, type(exc).__name__)

    version = _parse_section("version", parse_version, outputs["version"], {}, section_errors)
    inventory = _parse_section(
        "inventory", parse_inventory, outputs["inventory"], {}, section_errors
    )
    config = _parse_section("config", parse_running_config, outputs["config"], {}, section_errors)
    interfaces = _parse_section(
        "interfaces", parse_interface_status, outputs["interfaces"], [], section_errors
    )
    configured_host = _current_device_host()
    for svi in config.get("svi_addresses", []):
        if isinstance(svi, dict) and configured_host and svi.get("ip") == configured_host:
            config["management_ip"] = svi.get("ip")
            config["management_mask"] = svi.get("mask")
            config["management_interface"] = svi.get("interface")
            get_interface_policy_store().ensure_protected(
                configured_host, str(svi.get("interface") or "")
            )
            break
    _annotate_interface_policy(interfaces)
    environment = _parse_section(
        "environment",
        parse_environment,
        outputs["environment"],
        EnvironmentStatus(),
        section_errors,
    )
    cpu = _parse_section("cpu", parse_cpu, outputs["cpu"], CpuStatus(), section_errors)
    poe = _parse_section(
        "poe",
        parse_poe,
        outputs["poe"],
        PoeResponse(availableWatts=0, usedWatts=0, remainingWatts=0, ports=[]),
        section_errors,
    )
    counters = _parse_section(
        "errors", parse_interface_errors, outputs["errors"], [], section_errors
    )
    error_total = sum(counter.total for counter in counters)
    errors = InterfaceErrorsResponse(
        counters=counters,
        totalErrors=error_total,
        healthy=error_total == 0,
    )
    memory = _parse_section(
        "memory", parse_memory, outputs["memory"], MemoryStatus(), section_errors
    )
    mac_entries = _parse_section(
        "macTable", parse_mac_table, outputs["macTable"], [], section_errors
    )
    logs = _parse_section(
        "logs", parse_logs, outputs["logs"], LogsResponse(entries=[]), section_errors
    )
    # CDP is the only direct-neighbour evidence available on this platform. An
    # empty result is normal and must not be treated as a failure.
    cdp_neighbors = _parse_section(
        "neighbors", parse_cdp, outputs["neighbors"], [], section_errors
    )
    lldp_status = inspect_lldp(
        running_config=outputs["config"],
        summary_output=outputs["lldpSummary"],
        detail_output=outputs["lldpDetail"],
    )
    lldp_neighbors = lldp_status.neighbors
    # ARP ties an IP to a hardware address. Combined with the MAC table it can
    # place the default gateway on a port. An empty or stale table proves
    # nothing and is not an error.
    arp_entries = _parse_section("arp", parse_arp, outputs["arp"], [], section_errors)

    observed_at = datetime.now(timezone.utc)
    credential_status = get_credential_store().status()
    inventory_serial = inventory.get("serial")
    if inventory_serial and inventory_serial.startswith("__"):
        inventory_serial = None
    hostname = str(config.get("hostname") or version.get("hostname") or "Unknown")
    model = str(inventory.get("pid") or version.get("model") or "Unknown Cisco device")
    management_ip = str(
        config.get("management_ip")
        or credential_status.get("switch_host")
        or settings.switch_host
        or "Unknown"
    )
    local_endpoint = correlate_local_endpoint(
        management_ip=management_ip,
        mac_entries=mac_entries,
        arp_entries=arp_entries,
        interfaces=interfaces,
        adapters=[] if settings.mock_mode else None,
    )
    discovery = DiscoveryStatus(
        lldp=lldp_status,
        localEndpoint=local_endpoint,
        snmp=inspect_snmp_config(outputs["config"]),
    )
    intent_store = get_intent_store()
    try:
        stored_expectations = intent_store.list_expected(
            switch_device_id(hostname, "mock" if settings.mock_mode else "physical")
        )
    except Exception as exc:
        section_errors["topologyIntent"] = "persistence_failed"
        logger.warning("Topology intent load failed (%s)", type(exc).__name__)
        stored_expectations = []
    topology = build_topology(
        hostname=hostname,
        model=model,
        management_ip=management_ip,
        interfaces=interfaces,
        mac_entries=mac_entries,
        poe_ports=poe.ports,
        cdp_neighbors=cdp_neighbors,
        lldp_neighbors=lldp_neighbors,
        arp_entries=arp_entries,
        local_endpoint=local_endpoint,
        expected_relationships=stored_expectations,
        observed_at=observed_at,
        source_namespace="mock" if settings.mock_mode else "physical",
    )
    try:
        core_discovery_sections = {
            "interfaces", "macTable", "neighbors", "lldpSummary", "lldpDetail", "arp"
        }
        topology = get_discovery_store().apply_observation(
            topology,
            complete=not bool(core_discovery_sections & section_errors.keys()),
            observed_at=observed_at,
            connection_state=str(get_device_session().status().get("state") or "live"),
        )
    except Exception as exc:
        section_errors["discoveryHistory"] = "persistence_failed"
        logger.warning("Discovery history failed (%s)", type(exc).__name__)
    telemetry_store = get_telemetry_store(
        retention_days=settings.telemetry_retention_days
    )
    try:
        telemetry = telemetry_store.record_snapshot(
            device_id=topology.root_device_id,
            reachable=True,
            cpu=cpu,
            memory=memory,
            environment=environment,
            poe=poe,
            interfaces=interfaces,
            errors=counters,
            mac_entries=mac_entries,
            observed_at=observed_at,
        )
    except Exception as exc:
        section_errors["telemetry"] = "persistence_failed"
        logger.warning("Telemetry persistence failed (%s)", type(exc).__name__)
        errors_by_port = {counter.port: counter.total for counter in counters}
        telemetry = TelemetrySnapshotSummary(
            observedAt=observed_at,
            historyAvailable=False,
            retentionDays=settings.telemetry_retention_days,
            interfaceDeltas=[
                InterfaceDelta(
                    port=interface.port,
                    currentTotalErrors=errors_by_port.get(interface.port, 0),
                    counterState="first",
                    statusAfter=interface.status,
                    adminAfter="down" if interface.status == "disabled" else "up",
                    speedAfter=interface.speed,
                    duplexAfter=interface.duplex,
                    vlanAfter=interface.vlan,
                )
                for interface in interfaces
            ],
        )
    config_history_store = get_configuration_history_store()
    try:
        if outputs["config"].strip():
            _, configuration_changed = config_history_store.observe(
                device_id=topology.root_device_id,
                hostname=hostname,
                config_text=outputs["config"],
                observed_at=observed_at,
            )
            if configuration_changed:
                telemetry_store.record_event(NetworkEvent(
                    timestamp=observed_at,
                    deviceId=topology.root_device_id,
                    eventType="configuration_drift_detected",
                    severity="NOTICE",
                    title="Running configuration changed",
                    detail="The running configuration differs from the preceding observation. Change source is unknown or external to SwitchOps.",
                    metadata={"source": "external_or_unknown"},
                ))
        configuration_history = ConfigurationHistoryResponse(
            entries=config_history_store.recent(
                device_id=topology.root_device_id,
                limit=50,
            )
        )
    except Exception as exc:
        section_errors["configurationHistory"] = "persistence_failed"
        logger.warning("Configuration history failed (%s)", type(exc).__name__)
        configuration_history = ConfigurationHistoryResponse(entries=[])
    # --- topology reconciliation -------------------------------------------
    #
    # Deliberately computed from the same observation, but kept entirely
    # separate from health: a network can be perfectly healthy and still not
    # match what the operator believes is plugged in.
    # The live cache needs the device identity to attribute its events.
    live_state = get_live_state()
    live_state.device_id = topology.root_device_id
    live_state.mark_fresh("deep", observed_at)
    gateway_value = str(config.get("gateway") or "")
    try:
        previous_identities = intent_store.previous_observations(topology.root_device_id)
        previous_states = {}
        for delta in telemetry.interface_deltas:
            label, identified = previous_identities.get(delta.port, (None, False))
            if delta.status_before is None and label is None:
                continue
            previous_states[delta.port] = PreviousInterfaceState(
                connected=(delta.status_before or "").lower() == "connected",
                identity=label if identified else None,
                observed_at=telemetry.previous_observed_at,
            )
        ios_provider = CiscoIosEvidenceProvider(
            interfaces=interfaces,
            mac_entries=mac_entries,
            cdp_neighbors=cdp_neighbors,
            lldp_neighbors=lldp_neighbors,
            local_endpoint=local_endpoint,
            arp_entries=arp_entries,
            default_gateway=gateway_value,
            observed_at=observed_at,
            topology=topology,
        )
        reconciliation = reconcile(
            device_id=topology.root_device_id,
            interfaces=interfaces,
            ios=ios_provider,
            intent=IntentProvider(
                interfaces=interfaces,
                stored=stored_expectations,
            ),
            history=HistoryProvider(previous_states),
            evaluated_at=observed_at,
        )
        for event in reconciliation_events(
            device_id=topology.root_device_id,
            summary=reconciliation,
            store=intent_store,
            observed_at=observed_at,
        ):
            telemetry_store.record_event(event)
    except Exception as exc:
        section_errors["reconciliation"] = "reconciliation_failed"
        logger.warning("Reconciliation failed (%s)", type(exc).__name__)
        reconciliation = ReconciliationSummary(
            evaluatedAt=observed_at,
            deviceId=topology.root_device_id,
            headline="Reconciliation unavailable for this observation.",
        )

    # Deep observations publish one authoritative topology envelope. Fast
    # interface events remain lightweight overlays and never reconstruct
    # identity in the frontend.
    live_state.apply_topology(topology.model_dump(by_alias=True, mode="json"))

    summary = build_summary(
        hostname=hostname,
        model=model,
        management_ip=management_ip,
        gateway=str(config.get("gateway") or "Unknown"),
        ios_version=str(version.get("ios_version") or "Unknown"),
        serial=inventory_serial or version.get("serial"),
        uptime=version.get("uptime"),
        interfaces=interfaces,
        env=environment,
        cpu=cpu,
        memory=memory,
        poe=poe,
        errors=errors,
        deltas=telemetry.interface_deltas,
        evaluated_at=observed_at,
        pid=inventory.get("pid") or version.get("model"),
        hardware_revision=inventory.get("vid") or version.get("hardware_revision"),
        ios_image=version.get("ios_image"),
        bootloader=version.get("bootloader"),
        interface_counts=version.get("interface_counts"),
        telemetry_complete=not section_errors,
    )
    errors.healthy = not any(
        delta.counter_state in {"increased", "wrapped"}
        and (delta.error_delta or 0) > 0
        for delta in telemetry.interface_deltas
    )
    if section_errors:
        unavailable = ", ".join(section_errors)
        summary.summary = f"{summary.summary} Partial telemetry: {unavailable} unavailable."

    live_state.apply_assurance_context(
        health=summary.health.model_dump(by_alias=True, mode="json"),
        reconciliation=reconciliation.model_dump(by_alias=True, mode="json"),
    )

    dashboard = DashboardResponse(
        summary=summary,
        interfaces=InterfaceStatusResponse(interfaces=interfaces),
        poe=poe,
        errors=errors,
        environment=environment,
        cpu=cpu,
        memory=memory,
        macTable=MacTableResponse(entries=mac_entries),
        logs=logs,
        audit=AuditResponse(events=get_audit_store().recent(limit=100)),
        telemetry=telemetry,
        events=NetworkEventsResponse(
            events=telemetry_store.recent_events(
                device_id=topology.root_device_id,
                limit=100,
            )
        ),
        topology=topology,
        reconciliation=reconciliation,
        discovery=discovery,
        configurationHistory=configuration_history,
        sectionErrors=section_errors,
    )
    try:
        get_unified_lab_service().update_catalyst(dashboard)
    except Exception as exc:
        # Unified Lab is an additive read-only view. Its persistence or
        # reconciliation can never make the Catalyst dashboard unavailable.
        logger.warning("Unified Catalyst normalization failed (%s)", type(exc).__name__)
    return dashboard


# --- system endpoints -----------------------------------------------------

# --- live operations lifecycle ---------------------------------------------


def _record_interface_transitions(changes: list[dict], at: datetime) -> None:
    """Persist meaningful link transitions the fast tier revealed.

    Only transitions are written. A 5 s sample rate would add roughly 17,000
    rows a day if every sample were stored, to answer a question nobody asks.
    """
    live = get_live_state()
    device_id = live.device_id
    if not device_id:
        return
    store = get_telemetry_store(retention_days=settings.telemetry_retention_days)
    for change in changes:
        before, after = change["before"], change["after"]
        port = change["port"]
        if before["oper_state"] != after["oper_state"]:
            up = after["oper_state"] == "up"
            event = NetworkEvent(
                timestamp=at,
                deviceId=device_id,
                interface=port,
                eventType="interface_link_up" if up else "interface_link_down",
                severity="HEALTHY" if up else "NOTICE",
                title=f"{port} link {'established' if up else 'lost'}",
                detail=(
                    f"Observed by live telemetry. Negotiated {after['speed']} "
                    f"{after['duplex']}." if up
                    else "The switch no longer detects an Ethernet link on this interface."
                ),
                metadata={"source": "live-fast"},
            )
        elif before["admin_state"] != after["admin_state"]:
            enabled = after["admin_state"] == "up"
            event = NetworkEvent(
                timestamp=at,
                deviceId=device_id,
                interface=port,
                eventType="interface_admin_changed",
                severity="NOTICE",
                title=f"{port} administratively {'enabled' if enabled else 'disabled'}",
                detail="The interface's administrative state changed.",
                metadata={"source": "live-fast"},
            )
        elif before["description"] != after["description"]:
            event = NetworkEvent(
                timestamp=at,
                deviceId=device_id,
                interface=port,
                eventType="interface_description_changed",
                severity="NOTICE",
                title=f"{port} description changed",
                detail=f"The interface description is now {after['description']!r}.",
                metadata={"source": "live-fast"},
            )
        else:
            continue
        try:
            stored = store.record_event(event)
            get_live_state().hub.publish("network_event", stored.model_dump(by_alias=True, mode="json"))
        except Exception:
            logger.warning("Could not persist a live transition for %s", port)


def _record_poe_transitions(changes: list[dict], at: datetime) -> None:
    live = get_live_state()
    device_id = live.device_id
    if not device_id:
        return
    store = get_telemetry_store(retention_days=settings.telemetry_retention_days)
    for change in changes:
        active = str(change["after"]).lower() not in {"", "off", "n/a", "faulty", "deny"}
        event = NetworkEvent(
            timestamp=at,
            deviceId=device_id,
            interface=change["port"],
            eventType="poe_state_changed",
            severity="HEALTHY" if active else "NOTICE",
            title=f"PoE {'started' if active else 'stopped'} on {change['port']}",
            detail=(
                f"Observed PoE operational state changed from {change['before'] or 'unknown'} "
                f"to {change['after'] or 'unknown'}."
            ),
            metadata={"source": "live-medium", "watts": change.get("watts")},
        )
        try:
            stored = store.record_event(event)
            live.hub.publish("network_event", stored.model_dump(by_alias=True, mode="json"))
        except Exception:
            logger.warning("Could not persist a PoE transition")


def _record_slow_discovery(payload: dict, at: datetime) -> None:
    """Rebuild topology from the slow-tier commands already collected.

    No additional IOS command is sent. The cached deep snapshot supplies switch
    metadata; fast state supplies the current physical interfaces.
    """
    live = get_live_state()
    snapshot = live.snapshot()
    cached_payload = snapshot.get("topology")
    if not cached_payload:
        return
    try:
        cached = TopologyModel.model_validate(cached_payload)
        root = next(item for item in cached.devices if item.id == cached.root_device_id)
        interfaces = [
            InterfaceStatus(
                port=item["port"],
                name=item.get("description") or "",
                status=item.get("status") or "",
                vlan=item.get("vlan") or "",
                duplex=item.get("duplex") or "",
                speed=item.get("speed") or "",
                protected=bool(item.get("protected")),
                policyState=item.get("policy_state") or "UNMANAGED",
            )
            for item in snapshot.get("interfaces", [])
        ]
        if not interfaces:
            return
        poe_ports = [
            PoePort(
                interface=item["port"],
                oper=item.get("poe_state") or "not-supported",
                powerWatts=float(item.get("poe_watts") or 0),
            )
            for item in snapshot.get("interfaces", [])
            if item.get("poe_state")
        ]
        management_ip = root.ip or "Unknown"
        mac_entries = payload.get("macEntries") or []
        arp_entries = payload.get("arpEntries") or []
        local_endpoint = correlate_local_endpoint(
            management_ip=management_ip,
            mac_entries=mac_entries,
            arp_entries=arp_entries,
            interfaces=interfaces,
            adapters=[] if settings.mock_mode else None,
        )
        expected = get_intent_store().list_expected(cached.root_device_id)
        updated = build_topology(
            hostname=root.name,
            model=root.model or "Unknown Cisco device",
            management_ip=management_ip,
            interfaces=interfaces,
            mac_entries=mac_entries,
            arp_entries=arp_entries,
            poe_ports=poe_ports,
            cdp_neighbors=payload.get("cdpNeighbors") or [],
            lldp_neighbors=payload.get("lldpNeighbors") or [],
            local_endpoint=local_endpoint,
            expected_relationships=expected,
            observed_at=at,
            source_namespace="mock" if settings.mock_mode else "physical",
        )
        updated = get_discovery_store().apply_observation(
            updated,
            complete=True,
            observed_at=at,
            connection_state=str(get_device_session().status().get("state") or "live"),
        )
        live.apply_topology(updated.model_dump(by_alias=True, mode="json"))
    except Exception as exc:
        logger.debug("Slow-tier topology refresh failed (%s)", type(exc).__name__)


def _record_slow_failure(at: datetime) -> None:
    """Age cached discovery after a failed slow poll without revoking it."""
    live = get_live_state()
    cached_payload = live.snapshot().get("topology")
    if not cached_payload:
        return
    try:
        cached = TopologyModel.model_validate(cached_payload)
        root = next(item for item in cached.devices if item.id == cached.root_device_id)
        incomplete = TopologyModel(
            generatedAt=at,
            rootDeviceId=cached.root_device_id,
            devices=[root],
            interfaces=cached.interfaces,
            links=[],
            evidence=[],
            expectations=cached.expectations,
            evidenceModelVersion=cached.evidence_model_version,
        )
        store = get_discovery_store()
        if store.observation_count(cached.root_device_id) == 0:
            return
        aged = store.apply_observation(
            incomplete,
            complete=False,
            observed_at=at,
            connection_state=str(get_device_session().status().get("state") or "stale"),
        )
        live.apply_topology(aged.model_dump(by_alias=True, mode="json"))
    except Exception as exc:
        logger.debug("Slow-tier topology aging failed (%s)", type(exc).__name__)


def _start_live_operations() -> None:
    session = get_device_session()
    session.start()
    live = get_live_state()
    session.add_listener(lambda status: live.hub.publish("connection_state", status))
    collector = LiveCollector(
        state=live,
        session=session,
        config=TierConfig(),
        on_fast_change=_record_interface_transitions,
        on_poe_change=_record_poe_transitions,
        on_slow_discovery=_record_slow_discovery,
        on_slow_failure=_record_slow_failure,
    )
    set_collector(collector)
    collector.start()


def _stop_live_operations() -> None:
    collector = get_collector()
    if collector is not None:
        collector.stop()
    set_collector(None)
    get_device_session().stop()


@app.get("/api/live/state")
def get_live_snapshot():
    """Current normalised live state, for a client that has just connected."""
    live = get_live_state()
    payload = live.snapshot()
    payload["connection"] = get_device_session().status()
    collector = get_collector()
    payload["tiers"] = {
        "fastSeconds": collector.config.fast_seconds if collector else None,
        "mediumSeconds": collector.config.medium_seconds if collector else None,
        "slowSeconds": collector.config.slow_seconds if collector else None,
        "paused": collector.paused if collector else False,
        "ticksRun": collector.ticks_run if collector else 0,
        "ticksSkipped": collector.ticks_skipped if collector else 0,
    }
    return payload


@app.get("/api/live/stream")
async def live_stream(request: Request):
    """Server-Sent Events channel.

    SSE rather than WebSocket: the traffic is one-directional, EventSource
    reconnects on its own, and it needs no dependency beyond what is already
    here.
    """
    live = get_live_state()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    live.hub.subscribe(loop, queue)

    async def events():
        try:
            # The first message is always a complete snapshot, so a client
            # never has to assemble state from a partial stream.
            opening = live.snapshot()
            opening["connection"] = get_device_session().status()
            yield _sse("snapshot", opening)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Keeps intermediaries and EventSource from timing out, and
                    # tells the UI the channel is still alive.
                    yield ": keepalive\n\n"
                    continue
                yield _sse(message["type"], message["data"], at=message["at"])
        finally:
            live.hub.unsubscribe(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event_type: str, data: Any, at: str | None = None) -> str:
    payload = {"at": at or datetime.now(timezone.utc).isoformat(), "data": data}
    return f"event: {event_type}\ndata: {json.dumps(payload, default=str)}\n\n"


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "switchops-backend",
        "mockMode": settings.mock_mode,
        "enableWriteActions": get_interface_policy_store().controlled_writes_enabled(),
    }


@app.get("/api/system/info", response_model=RuntimeInfo, response_model_by_alias=True)
def system_info():
    """Non-secret runtime facts for the Settings screen."""
    credential_status = get_credential_store().status()
    host = credential_status.get("switch_host") or settings.switch_host
    return RuntimeInfo(
        version=app.version,
        apiHost=settings.host,
        apiPort=settings.port,
        mockMode=settings.mock_mode,
        enableWriteActions=get_interface_policy_store().controlled_writes_enabled(),
        legacySsh=settings.legacy_ssh,
        apiDocsEnabled=settings.enable_api_docs,
        hostKeyPinned=bool(host) and is_host_pinned(str(host)),
        telemetryRetentionDays=settings.telemetry_retention_days,
        dataDir=str(settings.data_dir),
        backupDir=str(settings.backup_dir),
        logDir=str(settings.log_dir),
        corsOrigins=settings.cors_origin_list,
        deviceDriver=credential_status.get("switch_device_type") or settings.switch_device_type,
    )


@app.get("/api/setup/status", response_model=SetupStatus, response_model_by_alias=True)
def setup_status():
    store = get_credential_store()
    raw = store.status()
    return SetupStatus(
        configured=raw["configured"] or settings.mock_mode,
        hasPassword=raw["has_password"],
        hasEnableSecret=raw["has_enable_secret"],
        storage=raw["storage"],
        mockMode=settings.mock_mode,
        enableWriteActions=get_interface_policy_store().controlled_writes_enabled(),
        switchHost=raw["switch_host"],
        switchUsername=raw["switch_username"],
        switchDeviceType=raw["switch_device_type"],
    )


@app.get(
    "/api/mock/scenario",
    response_model=MockScenarioStatus,
    response_model_by_alias=True,
)
def get_mock_scenario_status():
    if not settings.mock_mode:
        raise HTTPException(status_code=403, detail="Mock scenarios are available only in mock mode.")
    return MockScenarioStatus(scenario=get_mock_scenario(), mockMode=True)


@app.post(
    "/api/mock/scenario",
    response_model=MockScenarioStatus,
    response_model_by_alias=True,
)
def post_mock_scenario(req: MockScenarioRequest):
    if not settings.mock_mode:
        raise HTTPException(status_code=403, detail="Mock scenarios are available only in mock mode.")
    return MockScenarioStatus(scenario=set_mock_scenario(req.scenario), mockMode=True)


@app.post("/api/setup/credentials", response_model=SetupStatus, response_model_by_alias=True)
def setup_credentials(req: CredentialSetupRequest):
    store = get_credential_store()
    register_secret(req.switch_password)
    register_secret(req.switch_enable_secret)
    store.save(
        SwitchCredentials(
            switch_host=req.switch_host,
            switch_username=req.switch_username,
            switch_password=req.switch_password,
            switch_enable_secret=req.switch_enable_secret or req.switch_password,
            switch_device_type=req.switch_device_type,
        )
    )
    return setup_status()


@app.delete("/api/setup/credentials", response_model=SetupStatus, response_model_by_alias=True)
def clear_credentials():
    get_credential_store().clear()
    return setup_status()


# --- optional read-only Meraki evidence source ----------------------------


@app.get(
    "/api/meraki/setup/status",
    response_model=MerakiSetupStatus,
    response_model_by_alias=True,
)
def meraki_setup_status():
    return get_unified_lab_service().setup_status()


@app.post(
    "/api/meraki/setup/credentials",
    response_model=MerakiSetupStatus,
    response_model_by_alias=True,
)
def meraki_setup_credentials(req: MerakiCredentialRequest):
    register_secret(req.api_key)
    try:
        return get_unified_lab_service().save_api_key(req.api_key)
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail="Windows Credential Manager is unavailable; the API key was not stored.",
        ) from None


@app.delete(
    "/api/meraki/setup/credentials",
    response_model=MerakiSetupStatus,
    response_model_by_alias=True,
)
def clear_meraki_credentials():
    return get_unified_lab_service().clear_api_key()


@app.post(
    "/api/meraki/setup/test",
    response_model=MerakiConnectionTestResult,
    response_model_by_alias=True,
)
def test_meraki_connection():
    return get_unified_lab_service().test_connection()


def _meraki_read(action: Callable[[], T]) -> T:
    try:
        return action()
    except RuntimeError:
        raise HTTPException(status_code=412, detail="Meraki is not configured.") from None
    except MerakiApiError as exc:
        status = 429 if exc.code == "rate-limited" else 502
        raise HTTPException(
            status_code=status,
            detail="The allowlisted Meraki read operation failed.",
        ) from None


@app.get(
    "/api/meraki/organizations",
    response_model=list[MerakiOrganization],
    response_model_by_alias=True,
)
def get_meraki_organizations():
    return _meraki_read(lambda: get_unified_lab_service().organizations())


@app.get(
    "/api/meraki/networks",
    response_model=list[MerakiNetwork],
    response_model_by_alias=True,
)
def get_meraki_networks(
    organizationId: str = Query(min_length=1, max_length=128),
):
    return _meraki_read(
        lambda: get_unified_lab_service().networks(organizationId)
    )


@app.put(
    "/api/meraki/selection",
    response_model=MerakiSetupStatus,
    response_model_by_alias=True,
)
def put_meraki_selection(selection: MerakiSelection):
    return get_unified_lab_service().save_selection(selection)


@app.post(
    "/api/meraki/refresh",
    response_model=MerakiRefreshResult,
    response_model_by_alias=True,
)
def refresh_meraki_evidence():
    health = get_unified_lab_service().refresh_meraki()
    accepted = health.state not in {"not-configured", "unavailable"}
    return MerakiRefreshResult(
        accepted=accepted,
        summary=(
            "Meraki evidence refresh completed."
            if accepted
            else "Meraki evidence refresh did not complete; Catalyst remains available."
        ),
        sourceHealth=health,
    )


@app.get(
    "/api/unified-lab/state",
    response_model=UnifiedLabState,
    response_model_by_alias=True,
)
def get_unified_lab_state():
    return get_unified_lab_service().state()


# --- v0.8 Lab Assurance --------------------------------------------------


@app.get(
    "/api/lab-assurance/devices",
    response_model=LabDeviceList,
    response_model_by_alias=True,
)
def get_lab_assurance_devices():
    return get_lab_assurance_service().configured_devices()


@app.post(
    "/api/lab-assurance/devices",
    response_model=ConfiguredLabDevice,
    response_model_by_alias=True,
)
def post_lab_assurance_device(req: LabDeviceCreateRequest):
    try:
        return get_lab_assurance_service().add_device(req)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/api/lab-assurance/devices/{device_id}")
def delete_lab_assurance_device(device_id: str):
    try:
        removed = get_lab_assurance_service().remove_device(device_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Configured Lab Assurance device not found.")
    return {"removed": True}


@app.get(
    "/api/lab-assurance/state",
    response_model=LabAssuranceState,
    response_model_by_alias=True,
)
def get_lab_assurance_state():
    return get_lab_assurance_service().state()


@app.post(
    "/api/lab-assurance/refresh",
    response_model=LabRefreshResult,
    response_model_by_alias=True,
)
def post_lab_assurance_refresh():
    host = _current_device_host()
    primary_id = f"primary-{device_key(host)[:16]}" if host else "primary-mock"
    try:
        primary = on_device(
            "lab-assurance-primary",
            lambda client: collect_lab_device(
                client,
                device_id=primary_id,
                label="Primary Catalyst",
                primary=True,
            ),
            priority=JobPriority.DIAGNOSTIC,
            timeout=360.0,
        )
    except Exception:
        primary = LabDeviceObservation(
            device_id=primary_id,
            configured_label="Primary Catalyst",
            primary=True,
            observed_at=datetime.now(timezone.utc),
            outputs={symbol: "" for symbol in LAB_COMMANDS},
            command_state={symbol: "failed" for symbol in LAB_COMMANDS},
        )
    return LabRefreshResult(
        accepted=True,
        state=get_lab_assurance_service().refresh(primary),
    )


@app.get(
    "/api/lab-assurance/capabilities",
    response_model=list[LabCapability],
    response_model_by_alias=True,
)
def get_lab_assurance_capabilities():
    return get_lab_assurance_service().state().capabilities


@app.get(
    "/api/lab-assurance/findings",
    response_model=list[LabFinding],
    response_model_by_alias=True,
)
def get_lab_assurance_findings():
    return get_lab_assurance_service().state().findings


@app.get(
    "/api/lab-assurance/failures",
    response_model=list[FailureScenario],
    response_model_by_alias=True,
)
def get_lab_assurance_failures():
    return get_lab_assurance_service().state().failures


@app.get(
    "/api/lab-assurance/paths",
    response_model=list[LabPath],
    response_model_by_alias=True,
)
def get_lab_assurance_paths(
    fromNodeId: str | None = Query(default=None, max_length=200),
    toNodeId: str | None = Query(default=None, max_length=200),
):
    paths = get_lab_assurance_service().state().paths
    if fromNodeId:
        paths = [item for item in paths if item.from_node_id == fromNodeId]
    if toNodeId:
        paths = [item for item in paths if item.to_node_id == toNodeId]
    return paths


@app.get(
    "/api/lab-assurance/graph",
    response_model=list[LabEdge],
    response_model_by_alias=True,
)
def get_lab_assurance_graph():
    return get_lab_assurance_service().state().edges


@app.get(
    "/api/lab-assurance/performance",
    response_model=list[PerformanceObservation],
    response_model_by_alias=True,
)
def get_lab_assurance_performance():
    return get_lab_assurance_service().state().performance


@app.post(
    "/api/lab-assurance/performance/probe",
    response_model=PerformanceObservation,
    response_model_by_alias=True,
)
def post_lab_assurance_probe(req: PerformanceProbeRequest):
    try:
        return get_lab_assurance_service().probe(req.target, req.label, req.count)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/api/unified-lab/identity-decision",
    response_model=UnifiedLabState,
    response_model_by_alias=True,
)
def decide_unified_identity(req: OperatorIdentityDecisionRequest):
    try:
        return get_unified_lab_service().decide_identity(req.link_id, req.decision)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


# --- read-only switch endpoints -------------------------------------------

@app.post(
    "/api/setup/test-connection",
    response_model=ConnectionTestResult,
    response_model_by_alias=True,
)
def post_test_connection():
    """Bounded read-only reachability test. Sends only allowlisted show commands."""
    if settings.mock_mode:
        return run_connection_test()
    try:
        # Runs on the operational session, so the result describes the session
        # SwitchOps actually uses rather than a throwaway one.
        return on_device(
            "connection_test",
            lambda client: run_connection_test(client=client),
            priority=JobPriority.DIAGNOSTIC,
            timeout=90.0,
        )
    except SwitchOpsError as exc:
        return run_connection_test(session_error=exc)


@app.get("/api/switch/interfaces", response_model=InterfaceStatusResponse, response_model_by_alias=True)
def get_interfaces():
    out = on_device(
        "interfaces",
        lambda c: run_and_audit(c, symbol="show_interfaces_status"),
    )
    interfaces = parse_interface_status(out)
    _annotate_interface_policy(interfaces)
    return InterfaceStatusResponse(interfaces=interfaces)


@app.get("/api/switch/errors", response_model=InterfaceErrorsResponse, response_model_by_alias=True)
def get_errors():
    out = on_device(
        "errors",
        lambda c: run_and_audit(c, symbol="show_interfaces_counters_errors"),
    )
    counters = parse_interface_errors(out)
    total = sum(c.total for c in counters)
    return InterfaceErrorsResponse(counters=counters, totalErrors=total, healthy=(total == 0))


@app.get("/api/switch/poe", response_model=PoeResponse, response_model_by_alias=True)
def get_poe():
    out = on_device(
        "poe",
        lambda c: run_and_audit(c, symbol="show_power_inline"),
    )
    return parse_poe(out)


@app.get("/api/switch/environment", response_model=EnvironmentStatus, response_model_by_alias=True)
def get_environment():
    out = on_device(
        "environment",
        lambda c: run_and_audit(c, symbol="show_env_all"),
    )
    return parse_environment(out)


@app.get("/api/switch/cpu", response_model=CpuStatus, response_model_by_alias=True)
def get_cpu():
    out = on_device(
        "cpu",
        lambda c: run_and_audit(c, symbol="show_processes_cpu"),
    )
    return parse_cpu(out)


@app.get("/api/switch/memory", response_model=MemoryStatus, response_model_by_alias=True)
def get_memory():
    out = on_device(
        "memory",
        lambda c: run_and_audit(c, symbol="show_memory_statistics"),
    )
    return parse_memory(out)


@app.get("/api/switch/mac-table", response_model=MacTableResponse, response_model_by_alias=True)
def get_mac_table():
    out = on_device(
        "mac_table",
        lambda c: run_and_audit(c, symbol="show_mac_address_table"),
    )
    return MacTableResponse(entries=parse_mac_table(out))


@app.get("/api/switch/logs", response_model=LogsResponse, response_model_by_alias=True)
def get_logs():
    out = on_device(
        "logs",
        lambda c: run_and_audit(c, symbol="show_logging"),
    )
    return parse_logs(out)


@app.get(
    "/api/switch/dashboard",
    response_model=DashboardResponse,
    response_model_by_alias=True,
)
def get_dashboard():
    return on_device("dashboard", lambda client: _collect_dashboard(client))


@app.get("/api/switch/summary", response_model=SwitchSummary, response_model_by_alias=True)
def get_summary():
    return on_device("summary", lambda client: _collect_dashboard(client).summary)


@app.get("/api/switch/audit", response_model=AuditResponse, response_model_by_alias=True)
def get_audit(limit: int = Query(default=100, ge=1, le=500)):
    events = get_audit_store().recent(limit=limit)
    return AuditResponse(events=events)


@app.get(
    "/api/network/events",
    response_model=NetworkEventsResponse,
    response_model_by_alias=True,
)
def get_network_events(
    limit: int = Query(default=100, ge=1, le=500),
    device_id: str | None = Query(default=None, alias="deviceId", max_length=128),
    interface: str | None = Query(default=None, max_length=64),
    severity: Literal["HEALTHY", "NOTICE", "ATTENTION", "CRITICAL"] | None = None,
    event_type: str | None = Query(default=None, alias="eventType", pattern=r"^[a-z0-9_]{1,64}$"),
):
    events = get_telemetry_store(
        retention_days=settings.telemetry_retention_days
    ).recent_events(
        limit=limit,
        device_id=device_id,
        interface=interface,
        severity=severity,
        event_type=event_type,
    )
    return NetworkEventsResponse(events=events)


@app.get(
    "/api/telemetry/history",
    response_model=TelemetryHistoryResponse,
    response_model_by_alias=True,
)
def get_telemetry_history(
    device_id: str = Query(alias="deviceId", min_length=1, max_length=128),
    hours: int = Query(default=24, ge=1, le=24 * 90),
    limit: int = Query(default=500, ge=1, le=2000),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return get_telemetry_store(
        retention_days=settings.telemetry_retention_days
    ).history(device_id=device_id, since=since, limit=limit)


@app.get(
    "/api/guide/operations",
    response_model=GuideCatalogResponse,
    response_model_by_alias=True,
)
def get_guide_operations():
    return GuideCatalogResponse(operations=list_guide_operations())


@app.get(
    "/api/configuration/history",
    response_model=ConfigurationHistoryResponse,
    response_model_by_alias=True,
)
def get_configuration_history(
    device_id: str | None = Query(default=None, alias="deviceId", max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
):
    entries = get_configuration_history_store().recent(
        device_id=device_id,
        limit=limit,
    )
    return ConfigurationHistoryResponse(entries=entries)


@app.post(
    "/api/configuration/history/{entry_id}/known-good",
    response_model=ConfigurationHistoryEntry,
    response_model_by_alias=True,
)
def post_configuration_known_good(entry_id: int):
    try:
        return get_configuration_history_store().mark_known_good(entry_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Configuration version was not found.")


@app.post(
    "/api/guide/operations/{operation_id}/run",
    response_model=GuideRunResult,
    response_model_by_alias=True,
)
def post_guide_operation(operation_id: str, req: GuideRunRequest):
    return on_device(
        "guide_operation",
        lambda client: run_guide_operation(
            client,
            operation_id=operation_id,
            interface=req.interface,
        ),
    )


@app.post(
    "/api/plans/access-point",
    response_model=DeploymentPlan,
    response_model_by_alias=True,
)
def post_access_point_plan(req: AccessPointPlanRequest):
    """Build a validated proposal from current read-only observations.

    This route has no execution path: it performs three allowlisted show
    commands and returns a plan whose ``applyAvailable`` value is always false.
    """
    def _collect(client: SwitchClient):
        return (
            parse_interface_status(run_and_audit(client, symbol="show_interfaces_status")),
            parse_poe(run_and_audit(client, symbol="show_power_inline")),
            parse_vlans(run_and_audit(client, symbol="show_vlan_brief")),
        )

    interfaces, poe, vlans = on_device("access_point_plan", _collect)
    _annotate_interface_policy(interfaces)
    return build_access_point_plan(
        req,
        interfaces=interfaces,
        poe_ports=poe.ports,
        vlan_ids={item["id"] for item in vlans},
    )


@app.get(
    "/api/topology/intent",
    response_model=ExpectedTopologyResponse,
    response_model_by_alias=True,
)
def get_topology_intent(deviceId: str = Query(min_length=1, max_length=200)):
    """Expected topology recorded in SwitchOps. Local metadata only."""
    return ExpectedTopologyResponse(
        deviceId=deviceId,
        relationships=get_intent_store().list_expected(deviceId),
    )


@app.put(
    "/api/topology/intent/{port}",
    response_model=ExpectedTopologyResponse,
    response_model_by_alias=True,
)
def put_topology_intent(
    port: str,
    req: ExpectedRelationshipRequest,
    deviceId: str = Query(min_length=1, max_length=200),
):
    """Record what should be on an interface.

    This writes SwitchOps' own metadata. It sends nothing to the switch, and
    the interface description on the device is left untouched on purpose - the
    resulting disagreement is reported as stale documentation.
    """
    interface = _short_interface(assert_interface_readable(_decode_port(port)))
    observed = {
        str(item.get("port", ""))
        for item in get_live_state().snapshot().get("interfaces", [])
    }
    if observed and interface not in observed:
        raise CommandNotAllowedError(
            f"Interface {interface} has not been observed on the configured device."
        )
    store = get_intent_store()
    store.set_expected(
        device_id=deviceId,
        interface=interface,
        expected_name=req.expected_name,
        expected_device_type=req.expected_device_type,
        expected_vendor=req.expected_vendor,
        expected_model=req.expected_model,
        source="user-intent",
        note=req.note,
        suppressed=req.suppressed,
    )
    return ExpectedTopologyResponse(
        deviceId=deviceId,
        relationships=store.list_expected(deviceId),
    )


@app.delete(
    "/api/topology/intent/{port}",
    response_model=ExpectedTopologyResponse,
    response_model_by_alias=True,
)
def delete_topology_intent(port: str, deviceId: str = Query(min_length=1, max_length=200)):
    """Forget a recorded expectation; intent falls back to the description."""
    interface = _short_interface(assert_interface_readable(_decode_port(port)))
    store = get_intent_store()
    store.clear_expected(device_id=deviceId, interface=interface)
    return ExpectedTopologyResponse(
        deviceId=deviceId,
        relationships=store.list_expected(deviceId),
    )


@app.post("/api/switch/backup-config", response_model=BackupResult, response_model_by_alias=True)
def post_backup_config():
    return on_device("backup_config", lambda c: backup_running_config(c))


def _short_interface(canonical: str) -> str:
    """Return the IOS short form used by observation keys."""
    return short_interface(canonical)


def _decode_port(port: str) -> str:
    """Path segments can't contain '/'; clients send 'Gi0-6' which we restore."""
    return port.replace("-", "/")


# --- controlled operations ------------------------------------------------


def _interface_policy_response() -> InterfacePolicyResponse:
    observed = [item.get("port", "") for item in get_live_state().snapshot()["interfaces"]]
    status = get_interface_policy_store().status(_current_device_host(), observed)
    return InterfacePolicyResponse(
        deviceConfigured=status["deviceConfigured"],
        deviceKey=status["deviceKey"],
        valid=status["valid"],
        loadError=status["loadError"],
        controlledWritesEnabled=status["controlledWritesEnabled"],
        interfaces=[
            InterfacePolicyEntry(interface=interface, state=state)
            for interface, state in sorted(status["interfaces"].items())
        ],
    )


@app.get(
    "/api/interface-policy",
    response_model=InterfacePolicyResponse,
    response_model_by_alias=True,
)
def get_interface_policy():
    return _interface_policy_response()


@app.put(
    "/api/interface-policy/control",
    response_model=InterfacePolicyResponse,
    response_model_by_alias=True,
)
def put_controlled_writes(req: ControlledWritesUpdate):
    get_interface_policy_store().set_controlled_writes(req.enabled)
    if not req.enabled:
        get_write_lock().lock()
    return _interface_policy_response()


@app.put(
    "/api/interface-policy/interfaces/{port}",
    response_model=InterfacePolicyResponse,
    response_model_by_alias=True,
)
def put_interface_policy(port: str, req: InterfacePolicyUpdate):
    host = _current_device_host()
    if not host:
        raise HTTPException(status_code=409, detail="No device is configured.")
    canonical = assert_interface_readable(_decode_port(port))
    interface = _short_interface(canonical)
    if req.state == "OPERABLE":
        observed = {
            item.get("port", "").strip().lower()
            for item in get_live_state().snapshot()["interfaces"]
            if item.get("port")
        }
        if interface.lower() not in observed:
            raise HTTPException(
                status_code=409,
                detail="Only an interface currently reported by the device can be made operable.",
            )
    get_interface_policy_store().set_state(host, canonical, req.state)
    return _interface_policy_response()


@app.get("/api/control/lock", response_model=WriteLockStatus, response_model_by_alias=True)
def get_control_lock():
    return get_write_lock().status()


@app.post("/api/control/unlock", response_model=WriteLockStatus, response_model_by_alias=True)
def post_control_unlock():
    lock = get_write_lock()
    lock.unlock()
    status = lock.status()
    get_live_state().hub.publish("control_lock", status)
    return status


@app.post("/api/control/lock", response_model=WriteLockStatus, response_model_by_alias=True)
def post_control_lock():
    lock = get_write_lock()
    lock.lock()
    status = lock.status()
    get_live_state().hub.publish("control_lock", status)
    return status


@app.get("/api/operations/catalog")
def get_operations_catalog():
    policy = _interface_policy_response()
    return {
        "operations": [
            {
                "kind": spec.kind,
                "label": spec.label,
                "needsValue": spec.needs_value,
                "reversible": spec.reversible,
            }
            for spec in OPERATIONS.values()
        ],
        "writableInterfaces": [
            entry.interface for entry in policy.interfaces if entry.state == "OPERABLE"
        ],
        "protectedInterfaces": [
            entry.interface for entry in policy.interfaces if entry.state == "PROTECTED"
        ],
        "unmanagedInterfaces": [
            entry.interface for entry in policy.interfaces if entry.state == "UNMANAGED"
        ],
        "arbitraryCli": False,
        "automaticSave": False,
    }


# --- Change Assurance ----------------------------------------------------


def _publish_change_session(session: ChangeSession) -> None:
    live = get_live_state()
    payload = session.model_dump(by_alias=True, mode="json")
    live.hub.publish("change_session", payload)
    step = session.plan.steps[0]
    if session.status in {"executing", "verifying", "rolling_back"} or session.operation_result:
        live.hub.publish(
            "operation_progress",
            {
                "kind": step.kind,
                "interface": session.plan.target_interface,
                "stages": [stage.model_dump(mode="json") for stage in session.operation_stages],
                "status": "running"
                if session.status in {"executing", "verifying", "rolling_back"}
                else session.operation_result.status,
            },
        )


@app.post(
    "/api/change-sessions",
    response_model=ChangeSession,
    response_model_by_alias=True,
)
def post_change_session(req: ChangePlanRequest):
    session = get_change_assurance_service().create_plan(
        req,
        device_id=_current_change_device_id(),
    )
    get_live_state().hub.publish(
        "change_session", session.model_dump(by_alias=True, mode="json")
    )
    return session


@app.get(
    "/api/change-sessions",
    response_model=ChangeSessionList,
    response_model_by_alias=True,
)
def get_change_sessions(limit: int = Query(default=50, ge=1, le=200)):
    return ChangeSessionList(
        sessions=get_change_assurance_service().store.recent(limit=limit)
    )


@app.get(
    "/api/change-sessions/{session_id}",
    response_model=ChangeSession,
    response_model_by_alias=True,
)
def get_change_session(session_id: str):
    session = get_change_assurance_service().store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Change session not found.")
    return session


@app.post(
    "/api/change-sessions/{session_id}/preflight",
    response_model=ChangeSession,
    response_model_by_alias=True,
)
def post_change_preflight(session_id: str):
    service = get_change_assurance_service()
    try:
        return on_device(
            "change-preflight",
            lambda client: service.run_preflight(
                session_id,
                client,
                context=_assurance_context(),
                listener=_publish_change_session,
            ),
            priority=JobPriority.DIAGNOSTIC,
            timeout=240.0,
        )
    except SwitchOpsError:
        session = service.block_preflight_unavailable(session_id)
        _publish_change_session(session)
        return session


@app.post(
    "/api/change-sessions/{session_id}/execute",
    response_model=ChangeSession,
    response_model_by_alias=True,
)
def post_change_execute(session_id: str):
    # An unlock is deliberately unnecessary for planning/preflight and is
    # checked only at the irreversible operator choice to execute.
    get_write_lock().require_unlocked()
    service = get_change_assurance_service()
    existing = service.store.get(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Change session not found.")
    step = existing.plan.steps[0]
    live = get_live_state()
    collector = get_collector()
    live.operation_in_progress = f"{step.kind}:{existing.plan.target_interface}"
    if collector is not None:
        collector.pause()
    try:
        try:
            session = on_device(
                f"change-{step.kind}",
                lambda client: service.execute(
                    session_id,
                    client,
                    context_provider=_assurance_context,
                    listener=_publish_change_session,
                ),
                priority=JobPriority.TRANSACTION,
                timeout=300.0,
            )
        except SwitchOpsError:
            session = service.block_before_execution(
                session_id,
                "Execution was blocked before IOS configuration because the control session or a mandatory write gate was unavailable.",
            )
            _publish_change_session(session)
        result = session.operation_result
        if result is not None and result.requires_save:
            get_save_tracker().record_change(result.at)
        if result is not None:
            live.hub.publish(
                "operation_complete", result.model_dump(by_alias=True, mode="json")
            )
        live.hub.publish(
            "config_state",
            get_save_tracker().state().model_dump(by_alias=True, mode="json"),
        )
        return session
    finally:
        live.operation_in_progress = None
        if collector is not None:
            collector.resume()
            collector.collect_fast()


@app.post(
    "/api/interfaces/{port}/operations",
    response_model=OperationResult,
    response_model_by_alias=True,
)
def post_interface_operation(port: str, req: OperationRequest):
    lock = get_write_lock()
    lock.require_unlocked()
    # Reject an unsafe path before a connection or queue job is attempted.
    canonical = assert_interface_operable(_decode_port(port))
    short = _short_interface(canonical)
    live = get_live_state()
    collector = get_collector()
    marker = f"{req.kind}:{short}"
    live.operation_in_progress = marker
    if collector is not None:
        collector.pause()
    live.hub.publish(
        "operation_progress",
        {"kind": req.kind, "interface": short, "stages": [], "status": "running"},
    )

    def progress(stages):
        live.hub.publish(
            "operation_progress",
            {
                "kind": req.kind,
                "interface": short,
                "stages": [stage.model_dump(mode="json") for stage in stages],
                "status": "running",
            },
        )

    try:
        result = on_device(
            f"operation-{req.kind}",
            lambda c: run_operation(
                c,
                kind=req.kind,
                interface=short,
                value=req.value,
                actor="operator",
                on_progress=progress,
            ),
            priority=JobPriority.TRANSACTION,
        )
        if result.requires_save:
            get_save_tracker().record_change(result.at)
        live.hub.publish(
            "operation_complete", result.model_dump(by_alias=True, mode="json")
        )
        live.hub.publish(
            "config_state",
            get_save_tracker().state().model_dump(by_alias=True, mode="json"),
        )
        return result
    finally:
        live.operation_in_progress = None
        if collector is not None:
            collector.resume()
            # The transaction has left the queue; enqueue one fresh status read
            # instead of waiting for the next periodic tick.
            collector.collect_fast()


@app.get("/api/config/state", response_model=ConfigSaveState, response_model_by_alias=True)
def get_config_state():
    return get_save_tracker().state()


@app.post(
    "/api/config/state/refresh",
    response_model=ConfigSaveState,
    response_model_by_alias=True,
)
def post_config_state_refresh():
    running, startup = on_device(
        "config-fingerprints",
        lambda c: config_fingerprints(c, actor="operator"),
        priority=JobPriority.DIAGNOSTIC,
    )
    tracker = get_save_tracker()
    tracker.record_fingerprints(running, startup)
    state = tracker.state()
    get_live_state().hub.publish(
        "config_state", state.model_dump(by_alias=True, mode="json")
    )
    return state


@app.post("/api/config/save")
def post_config_save():
    # Saving is a distinct, explicit write and therefore uses both gates too.
    get_write_lock().require_unlocked()
    success, detail = on_device(
        "save-config",
        lambda c: save_running_config(c, actor="operator"),
        priority=JobPriority.TRANSACTION,
    )
    tracker = get_save_tracker()
    if success:
        tracker.record_save()
    state = tracker.state()
    get_live_state().hub.publish(
        "config_state", state.model_dump(by_alias=True, mode="json")
    )
    return {
        "success": success,
        "detail": detail,
        "state": state.model_dump(by_alias=True, mode="json"),
    }


# --- entry point ----------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="SwitchOps backend")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    args = parser.parse_args()
    # Enforce loopback bind.
    host = args.host
    if host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning("Forcing bind to 127.0.0.1 (was %s)", host)
        host = "127.0.0.1"
    logger.info(
        "Starting SwitchOps backend on %s:%s (mock=%s, writes=%s)",
        host,
        args.port,
        settings.mock_mode,
        get_interface_policy_store().controlled_writes_enabled(),
    )
    uvicorn.run(app, host=host, port=args.port, log_config=None)


if __name__ == "__main__":
    main()
