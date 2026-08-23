"""Evidence-backed orchestration above the bounded operation executor.

v0.6 is deliberately single-device and single-step.  This service never
renders arbitrary IOS and never grants write authority from discovery data.
It holds the existing write lock and interface-policy guard across the final
before snapshot, the trusted primitive operation, and the after snapshot.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .change_models import (
    AssuranceConfigurationSnapshot,
    AssuranceEvidenceSnapshot,
    AssuranceHealthSnapshot,
    AssuranceInterfaceSnapshot,
    AssuranceSnapshot,
    AssuranceTopologySnapshot,
    BlastRadius,
    ChangeComparison,
    ChangeDifference,
    ChangePlan,
    ChangePlanRequest,
    ChangePreflight,
    ChangeSession,
    DeclaredChangeIntent,
    ExpectedChangeEffect,
    PreflightCheck,
)
from .change_store import ChangeStore, get_change_store
from .command_registry import normalize_interface, sanitize_description, short_interface
from .credential_store import get_credential_store
from .errors import CommandNotAllowedError, SwitchOpsError
from .interface_policy import device_key, get_interface_policy_store
from .models import OperationKind, OperationResult
from .operations import (
    OPERATIONS,
    _interface_config,
    _rollback_commands,
    configuration_fingerprint,
    get_write_lock,
    run_operation,
)
from .parsers.errors import parse_interface_errors
from .parsers.interfaces import parse_interface_status
from .parsers.mac_table import parse_mac_table
from .parsers.poe import parse_poe
from .switch_client import SwitchClient
from .tools.read_only import run_and_audit
from .topology import interface_admin_state


logger = logging.getLogger(__name__)
SessionListener = Callable[[ChangeSession], None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_and_short(interface: str) -> tuple[str, str]:
    canonical = normalize_interface(interface)
    return canonical, short_interface(canonical)


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _entity_key(mac: str) -> str:
    normalized = "".join(char for char in mac.lower() if char in "0123456789abcdef")
    return "learned-" + hashlib.sha256(normalized.encode("ascii")).hexdigest()[:16]


def _intent_for(kind: OperationKind, interface: str, value: Optional[str]) -> DeclaredChangeIntent:
    common_unacceptable = [
        "A different bounded property on the target interface changes unexpectedly.",
        "Any unrelated interface changes during the assurance window.",
        "The startup configuration changes without an explicit save action.",
    ]
    if kind == "admin_down":
        summary = f"Administratively disable {interface}."
        effects = [
            ExpectedChangeEffect(category="interface", field="adminState", expectation="down", required=True),
            ExpectedChangeEffect(category="interface", field="operState", expectation="may become down"),
            ExpectedChangeEffect(category="topology", field="target attachment", expectation="may disappear or age"),
            ExpectedChangeEffect(category="topology", field="learned addresses", expectation="may disappear or age"),
        ]
    elif kind == "admin_up":
        summary = f"Administratively enable {interface}."
        effects = [
            ExpectedChangeEffect(category="interface", field="adminState", expectation="up", required=True),
            ExpectedChangeEffect(category="interface", field="operState", expectation="may remain down until a link exists"),
            ExpectedChangeEffect(category="topology", field="target attachment", expectation="may appear if a device is connected"),
        ]
    elif kind == "poe_auto":
        summary = f"Set Power over Ethernet policy to auto on {interface}."
        effects = [
            ExpectedChangeEffect(category="interface", field="poeAdmin", expectation="auto", required=True),
            ExpectedChangeEffect(category="interface", field="poeOper", expectation="may begin supplying power"),
            ExpectedChangeEffect(category="topology", field="target attachment", expectation="may appear after a powered device starts"),
        ]
    elif kind == "poe_never":
        summary = f"Disable Power over Ethernet on {interface}."
        effects = [
            ExpectedChangeEffect(category="interface", field="poeAdmin", expectation="never", required=True),
            ExpectedChangeEffect(category="interface", field="poeOper", expectation="may stop supplying power"),
            ExpectedChangeEffect(category="topology", field="target attachment", expectation="may disappear if the endpoint depends on PoE"),
        ]
    else:
        summary = f"Set the interface description on {interface} to {value!r}."
        effects = [
            ExpectedChangeEffect(category="interface", field="description", expectation=value or "", required=True),
            ExpectedChangeEffect(category="topology", field="expected intent", expectation="may be reclassified after discovery refresh"),
        ]
    return DeclaredChangeIntent(
        summary=summary,
        expectedPostconditions=effects,
        unacceptableEffects=common_unacceptable,
    )


def _safe_value(kind: OperationKind, value: Optional[str]) -> Optional[str]:
    spec = OPERATIONS.get(kind)
    if spec is None:
        raise CommandNotAllowedError(f"{kind!r} is not a supported operation.")
    if spec.needs_value:
        return sanitize_description(value or "")
    if value not in {None, ""}:
        raise CommandNotAllowedError(f"{kind!r} does not accept a value.")
    return None


def _target_health(context: dict[str, Any], interface: str) -> str:
    health = context.get("health") or {}
    ranked = {"HEALTHY": 0, "NOTICE": 1, "ATTENTION": 2, "CRITICAL": 3}
    state = "HEALTHY"
    for reason in health.get("reasons") or []:
        if str(reason.get("interface") or "").lower() != interface.lower():
            continue
        candidate = str(reason.get("severity") or "UNKNOWN").upper()
        if ranked.get(candidate, -1) > ranked.get(state, -1):
            state = candidate
    return state if health else "UNKNOWN"


def capture_assurance_snapshot(
    client: SwitchClient,
    *,
    device_id: str,
    interface: str,
    kind: OperationKind,
    value: Optional[str],
    context: dict[str, Any],
    actor: str = "operator",
) -> AssuranceSnapshot:
    """Capture normalized proof without persisting raw configuration or MACs."""
    canonical, target_short = _canonical_and_short(interface)
    safe_value = _safe_value(kind, value)
    interfaces = parse_interface_status(
        run_and_audit(client, symbol="show_interfaces_status", actor=actor)
    )
    poe = parse_poe(run_and_audit(client, symbol="show_power_inline", actor=actor))
    errors = parse_interface_errors(
        run_and_audit(client, symbol="show_interfaces_counters_errors", actor=actor)
    )
    mac_entries = parse_mac_table(
        run_and_audit(client, symbol="show_mac_address_table", actor=actor)
    )
    running = run_and_audit(client, symbol="show_running_config", actor=actor)
    startup = run_and_audit(client, symbol="show_startup_config", actor=actor)

    config_state = _interface_config(running, canonical)
    rollback = (
        _rollback_commands(kind, canonical, config_state)
        if config_state is not None
        else None
    )
    poe_by_port = {item.interface.lower(): item for item in poe.ports}
    error_by_port = {item.port.lower(): item.total for item in errors}
    learned_by_port: dict[str, list[str]] = {}
    for entry in mac_entries:
        if entry.port.upper() == "CPU" or entry.vlan.lower() == "all":
            continue
        learned_by_port.setdefault(entry.port.lower(), []).append(_entity_key(entry.mac))

    def interface_snapshot(item: Any) -> AssuranceInterfaceSnapshot:
        port = short_interface(normalize_interface(item.port))
        poe_item = poe_by_port.get(port.lower())
        is_target = port.lower() == target_short.lower()
        target_config = config_state if is_target else None
        return AssuranceInterfaceSnapshot(
            port=port,
            present=True,
            adminState=interface_admin_state(item.status),
            operState="up" if item.status.strip().lower() == "connected" else "down",
            description=(target_config.description if target_config else item.name or ""),
            vlan=item.vlan,
            speed=item.speed,
            duplex=item.duplex,
            poeAdmin=(
                target_config.poe_admin
                if target_config is not None
                else (poe_item.admin if poe_item else "unknown")
            ),
            poeOper=poe_item.oper if poe_item else "unknown",
            errorTotal=error_by_port.get(port.lower(), 0),
            learnedMacCount=len(set(learned_by_port.get(port.lower(), []))),
        )

    normalized = [interface_snapshot(item) for item in interfaces]
    target = next(
        (item for item in normalized if item.port.lower() == target_short.lower()),
        AssuranceInterfaceSnapshot(port=target_short, present=False),
    )
    others = [item for item in normalized if item.port.lower() != target_short.lower()]

    topology = context.get("topology") or {}
    topology_interfaces = topology.get("interfaces") or []
    topology_links = topology.get("links") or []
    topology_devices = topology.get("devices") or []
    topology_target = next(
        (
            item
            for item in topology_interfaces
            if str(item.get("port") or "").lower() == target_short.lower()
        ),
        {},
    )
    target_links = [
        link
        for link in topology_links
        if str(link.get("fromInterface") or "").lower() == target_short.lower()
    ]
    relationships = sorted(
        {str(link.get("relationship") or "unknown") for link in target_links}
    )
    attached = {
        str(link.get("toDeviceId"))
        for link in target_links
        if link.get("relationship") in {"attached-endpoint", "direct-neighbour"}
        and link.get("toDeviceId")
    }
    attached.update(
        str(device.get("id"))
        for device in topology_devices
        if str(device.get("connectedInterface") or "").lower() == target_short.lower()
        and device.get("existenceState") != "historical"
        and device.get("id")
    )
    local_host_correlated = any(
        str(device.get("connectedInterface") or "").lower() == target_short.lower()
        and device.get("existenceState") != "historical"
        and (
            str(device.get("identitySource") or "") == "local-host"
            or str(device.get("name") or "").strip().lower() == "this switchops pc"
        )
        for device in topology_devices
    )
    learned = set(learned_by_port.get(target_short.lower(), []))
    learned.update(
        str(link.get("toDeviceId"))
        for link in target_links
        if link.get("relationship") == "learned-behind" and link.get("toDeviceId")
    )
    reconciliation = context.get("reconciliation") or {}
    reconciliation_target = next(
        (
            item
            for item in reconciliation.get("interfaces") or []
            if str(item.get("interface") or "").lower() == target_short.lower()
        ),
        {},
    )
    other_topology = {
        "links": [
            link
            for link in topology_links
            if str(link.get("fromInterface") or "").lower() != target_short.lower()
        ],
        "devices": [
            device
            for device in topology_devices
            if str(device.get("connectedInterface") or "").lower() != target_short.lower()
        ],
    }
    evidence_ids = set(topology_target.get("evidenceIds") or [])
    for link in target_links:
        evidence_ids.update(link.get("evidenceIds") or [])

    generated_at = topology.get("generatedAt")
    try:
        topology_at = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00")) if generated_at else None
    except ValueError:
        topology_at = None
    connection = context.get("connection") or {}
    health = context.get("health") or {}
    return AssuranceSnapshot(
        capturedAt=_now(),
        deviceId=device_id,
        targetInterface=target_short,
        configuration=AssuranceConfigurationSnapshot(
            runningFingerprint=configuration_fingerprint(running),
            startupFingerprint=configuration_fingerprint(startup),
            runningDiffersFromStartup=(
                configuration_fingerprint(running) != configuration_fingerprint(startup)
            ),
            rollbackRepresentable=rollback is not None,
        ),
        target=target,
        otherInterfaces=others,
        topology=AssuranceTopologySnapshot(
            relationships=relationships,
            attachedEntityIds=sorted(attached),
            learnedBehindEntityIds=sorted(learned - attached),
            expectedRelationship=topology_target.get("expectedName"),
            reconciliationState=reconciliation_target.get("status"),
            targetRole=str(topology_target.get("role") or "unknown"),
            localHostCorrelated=local_host_correlated,
            otherTopologyFingerprint=_fingerprint(other_topology),
        ),
        health=AssuranceHealthSnapshot(
            connectionState=str(connection.get("state") or "unknown"),
            deviceHealth=str(health.get("state") or "UNKNOWN"),
            targetHealth=_target_health(context, target_short),
            targetErrorTotal=target.error_total,
        ),
        evidence=AssuranceEvidenceSnapshot(
            topologyObservedAt=topology_at,
            freshness=str(topology_target.get("freshness") or "unknown"),
            evidenceIds=sorted(evidence_ids),
        ),
    )


def _preflight(
    *,
    plan: ChangePlan,
    snapshot: AssuranceSnapshot,
    host: Optional[str],
) -> ChangePreflight:
    step = plan.steps[0]
    target = snapshot.target
    checks: list[PreflightCheck] = []

    def add(code: str, label: str, status: str, detail: str, evidence: list[str] | None = None) -> None:
        checks.append(
            PreflightCheck(
                code=code,
                label=label,
                status=status,  # type: ignore[arg-type]
                detail=detail,
                evidence=evidence or [],
            )
        )

    add("operation_allowlisted", "Bounded operation", "pass", f"{step.kind} is in the fixed operation catalogue.")
    current_device_id = f"device-{device_key(str(host))[:16]}" if host else None
    device_matches = current_device_id == plan.device_id
    add(
        "device_target",
        "Plan device",
        "pass" if device_matches else "block",
        "The plan is bound to the currently configured device."
        if device_matches
        else "The configured device changed after this plan was created. Create a new plan for the current device.",
    )
    connection_state = snapshot.health.connection_state.lower()
    add(
        "device_live",
        "Device session",
        "pass" if connection_state == "live" else "block",
        "The serialized device session is LIVE."
        if connection_state == "live"
        else f"The serialized device session is {connection_state}; execution requires LIVE state.",
    )
    add(
        "interface_exists",
        "Interface exists",
        "pass" if target.present else "block",
        f"{plan.target_interface} is currently reported by the switch."
        if target.present
        else f"{plan.target_interface} is not in the current switch interface table.",
    )
    policy = get_interface_policy_store()
    policy_state = policy.state_for(host, plan.target_interface)
    add(
        "interface_policy",
        "Interface policy",
        "pass" if policy_state == "OPERABLE" else "block",
        f"{plan.target_interface} is explicitly OPERABLE."
        if policy_state == "OPERABLE"
        else f"{plan.target_interface} is {policy_state}; discovery evidence cannot grant write authority.",
    )
    capability = policy.controlled_writes_enabled()
    add(
        "controlled_writes",
        "Controlled writes",
        "pass" if capability else "block",
        "Controlled writes are enabled for this installation."
        if capability
        else "Controlled writes are disabled in local settings. Preflight remains read-only.",
    )
    unlocked = get_write_lock().unlocked
    add(
        "session_unlock",
        "Process-local unlock",
        "pass" if unlocked else "info",
        "Control is unlocked for this process."
        if unlocked
        else "Control remains locked. Unlock is required only when the operator chooses Execute.",
    )
    add(
        "rollback_representable",
        "Bounded rollback",
        "pass" if snapshot.configuration.rollback_representable else "block",
        "The current property can be restored using the bounded operation vocabulary."
        if snapshot.configuration.rollback_representable
        else "The current property cannot be represented safely by the bounded rollback vocabulary.",
    )

    freshness = snapshot.evidence.freshness
    add(
        "evidence_freshness",
        "Evidence freshness",
        "pass" if freshness == "current" else "warn",
        "Target topology evidence is current."
        if freshness == "current"
        else f"Target topology evidence is {freshness}; impact analysis may be incomplete.",
        snapshot.evidence.evidence_ids,
    )
    attached_count = len(snapshot.topology.attached_entity_ids)
    add(
        "attached_endpoints",
        "Observed attachment",
        "info",
        f"Current evidence identifies {attached_count} attached endpoint(s) on the target.",
        snapshot.topology.attached_entity_ids,
    )
    learned_count = len(snapshot.topology.learned_behind_entity_ids)
    add(
        "learned_behind",
        "Learned through target",
        "warn" if learned_count else "pass",
        f"{learned_count} entity identifier(s) are learned through this interface; they are not proof of direct attachment."
        if learned_count
        else "No learned-behind entities are currently attributed to the target.",
        snapshot.topology.learned_behind_entity_ids,
    )

    topology = snapshot.topology
    disruptive = step.kind in {"admin_down", "poe_never"}
    local_host = topology.local_host_correlated
    # A gateway-path assertion is direct control-path evidence. Uplink role and
    # multiple learned-behind entities are weaker and remain explicit warnings.
    gateway = "gateway-path" in topology.relationships
    confirmed_path = disruptive and (gateway or local_host)
    possible_path = topology.target_role == "uplink" or learned_count > 0
    control_path = "confirmed" if confirmed_path else "possible" if possible_path else "clear"
    control_detail = (
        "Current evidence places the local control or gateway path through this target."
        if confirmed_path
        else "The target looks like an uplink or carries learned-behind entities; SwitchOps cannot prove the control path is elsewhere."
        if possible_path
        else "No current evidence places the SwitchOps control or gateway path through this target."
    )
    add(
        "control_path",
        "Control path",
        "block" if confirmed_path else "warn" if disruptive and possible_path else "pass",
        control_detail,
        snapshot.evidence.evidence_ids,
    )
    if topology.reconciliation_state == "uncertain":
        add(
            "topology_uncertainty",
            "Topology reconciliation",
            "warn",
            "The target currently has conflicting or insufficient topology evidence.",
        )
    else:
        add(
            "topology_uncertainty",
            "Topology reconciliation",
            "pass",
            f"Target reconciliation is {topology.reconciliation_state or 'not yet applicable'}.",
        )
    add(
        "running_startup",
        "Running versus startup",
        "warn" if snapshot.configuration.running_differs_from_startup else "pass",
        "Running configuration already differs from startup before this change."
        if snapshot.configuration.running_differs_from_startup
        else "Running and startup configuration fingerprints currently match.",
    )

    limitations: list[str] = []
    if snapshot.evidence.freshness != "current":
        limitations.append("Target discovery evidence is not current.")
    if attached_count and not snapshot.evidence.evidence_ids:
        limitations.append("An attachment exists, but no identifying evidence IDs are available.")
    if not attached_count:
        limitations.append("No current attached endpoint identity is established.")
    outcome = "blocked" if any(check.status == "block" for check in checks) else "ready"
    return ChangePreflight(
        evaluatedAt=_now(),
        outcome=outcome,
        checks=checks,
        impact=BlastRadius(
            targetInterface=plan.target_interface,
            attachedEndpoints=attached_count,
            learnedBehind=learned_count,
            expectedRelationship=topology.expected_relationship,
            controlPath=control_path,
            controlPathDetail=control_detail,
            confidenceLimitations=limitations,
        ),
        snapshot=snapshot,
    )


def compare_snapshots(
    *,
    plan: ChangePlan,
    before: AssuranceSnapshot,
    after: AssuranceSnapshot,
    operation: OperationResult,
) -> ChangeComparison:
    step = plan.steps[0]
    differences: list[ChangeDifference] = []
    warnings: list[str] = []

    def diff(scope: str, field: str, old: Any, new: Any, assessment: str, detail: str, interface: str | None = None) -> None:
        if old == new:
            return
        differences.append(
            ChangeDifference(
                scope=scope,  # type: ignore[arg-type]
                field=field,
                before=old,
                after=new,
                assessment=assessment,  # type: ignore[arg-type]
                detail=detail,
                interface=interface,
            )
        )
        if assessment == "warning":
            warnings.append(detail)

    target = after.target
    if not target.present:
        direct = "unknown"
    elif step.kind == "admin_down":
        direct = "met" if target.admin_state == "down" else "not_met"
    elif step.kind == "admin_up":
        direct = "met" if target.admin_state == "up" else "not_met"
    elif step.kind == "poe_auto":
        direct = "met" if target.poe_admin.lower() == "auto" else "not_met"
    elif step.kind == "poe_never":
        direct = "met" if target.poe_admin.lower() == "never" else "not_met"
    else:
        direct = "met" if target.description == (step.value or "") else "not_met"

    allowed_target = {
        "admin_up": {"admin_state", "oper_state", "learned_mac_count"},
        "admin_down": {"admin_state", "oper_state", "learned_mac_count"},
        "poe_auto": {"poe_admin", "poe_oper", "oper_state", "learned_mac_count"},
        "poe_never": {"poe_admin", "poe_oper", "oper_state", "learned_mac_count"},
        "set_description": {"description"},
    }[step.kind]
    labels = {
        "admin_state": "administrative state",
        "oper_state": "link state",
        "description": "description",
        "vlan": "VLAN",
        "speed": "speed",
        "duplex": "duplex",
        "poe_admin": "PoE policy",
        "poe_oper": "PoE operational state",
        "error_total": "error counter",
        "learned_mac_count": "learned-address count",
    }
    for field, label in labels.items():
        old, new = getattr(before.target, field), getattr(after.target, field)
        expected = field in allowed_target
        diff(
            "target",
            field,
            old,
            new,
            "expected" if expected else "warning",
            f"The target {label} changed as an expected consequence of the plan."
            if expected
            else f"The target {label} changed outside the plan's declared effects.",
            plan.target_interface,
        )

    before_others = {item.port: item for item in before.other_interfaces}
    after_others = {item.port: item for item in after.other_interfaces}
    for port in sorted(set(before_others) | set(after_others)):
        old = before_others.get(port)
        new = after_others.get(port)
        if old is None or new is None:
            diff(
                "unrelated", "presence", bool(old), bool(new), "warning",
                f"Unrelated interface {port} appeared or disappeared during the assurance window.", port,
            )
            continue
        for field in labels:
            old_value, new_value = getattr(old, field), getattr(new, field)
            diff(
                "unrelated", field, old_value, new_value, "warning",
                f"Unrelated interface {port} changed {labels[field]} during the assurance window. "
                "Temporal proximity does not prove the requested change caused it.", port,
            )

    diff(
        "configuration",
        "runningFingerprint",
        before.configuration.running_fingerprint,
        after.configuration.running_fingerprint,
        "expected" if operation.requires_save else "info",
        "The running configuration fingerprint changed."
        if operation.requires_save
        else "The running configuration fingerprint changed even though no change was required.",
    )
    diff(
        "configuration",
        "startupFingerprint",
        before.configuration.startup_fingerprint,
        after.configuration.startup_fingerprint,
        "warning",
        "The startup configuration changed without an explicit save action.",
    )
    before_attached = set(before.topology.attached_entity_ids)
    after_attached = set(after.topology.attached_entity_ids)
    diff(
        "topology", "targetAttachments", sorted(before_attached), sorted(after_attached),
        "expected" if step.kind in {"admin_up", "admin_down", "poe_auto", "poe_never"} else "warning",
        "Target attachment evidence changed consistently with a potentially disruptive port operation."
        if step.kind in {"admin_up", "admin_down", "poe_auto", "poe_never"}
        else "Target attachment evidence changed during a description-only operation.",
        plan.target_interface,
    )
    diff(
        "topology",
        "learnedBehindEntities",
        before.topology.learned_behind_entity_ids,
        after.topology.learned_behind_entity_ids,
        "expected" if step.kind in {"admin_up", "admin_down", "poe_auto", "poe_never"} else "warning",
        "Learned-behind evidence changed consistently with a potentially disruptive port operation."
        if step.kind in {"admin_up", "admin_down", "poe_auto", "poe_never"}
        else "Learned-behind evidence changed during a description-only operation.",
        plan.target_interface,
    )
    diff(
        "topology", "otherTopologyFingerprint",
        before.topology.other_topology_fingerprint,
        after.topology.other_topology_fingerprint,
        "warning",
        "Topology away from the target changed during the assurance window. SwitchOps records correlation, not causation.",
    )
    diff(
        "health", "deviceHealth", before.health.device_health, after.health.device_health,
        "warning", "The device health classification changed during the assurance window.",
    )
    diff(
        "health", "targetHealth", before.health.target_health, after.health.target_health,
        "warning", "The target interface health classification changed during the assurance window.",
        plan.target_interface,
    )
    diff(
        "health", "connectionState", before.health.connection_state, after.health.connection_state,
        "warning", "The device connection classification changed during the assurance window.",
    )
    if after.target.error_total > before.target.error_total:
        diff(
            "health", "targetErrorTotal", before.target.error_total, after.target.error_total,
            "warning", "The target cumulative error counter increased during the assurance window.",
            plan.target_interface,
        )

    if direct == "met" and not warnings:
        summary = "The declared postcondition was observed and no unrelated differences were detected."
    elif direct == "met":
        summary = "The declared postcondition was observed, but independent differences need attention."
    elif direct == "not_met":
        summary = "The assurance snapshot does not show the declared postcondition."
    else:
        summary = "The final target state could not be established."
    return ChangeComparison(
        evaluatedAt=_now(),
        directPostcondition=direct,
        differences=differences,
        warnings=list(dict.fromkeys(warnings)),
        summary=summary,
    )


class ChangeAssuranceService:
    def __init__(self, store: Optional[ChangeStore] = None) -> None:
        self.store = store or get_change_store()

    def _get(self, session_id: str) -> ChangeSession:
        session = self.store.get(session_id)
        if session is None:
            raise CommandNotAllowedError("Unknown Change Assurance session.")
        return session

    def _save(self, session: ChangeSession, listener: Optional[SessionListener] = None) -> ChangeSession:
        saved = self.store.save(session)
        if listener:
            try:
                listener(saved)
            except Exception:  # pragma: no cover - progress must not break safety
                logger.debug("Change-session listener raised; ignoring.")
        return saved

    def create_plan(
        self,
        request: ChangePlanRequest,
        *,
        device_id: str,
    ) -> ChangeSession:
        step = request.steps[0]
        _, interface = _canonical_and_short(step.interface)
        value = _safe_value(step.kind, step.value)
        now = _now()
        plan = ChangePlan(
            id=f"plan-{uuid.uuid4().hex}",
            deviceId=device_id,
            targetInterface=interface,
            steps=[{"interface": interface, "kind": step.kind, "value": value}],
            declaredIntent=_intent_for(step.kind, interface, value),
            createdAt=now,
        )
        if request.summary:
            # An operator note may add context, but it must not replace the
            # generated statement of what the bounded operation will do.
            plan.declared_intent.summary += f" Operator note: {request.summary.strip()}"
        session = ChangeSession(
            id=f"change-{uuid.uuid4().hex}",
            plan=plan,
            status="planned",
            outcomeDetail="Plan created. No device change has been attempted.",
            createdAt=now,
            updatedAt=now,
        )
        return self.store.save(session)

    def run_preflight(
        self,
        session_id: str,
        client: SwitchClient,
        *,
        context: dict[str, Any],
        listener: Optional[SessionListener] = None,
    ) -> ChangeSession:
        session = self._get(session_id)
        if session.status in {"executing", "verifying", "rolling_back"}:
            raise CommandNotAllowedError("This change session is already in progress.")
        if session.status in {
            "rolled_back",
            "succeeded",
            "succeeded_with_warnings",
            "indeterminate",
        }:
            raise CommandNotAllowedError(
                "Completed or indeterminate change sessions are immutable. Create a new plan."
            )
        session.status = "preflight"
        session.outcome_detail = "Read-only preflight is collecting current evidence."
        self._save(session, listener)
        step = session.plan.steps[0]
        host = get_credential_store().status().get("switch_host")
        try:
            snapshot = capture_assurance_snapshot(
                client,
                device_id=session.plan.device_id,
                interface=session.plan.target_interface,
                kind=step.kind,
                value=step.value,
                context=context,
            )
            session.preflight = _preflight(plan=session.plan, snapshot=snapshot, host=host)
            session.status = "ready" if session.preflight.outcome == "ready" else "blocked"
            session.outcome_detail = (
                "Preflight passed. Unlock remains a separate requirement for execution."
                if session.status == "ready"
                else "Preflight found one or more blocking conditions. No change was attempted."
            )
        except Exception as exc:
            logger.warning("Change preflight capture failed (%s)", type(exc).__name__)
            session.preflight = ChangePreflight(
                evaluatedAt=_now(),
                outcome="blocked",
                checks=[PreflightCheck(
                    code="snapshot_capture",
                    label="Assurance snapshot",
                    status="block",
                    detail="SwitchOps could not capture the read-only evidence required for a safe change.",
                )],
                impact=BlastRadius(
                    targetInterface=session.plan.target_interface,
                    controlPath="unknown",
                    controlPathDetail="Control-path evidence is unavailable because preflight capture failed.",
                    confidenceLimitations=["No complete assurance snapshot is available."],
                ),
            )
            session.status = "blocked"
            session.outcome_detail = "Preflight evidence could not be captured. No change was attempted."
        return self._save(session, listener)

    def block_before_execution(self, session_id: str, detail: str) -> ChangeSession:
        session = self._get(session_id)
        if session.status not in {"planned", "preflight", "ready"}:
            # A duplicate or late request must never overwrite an in-flight or
            # terminal audit result with a synthetic BLOCKED state.
            return session
        session.status = "blocked"
        session.outcome_detail = detail
        return self.store.save(session)

    def block_preflight_unavailable(self, session_id: str) -> ChangeSession:
        session = self._get(session_id)
        session.preflight = ChangePreflight(
            evaluatedAt=_now(),
            outcome="blocked",
            checks=[PreflightCheck(
                code="device_live",
                label="Device session",
                status="block",
                detail="The serialized device session is unavailable, so current assurance evidence cannot be collected.",
            )],
            impact=BlastRadius(
                targetInterface=session.plan.target_interface,
                controlPath="unknown",
                controlPathDetail="Control-path evidence is unavailable while the device session is offline.",
                confidenceLimitations=["No current assurance snapshot is available."],
            ),
        )
        session.status = "blocked"
        session.outcome_detail = "Preflight could not reach the device. No change was attempted."
        return self.store.save(session)

    def execute(
        self,
        session_id: str,
        client: SwitchClient,
        *,
        context_provider: Callable[[], dict[str, Any]],
        listener: Optional[SessionListener] = None,
    ) -> ChangeSession:
        session = self._get(session_id)
        if session.status != "ready":
            raise CommandNotAllowedError("Run a successful read-only preflight before execution.")
        step = session.plan.steps[0]
        host = get_credential_store().status().get("switch_host")

        # These are the exact existing authorization guards. Discovery and
        # preflight evidence can block a write, but can never satisfy a guard.
        with get_write_lock().operation_guard():
            with get_interface_policy_store().operation_guard(host, session.plan.target_interface):
                session.status = "executing"
                session.outcome_detail = "Capturing the final before-state under stable write authorization."
                self._save(session, listener)
                try:
                    before = capture_assurance_snapshot(
                        client,
                        device_id=session.plan.device_id,
                        interface=session.plan.target_interface,
                        kind=step.kind,
                        value=step.value,
                        context=context_provider(),
                    )
                except Exception as exc:
                    logger.warning("Final before-snapshot failed (%s)", type(exc).__name__)
                    session.status = "blocked"
                    session.outcome_detail = (
                        "The final before-state could not be captured under stable authorization. "
                        "No IOS configuration was attempted."
                    )
                    return self._save(session, listener)
                session.before_snapshot = before
                session.preflight = _preflight(plan=session.plan, snapshot=before, host=host)
                if session.preflight.outcome == "blocked":
                    session.status = "blocked"
                    session.outcome_detail = "Conditions changed after review; execution was blocked before IOS configuration."
                    return self._save(session, listener)
                self._save(session, listener)

                def operation_progress(stages: list[Any]) -> None:
                    session.operation_stages = list(stages)
                    if stages:
                        name = stages[-1].name
                        if name == "verify":
                            session.status = "verifying"
                        elif name == "rollback":
                            session.status = "rolling_back"
                        else:
                            session.status = "executing"
                    self._save(session, listener)

                try:
                    result = run_operation(
                        client,
                        kind=step.kind,
                        interface=session.plan.target_interface,
                        value=step.value,
                        actor="operator",
                        on_progress=operation_progress,
                    )
                except Exception as exc:
                    logger.exception("Primitive operation raised during Change Assurance")
                    session.status = "indeterminate"
                    session.outcome_detail = (
                        "The bounded executor did not return a conclusive result. Inspect the "
                        "target before another change."
                    )
                    return self._save(session, listener)
                session.operation_result = result
                session.status = "verifying"
                session.outcome_detail = "Capturing normalized after-state and checking collateral observations."
                self._save(session, listener)
                try:
                    after = capture_assurance_snapshot(
                        client,
                        device_id=session.plan.device_id,
                        interface=session.plan.target_interface,
                        kind=step.kind,
                        value=step.value,
                        context=context_provider(),
                    )
                    session.after_snapshot = after
                    session.comparison = compare_snapshots(
                        plan=session.plan,
                        before=before,
                        after=after,
                        operation=result,
                    )
                except Exception as exc:
                    logger.warning("Change after-snapshot failed (%s)", type(exc).__name__)
                    session.status = "indeterminate"
                    session.outcome_detail = (
                        "The operation returned, but SwitchOps could not prove the final device state. "
                        "Inspect the target before another change."
                    )
                    return self._save(session, listener)

                if result.status == "rolled_back" and result.rolled_back:
                    session.status = "rolled_back"
                    session.outcome_detail = "The intended state was not verified and the bounded rollback was observed."
                elif result.status != "success":
                    session.status = "indeterminate"
                    session.outcome_detail = (
                        "The requested state was not proven. SwitchOps cannot claim a successful change or rollback."
                    )
                elif session.comparison.direct_postcondition != "met":
                    session.status = "indeterminate"
                    session.outcome_detail = "The primitive verification and assurance snapshot do not establish the same final state."
                elif session.comparison.warnings:
                    session.status = "succeeded_with_warnings"
                    session.outcome_detail = (
                        "The requested state was achieved, but unrelated or unexpected observations need attention."
                    )
                else:
                    session.status = "succeeded"
                    session.outcome_detail = (
                        "The requested state was achieved and observed effects matched the declared change."
                    )
                return self._save(session, listener)


_service: Optional[ChangeAssuranceService] = None


def get_change_assurance_service() -> ChangeAssuranceService:
    global _service
    if _service is None:
        _service = ChangeAssuranceService()
    return _service
