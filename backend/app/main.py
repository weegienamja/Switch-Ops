"""FastAPI sidecar for SwitchOps.

Binds 127.0.0.1 only. Allowlist-driven. No raw CLI endpoint exists.
"""
from __future__ import annotations

import argparse
import logging
import re
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from .audit_store import get_audit_store
from .command_registry import build_write_action
from .config import get_settings
from .credential_store import SwitchCredentials, get_credential_store
from .errors import SwitchOpsError
from .health_logic import build_summary
from .logging_config import configure_logging, redact, register_secret
from .models import (
    ApiError,
    AuditResponse,
    BackupResult,
    CpuStatus,
    CredentialSetupRequest,
    DashboardResponse,
    EnvironmentStatus,
    InterfaceErrorsResponse,
    InterfaceStatusResponse,
    LogsResponse,
    MacTableResponse,
    MemoryStatus,
    PoeResponse,
    PortDescriptionRequest,
    SetupStatus,
    SwitchSummary,
    WriteActionResult,
)
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
from .switch_client import SwitchClient, get_switch_client
from .tools.backup import backup_running_config
from .tools.read_only import run_and_audit
from .tools.safe_write import execute_safe_write


logger = logging.getLogger("switchops")

settings = get_settings()
configure_logging(settings.log_dir)

app = FastAPI(
    title="SwitchOps",
    version="0.1.0",
    description="Local-only network operations sidecar. Allowlisted commands only.",
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url=None,
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "testserver", "tauri.localhost"],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
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

@contextmanager
def switch_session() -> Iterator[SwitchClient]:
    client = get_switch_client()
    try:
        yield client
    finally:
        try:
            client.close()
        except Exception:  # pragma: no cover
            pass


T = TypeVar("T")
_UNSUPPORTED_IOS_OUTPUT = re.compile(
    r"%\s*(?:Invalid input|Ambiguous command|Incomplete command|Unrecognized command)",
    re.IGNORECASE,
)


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
        "logs": "show_logging",
    }
    outputs: dict[str, str] = {}
    section_errors: dict[str, str] = {}
    for section, symbol in commands.items():
        try:
            output = run_and_audit(client, symbol=symbol)
            if _UNSUPPORTED_IOS_OUTPUT.search(output):
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

    credential_status = get_credential_store().status()
    inventory_serial = inventory.get("serial")
    if inventory_serial and inventory_serial.startswith("__"):
        inventory_serial = None
    summary = build_summary(
        hostname=str(config.get("hostname") or version.get("hostname") or "Unknown"),
        model=str(inventory.get("pid") or version.get("model") or "Unknown Cisco device"),
        management_ip=str(
            config.get("management_ip")
            or credential_status.get("switch_host")
            or settings.switch_host
            or "Unknown"
        ),
        gateway=str(config.get("gateway") or "Unknown"),
        ios_version=str(version.get("ios_version") or "Unknown"),
        serial=inventory_serial or version.get("serial"),
        uptime=version.get("uptime"),
        interfaces=interfaces,
        env=environment,
        cpu=cpu,
        poe=poe,
        errors=errors,
        pid=inventory.get("pid") or version.get("model"),
        hardware_revision=inventory.get("vid") or version.get("hardware_revision"),
        ios_image=version.get("ios_image"),
        bootloader=version.get("bootloader"),
        interface_counts=version.get("interface_counts"),
        telemetry_complete=not section_errors,
    )
    if section_errors:
        unavailable = ", ".join(section_errors)
        summary.summary = f"{summary.summary} Partial telemetry: {unavailable} unavailable."

    return DashboardResponse(
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
        sectionErrors=section_errors,
    )


# --- system endpoints -----------------------------------------------------

@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "switchops-backend",
        "mockMode": settings.mock_mode,
        "enableWriteActions": settings.enable_write_actions,
    }


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
        enableWriteActions=settings.enable_write_actions,
        switchHost=raw["switch_host"],
        switchUsername=raw["switch_username"],
        switchDeviceType=raw["switch_device_type"],
    )


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


# --- read-only switch endpoints -------------------------------------------

@app.get("/api/switch/interfaces", response_model=InterfaceStatusResponse, response_model_by_alias=True)
def get_interfaces():
    with switch_session() as c:
        out = run_and_audit(c, symbol="show_interfaces_status")
    return InterfaceStatusResponse(interfaces=parse_interface_status(out))


@app.get("/api/switch/errors", response_model=InterfaceErrorsResponse, response_model_by_alias=True)
def get_errors():
    with switch_session() as c:
        out = run_and_audit(c, symbol="show_interfaces_counters_errors")
    counters = parse_interface_errors(out)
    total = sum(c.total for c in counters)
    return InterfaceErrorsResponse(counters=counters, totalErrors=total, healthy=(total == 0))


@app.get("/api/switch/poe", response_model=PoeResponse, response_model_by_alias=True)
def get_poe():
    with switch_session() as c:
        out = run_and_audit(c, symbol="show_power_inline")
    return parse_poe(out)


@app.get("/api/switch/environment", response_model=EnvironmentStatus, response_model_by_alias=True)
def get_environment():
    with switch_session() as c:
        out = run_and_audit(c, symbol="show_env_all")
    return parse_environment(out)


@app.get("/api/switch/cpu", response_model=CpuStatus, response_model_by_alias=True)
def get_cpu():
    with switch_session() as c:
        out = run_and_audit(c, symbol="show_processes_cpu")
    return parse_cpu(out)


@app.get("/api/switch/memory", response_model=MemoryStatus, response_model_by_alias=True)
def get_memory():
    with switch_session() as c:
        out = run_and_audit(c, symbol="show_memory_statistics")
    return parse_memory(out)


@app.get("/api/switch/mac-table", response_model=MacTableResponse, response_model_by_alias=True)
def get_mac_table():
    with switch_session() as c:
        out = run_and_audit(c, symbol="show_mac_address_table")
    return MacTableResponse(entries=parse_mac_table(out))


@app.get("/api/switch/logs", response_model=LogsResponse, response_model_by_alias=True)
def get_logs():
    with switch_session() as c:
        out = run_and_audit(c, symbol="show_logging")
    return parse_logs(out)


@app.get(
    "/api/switch/dashboard",
    response_model=DashboardResponse,
    response_model_by_alias=True,
)
def get_dashboard():
    with switch_session() as client:
        return _collect_dashboard(client)


@app.get("/api/switch/summary", response_model=SwitchSummary, response_model_by_alias=True)
def get_summary():
    with switch_session() as client:
        return _collect_dashboard(client).summary


@app.get("/api/switch/audit", response_model=AuditResponse, response_model_by_alias=True)
def get_audit(limit: int = Query(default=100, ge=1, le=500)):
    events = get_audit_store().recent(limit=limit)
    return AuditResponse(events=events)


@app.post("/api/switch/backup-config", response_model=BackupResult, response_model_by_alias=True)
def post_backup_config():
    with switch_session() as c:
        return backup_running_config(c)


# --- safe-write endpoints (disabled unless enable_write_actions) ----------

def _require_write_actions() -> None:
    if not settings.enable_write_actions:
        raise HTTPException(
            status_code=403,
            detail="Write actions are disabled. Set ENABLE_WRITE_ACTIONS=true to enable.",
        )


def _decode_port(port: str) -> str:
    """Path segments can't contain '/'; clients send 'Gi0-6' which we restore."""
    return port.replace("-", "/")


def _prevalidate_write(action: str, interface: str | None = None, value: str | None = None) -> None:
    """Reject unsafe input before opening any connection to the switch."""
    build_write_action(action, interface=interface, value=value)


@app.post("/api/switch/ports/{port}/enable", response_model=WriteActionResult, response_model_by_alias=True)
def post_port_enable(port: str):
    _require_write_actions()
    decoded = _decode_port(port)
    _prevalidate_write("enable_port", interface=decoded)
    with switch_session() as c:
        return execute_safe_write(c, action="enable_port", interface=decoded)


@app.post("/api/switch/ports/{port}/disable", response_model=WriteActionResult, response_model_by_alias=True)
def post_port_disable(port: str):
    _require_write_actions()
    decoded = _decode_port(port)
    _prevalidate_write("disable_port", interface=decoded)
    with switch_session() as c:
        return execute_safe_write(c, action="disable_port", interface=decoded)


@app.post("/api/switch/ports/{port}/description", response_model=WriteActionResult, response_model_by_alias=True)
def post_port_description(port: str, req: PortDescriptionRequest):
    _require_write_actions()
    decoded = _decode_port(port)
    _prevalidate_write("set_port_description", interface=decoded, value=req.description)
    with switch_session() as c:
        return execute_safe_write(c, action="set_port_description", interface=decoded, value=req.description)


@app.post("/api/switch/ports/{port}/poe/enable", response_model=WriteActionResult, response_model_by_alias=True)
def post_port_poe_enable(port: str):
    _require_write_actions()
    decoded = _decode_port(port)
    _prevalidate_write("enable_poe", interface=decoded)
    with switch_session() as c:
        return execute_safe_write(c, action="enable_poe", interface=decoded)


@app.post("/api/switch/save-config", response_model=WriteActionResult, response_model_by_alias=True)
def post_save_config():
    _require_write_actions()
    _prevalidate_write("save_config")
    with switch_session() as c:
        return execute_safe_write(c, action="save_config")


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
        host, args.port, settings.mock_mode, settings.enable_write_actions,
    )
    uvicorn.run(app, host=host, port=args.port, log_config=None)


if __name__ == "__main__":
    main()
