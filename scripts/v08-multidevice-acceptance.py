"""Privacy-safe operator harness for v0.8 real multi-device acceptance.

This script never accepts device credentials or addresses and never creates or
removes a configured device. Onboarding, failure injection, removal, and
re-addition are deliberate operator actions in the SwitchOps UI. The harness
only refreshes the fixed read-only collector and verifies the resulting local
API state.

The saved session lives below ``backend/data`` (gitignored) and contains only
opaque SwitchOps device IDs, categorical results, counts, and timestamps. Raw
API payloads, device labels, interface descriptions, targets, and evidence
details are never written.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_ORIGIN = "http://127.0.0.1:8765"
DEFAULT_SESSION = Path(__file__).resolve().parents[1] / "backend" / "data" / "v08-multidevice-acceptance.json"
ACCEPTANCE_STATUSES = {"PASS", "FAIL", "NOT CONFIGURED", "NOT SUPPORTED", "NOT EXERCISED"}
ADVANCED_CAPABILITIES = {
    "VRF": "VRF inventory",
    "EtherChannel": "EtherChannel",
    "OSPF adjacency": "OSPF neighbours",
    "EIGRP adjacency": "EIGRP neighbours",
    "BGP adjacency": "BGP IPv4 unicast",
    "BFD": "BFD neighbours",
    "EVPN": "BGP EVPN",
    "VXLAN/NVE": "VXLAN/NVE",
    "Segment Routing MPLS": "Segment Routing MPLS",
    "SRv6": "Segment Routing v6",
}
PRIVATE_KEYS = {
    "host",
    "switchHost",
    "username",
    "password",
    "enableSecret",
    "secret",
    "managementIp",
    "mac",
    "serial",
}

Status = Literal["PASS", "FAIL", "NOT CONFIGURED", "NOT SUPPORTED", "NOT EXERCISED"]


class HarnessError(RuntimeError):
    """A privacy-safe acceptance precondition or verification failure."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(status: Status, reason: str) -> dict[str, str]:
    if status not in ACCEPTANCE_STATUSES:
        raise ValueError(f"Invalid acceptance status: {status}")
    return {"status": status, "reason": reason}


def _request_json(path: str, *, method: str = "GET", timeout: float = 420.0) -> Any:
    if not path.startswith("/api/lab-assurance/"):
        raise HarnessError("The harness may call only the Lab Assurance loopback API.")
    request = Request(
        f"{API_ORIGIN}{path}",
        method=method,
        headers={"Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed loopback origin
            return json.load(response)
    except HTTPError as exc:
        raise HarnessError(f"The local Lab Assurance API returned HTTP {exc.code}.") from None
    except (URLError, TimeoutError, json.JSONDecodeError):
        raise HarnessError("The local Lab Assurance API is unavailable or returned invalid JSON.") from None


def _private_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PRIVATE_KEYS:
                found.add(key)
            found.update(_private_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_private_keys(item))
    return found


def _configured_roles(configured: dict[str, Any], secondary_id: str | None = None) -> tuple[str, str]:
    devices = configured.get("devices", [])
    primary = [item for item in devices if item.get("primary")]
    secondaries = [item for item in devices if not item.get("primary")]
    if len(primary) != 1:
        raise HarnessError("Exactly one configured primary device is required.")
    if not secondaries:
        raise HarnessError("No legitimate secondary IOS/IOS-XE device is configured.")
    if secondary_id:
        selected = [item for item in secondaries if item.get("id") == secondary_id]
        if len(selected) != 1:
            raise HarnessError("The selected opaque secondary device ID is not configured.")
        secondary = selected[0]
    elif len(secondaries) == 1:
        secondary = secondaries[0]
    else:
        raise HarnessError("More than one secondary is configured; pass --secondary-id with its opaque lab-* ID.")
    if secondary.get("storage") != "keyring":
        raise HarnessError("The selected secondary is not stored in Windows Credential Manager.")
    return str(primary[0]["id"]), str(secondary["id"])


def _device_state(state: dict[str, Any], device_id: str) -> str:
    device = next((item for item in state.get("devices", []) if item.get("id") == device_id), None)
    return str(device.get("collectionState")) if device else "NOT_COLLECTED"


def capability_acceptance(
    capability: dict[str, Any] | None,
    evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Translate product evidence into the five acceptance outcomes."""
    if capability is None:
        return _result("NOT EXERCISED", "No capability record was emitted.")
    evidence = [evidence_by_id[item] for item in capability.get("evidenceIds", []) if item in evidence_by_id]
    kinds = {str(item.get("kind")) for item in evidence}
    current = any(bool(item.get("current")) for item in evidence)
    state = capability.get("state")
    configured = capability.get("configured")
    observed = capability.get("observed")
    if state == "UNSUPPORTED":
        if "UNSUPPORTED" in kinds and current:
            return _result("NOT SUPPORTED", "Current IOS output explicitly reports unsupported capability.")
        return _result("FAIL", "UNSUPPORTED was asserted without current explicit unsupported evidence.")
    if state == "SUPPORTED":
        if configured is False:
            return _result("NOT CONFIGURED", "Support is observable, but current configuration does not enable the feature.")
        if observed is True or configured is True:
            return _result("PASS", "Current command or configuration evidence proves support.")
        return _result("FAIL", "SUPPORTED was asserted without observed or configured proof.")
    if state != "UNKNOWN":
        return _result("FAIL", "The capability emitted an invalid support state.")
    if "UNSUPPORTED" in kinds:
        return _result("FAIL", "Explicit unsupported evidence was reduced to UNKNOWN.")
    if configured is False:
        return _result("NOT CONFIGURED", "Current configuration evidence does not show the feature configured.")
    if kinds & {"AUTHORIZATION_FAILED", "COMMAND_FAILED", "TRANSPORT_FAILED", "PARSER_FAILED", "UNAVAILABLE"}:
        return _result("NOT EXERCISED", "Collection evidence is inconclusive for platform support.")
    return _result("NOT EXERCISED", "No positive or explicit unsupported evidence was collected.")


def _capability_matrix(state: dict[str, Any], device_ids: dict[str, str]) -> dict[str, dict[str, dict[str, str]]]:
    evidence = {item.get("id"): item for item in state.get("evidence", [])}
    capabilities = state.get("capabilities", [])
    result: dict[str, dict[str, dict[str, str]]] = {}
    for role, device_id in device_ids.items():
        by_name = {
            item.get("name"): item
            for item in capabilities
            if item.get("deviceId") == device_id
        }
        result[role] = {
            label: capability_acceptance(by_name.get(product_name), evidence)
            for label, product_name in ADVANCED_CAPABILITIES.items()
        }
    return result


def _graph_checks(state: dict[str, Any], primary_id: str, secondary_id: str) -> dict[str, dict[str, str]]:
    edges = state.get("edges", [])
    selected_pair = {primary_id, secondary_id}
    direct = [
        item
        for item in edges
        if item.get("kind") == "PHYSICAL"
        and {item.get("fromNodeId"), item.get("toNodeId")} == selected_pair
    ]
    reciprocal = [item for item in direct if item.get("reciprocal")]
    graph_invariants = all(
        item.get("state") == "PROVEN" and item.get("confidence") in {"HIGH", "CONFIRMED"}
        for item in direct
    ) and all(
        item.get("state") == "INFERRED" and item.get("reciprocal") is False
        for item in edges
        if item.get("kind") == "L2_MEMBERSHIP"
    ) and all(
        item.get("state") == "PROVEN"
        and item.get("confidence") in {"HIGH", "CONFIRMED"}
        and item.get("reciprocal") is False
        for item in edges
        if item.get("kind") == "ROUTING_ADJACENCY"
    )
    return {
        "graph_reconciliation": _result(
            "PASS" if graph_invariants else "FAIL",
            "Direct discovery, inferred MAC reachability, and logical adjacency retain distinct semantics."
            if graph_invariants
            else "At least one emitted graph edge violates its evidence semantics.",
        ),
        "one_sided_discovery": _result(
            "PASS" if direct and not reciprocal else "NOT EXERCISED",
            "A one-sided direct relationship was retained at non-reciprocal confidence."
            if direct and not reciprocal
            else "The selected pair did not expose a one-sided direct relationship.",
        ),
        "reciprocal_discovery": _result(
            "PASS" if reciprocal and all(item.get("confidence") == "CONFIRMED" for item in reciprocal) else "FAIL" if reciprocal else "NOT EXERCISED",
            "Reciprocal discovery reconciled at confirmed confidence."
            if reciprocal
            else "The selected pair did not expose reciprocal CDP/LLDP evidence.",
        ),
    }


def evaluate_baseline(
    configured: dict[str, Any],
    state: dict[str, Any],
    *,
    secondary_id: str | None,
    platform_kind: str,
) -> dict[str, Any]:
    leaked = _private_keys(configured)
    primary_id, selected_id = _configured_roles(configured, secondary_id)
    primary_state = _device_state(state, primary_id)
    secondary_state = _device_state(state, selected_id)
    device_ids = {"primary": primary_id, "secondary": selected_id}
    checks = {
        "keyring_onboarding": _result(
            "PASS" if configured.get("keyringAvailable") and not leaked else "FAIL",
            "The secondary is keyring-backed and the device-list API exposes no credential fields."
            if configured.get("keyringAvailable") and not leaked
            else "Keyring availability or the device-list privacy boundary failed.",
        ),
        "independent_collection": _result(
            "PASS" if primary_state == "CURRENT" and secondary_state in {"CURRENT", "PARTIAL"} else "FAIL",
            "Both configured devices produced independently attributed current collection state."
            if primary_state == "CURRENT" and secondary_state in {"CURRENT", "PARTIAL"}
            else "The primary or selected secondary did not produce usable independent collection.",
        ),
    }
    checks.update(_graph_checks(state, primary_id, selected_id))
    return {
        "checkedAt": _now(),
        "platformKind": platform_kind,
        "physicalHardwareAcceptance": platform_kind == "physical",
        "deviceIds": device_ids,
        "collection": {
            "overall": state.get("collectionState", "NOT_COLLECTED"),
            "primary": primary_state,
            "secondary": secondary_state,
        },
        "counts": {
            "configured": len(configured.get("devices", [])),
            "observed": int(state.get("summary", {}).get("observedDevices", 0)),
            "physicalEdges": int(state.get("summary", {}).get("physicalEdges", 0)),
            "logicalNetworks": int(state.get("summary", {}).get("logicalNetworks", 0)),
        },
        "checks": checks,
        "advancedCapabilities": _capability_matrix(state, device_ids),
    }


def evaluate_failure(state: dict[str, Any], *, primary_id: str, secondary_id: str) -> dict[str, Any]:
    primary_state = _device_state(state, primary_id)
    secondary_state = _device_state(state, secondary_id)
    current_primary_evidence = any(
        item.get("deviceId") == primary_id and item.get("current")
        for item in state.get("evidence", [])
    )
    passed = (
        state.get("collectionState") == "PARTIAL"
        and primary_state == "CURRENT"
        and secondary_state == "FAILED"
        and current_primary_evidence
    )
    return {
        "checkedAt": _now(),
        "collection": {
            "overall": state.get("collectionState", "NOT_COLLECTED"),
            "primary": primary_state,
            "secondary": secondary_state,
        },
        "failureIsolation": _result(
            "PASS" if passed else "FAIL",
            "The selected secondary failed independently while primary evidence remained current."
            if passed
            else "The failed-source state did not preserve the required primary/secondary separation.",
        ),
    }


def _load_session(path: Path) -> dict[str, Any]:
    try:
        session = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HarnessError("No valid local acceptance session exists; capture a baseline first.") from None
    if session.get("schema") != 1:
        raise HarnessError("The local acceptance session schema is not supported.")
    return session


def _save_session(path: Path, session: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _refresh(timeout: float) -> dict[str, Any]:
    payload = _request_json("/api/lab-assurance/refresh", method="POST", timeout=timeout)
    if not payload.get("accepted") or not isinstance(payload.get("state"), dict):
        raise HarnessError("The local refresh response did not contain an accepted Lab Assurance state.")
    return payload["state"]


def _public_report(session: dict[str, Any]) -> dict[str, Any]:
    baseline = dict(session.get("baseline", {}))
    baseline.pop("deviceIds", None)
    return {
        "schema": session.get("schema"),
        "createdAt": session.get("createdAt"),
        "updatedAt": session.get("updatedAt"),
        "baseline": baseline,
        "checkpoints": session.get("checkpoints", {}),
        "gate": "OPEN" if not session.get("complete") else "PASS",
        "note": (
            "A licensed virtual IOS/IOS-XE instance satisfies platform/multi-device acceptance only."
            if baseline.get("platformKind") == "licensed-virtual"
            else "Physical-hardware status reflects the operator's attestation; retain supporting lab notes separately."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify real v0.8 multi-device acceptance through the fixed loopback API.",
    )
    parser.add_argument("--session", type=Path, default=DEFAULT_SESSION)
    parser.add_argument("--timeout", type=float, default=420.0)
    subparsers = parser.add_subparsers(dest="command", required=True)

    baseline = subparsers.add_parser("baseline", help="Refresh and capture an onboarded two-device baseline.")
    baseline.add_argument("--secondary-id")
    baseline.add_argument("--platform-kind", required=True, choices=("physical", "licensed-virtual"))
    subparsers.add_parser("failure", help="Verify failure isolation after the operator makes the secondary unavailable.")
    subparsers.add_parser("recovery", help="Verify collection and graph recovery after the operator restores the secondary.")
    subparsers.add_parser("removal", help="Verify UI removal of the selected secondary.")
    readd = subparsers.add_parser("readd", help="Verify UI re-addition of a secondary under a new opaque ID.")
    readd.add_argument("--secondary-id")
    subparsers.add_parser("report", help="Print the privacy-safe accumulated report.")
    args = parser.parse_args(argv)

    if not 5 <= args.timeout <= 600:
        raise HarnessError("--timeout must be between 5 and 600 seconds.")
    if args.command == "report":
        print(json.dumps(_public_report(_load_session(args.session)), indent=2, sort_keys=True))
        return 0

    if args.command == "baseline":
        configured = _request_json("/api/lab-assurance/devices", timeout=args.timeout)
        state = _refresh(args.timeout)
        result = evaluate_baseline(
            configured,
            state,
            secondary_id=args.secondary_id,
            platform_kind=args.platform_kind,
        )
        session = {
            "schema": 1,
            "createdAt": _now(),
            "updatedAt": _now(),
            "baseline": result,
            "checkpoints": {},
            "complete": False,
        }
        _save_session(args.session, session)
        print(json.dumps(_public_report(session), indent=2, sort_keys=True))
        return 0

    session = _load_session(args.session)
    device_ids = session["baseline"]["deviceIds"]
    primary_id, secondary_id = device_ids["primary"], device_ids["secondary"]
    configured = _request_json("/api/lab-assurance/devices", timeout=args.timeout)

    if args.command == "failure":
        result = evaluate_failure(_refresh(args.timeout), primary_id=primary_id, secondary_id=secondary_id)
    elif args.command == "removal":
        configured_ids = {item.get("id") for item in configured.get("devices", [])}
        state = _refresh(args.timeout)
        absent = secondary_id not in configured_ids and _device_state(state, secondary_id) == "NOT_COLLECTED"
        result = {
            "checkedAt": _now(),
            "removal": _result(
                "PASS" if absent else "FAIL",
                "The UI-removed secondary is absent from configuration and refreshed graph state."
                if absent
                else "The selected secondary remains configured or present after refresh.",
            ),
        }
    elif args.command == "readd":
        new_primary, new_secondary = _configured_roles(configured, args.secondary_id)
        if new_primary != primary_id or new_secondary == secondary_id:
            result = {
                "checkedAt": _now(),
                "readdition": _result("FAIL", "Re-addition did not preserve the primary and issue a new secondary ID."),
            }
        else:
            device_ids["secondary"] = new_secondary
            state = _refresh(args.timeout)
            collected = _device_state(state, new_secondary) in {"CURRENT", "PARTIAL"}
            result = {
                "checkedAt": _now(),
                "readdition": _result(
                    "PASS" if collected else "FAIL",
                    "The UI re-addition created a new keyring-backed secondary and resumed collection."
                    if collected
                    else "The re-added secondary did not produce usable collection.",
                ),
            }
    else:
        state = _refresh(args.timeout)
        primary_id, secondary_id = device_ids["primary"], device_ids["secondary"]
        primary_current = _device_state(state, primary_id) == "CURRENT"
        secondary_current = _device_state(state, secondary_id) in {"CURRENT", "PARTIAL"}
        graph = _graph_checks(state, primary_id, secondary_id)
        baseline_graph = session["baseline"]["checks"]
        expected_reciprocal = baseline_graph["reciprocal_discovery"]["status"] == "PASS"
        reciprocal_recovered = not expected_reciprocal or graph["reciprocal_discovery"]["status"] == "PASS"
        passed = primary_current and secondary_current and reciprocal_recovered
        result = {
            "checkedAt": _now(),
            "recovery": _result(
                "PASS" if passed else "FAIL",
                "Independent collection recovered and the baseline reciprocal relationship returned when applicable."
                if passed
                else "Collection or the previously proved reciprocal relationship did not recover.",
            ),
            "graph": graph,
            "advancedCapabilities": _capability_matrix(state, device_ids),
        }

    session["checkpoints"][args.command] = result
    session["updatedAt"] = _now()
    required = {"failure", "recovery", "removal", "readd"}
    session["complete"] = required.issubset(session["checkpoints"]) and all(
        any(value.get("status") == "PASS" for value in checkpoint.values() if isinstance(value, dict))
        for name, checkpoint in session["checkpoints"].items()
        if name in required
    )
    _save_session(args.session, session)
    print(json.dumps(_public_report(session), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as exc:
        print(f"acceptance harness: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
