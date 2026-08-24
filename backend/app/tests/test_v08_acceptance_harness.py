from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "v08-multidevice-acceptance.py"
SPEC = importlib.util.spec_from_file_location("v08_multidevice_acceptance", SCRIPT)
assert SPEC and SPEC.loader
harness = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = harness
SPEC.loader.exec_module(harness)


def _evidence(kind: str, *, current: bool = True) -> dict:
    return {"id": "evidence-a", "kind": kind, "current": current}


def test_capability_acceptance_uses_all_five_operator_outcomes():
    evidence = {"evidence-a": _evidence("OBSERVED")}
    assert harness.capability_acceptance(
        {"state": "SUPPORTED", "configured": None, "observed": True, "evidenceIds": ["evidence-a"]},
        evidence,
    )["status"] == "PASS"

    assert harness.capability_acceptance(
        {"state": "SUPPORTED", "configured": False, "observed": True, "evidenceIds": ["evidence-a"]},
        evidence,
    )["status"] == "NOT CONFIGURED"

    evidence["evidence-a"] = _evidence("UNSUPPORTED")
    assert harness.capability_acceptance(
        {"state": "UNSUPPORTED", "configured": None, "observed": False, "evidenceIds": ["evidence-a"]},
        evidence,
    )["status"] == "NOT SUPPORTED"

    evidence["evidence-a"] = _evidence("EMPTY")
    assert harness.capability_acceptance(
        {"state": "UNKNOWN", "configured": False, "observed": False, "evidenceIds": ["evidence-a"]},
        evidence,
    )["status"] == "NOT CONFIGURED"

    evidence["evidence-a"] = _evidence("UNAVAILABLE")
    assert harness.capability_acceptance(
        {"state": "UNKNOWN", "configured": None, "observed": None, "evidenceIds": ["evidence-a"]},
        evidence,
    )["status"] == "NOT EXERCISED"

    evidence["evidence-a"] = _evidence("PARSER_FAILED", current=False)
    assert harness.capability_acceptance(
        {"state": "SUPPORTED", "configured": None, "observed": None, "evidenceIds": ["evidence-a"]},
        evidence,
    )["status"] == "FAIL"


def test_baseline_projection_is_keyring_bounded_and_public_report_is_private():
    configured = {
        "keyringAvailable": True,
        "devices": [
            {"id": "primary-opaque", "label": "PRIVATE PRIMARY", "primary": True, "storage": "legacy"},
            {"id": "lab-opaque", "label": "PRIVATE SECONDARY", "primary": False, "storage": "keyring"},
        ],
    }
    state = {
        "collectionState": "CURRENT",
        "summary": {"observedDevices": 2, "physicalEdges": 1, "logicalNetworks": 1},
        "devices": [
            {"id": "primary-opaque", "collectionState": "CURRENT"},
            {"id": "lab-opaque", "collectionState": "CURRENT"},
        ],
        "edges": [{
            "kind": "PHYSICAL",
            "fromNodeId": "primary-opaque",
            "toNodeId": "lab-opaque",
            "state": "PROVEN",
            "confidence": "CONFIRMED",
            "reciprocal": True,
        }],
        "capabilities": [],
        "evidence": [],
    }

    baseline = harness.evaluate_baseline(
        configured,
        state,
        secondary_id=None,
        platform_kind="licensed-virtual",
    )
    session = {
        "schema": 1,
        "createdAt": "fixture",
        "updatedAt": "fixture",
        "baseline": baseline,
        "checkpoints": {},
        "complete": False,
    }
    public = json.dumps(harness._public_report(session))

    assert baseline["checks"]["keyring_onboarding"]["status"] == "PASS"
    assert baseline["checks"]["independent_collection"]["status"] == "PASS"
    assert baseline["checks"]["reciprocal_discovery"]["status"] == "PASS"
    assert baseline["physicalHardwareAcceptance"] is False
    assert "primary-opaque" not in public
    assert "lab-opaque" not in public
    assert "PRIVATE PRIMARY" not in public
    assert "PRIVATE SECONDARY" not in public


def test_failure_checkpoint_requires_primary_current_and_secondary_failed():
    state = {
        "collectionState": "PARTIAL",
        "devices": [
            {"id": "primary-opaque", "collectionState": "CURRENT"},
            {"id": "lab-opaque", "collectionState": "FAILED"},
        ],
        "evidence": [
            {"deviceId": "primary-opaque", "current": True},
            {"deviceId": "lab-opaque", "current": False},
        ],
    }
    result = harness.evaluate_failure(
        state,
        primary_id="primary-opaque",
        secondary_id="lab-opaque",
    )
    assert result["failureIsolation"]["status"] == "PASS"

    state["devices"][0]["collectionState"] = "FAILED"
    assert harness.evaluate_failure(
        state,
        primary_id="primary-opaque",
        secondary_id="lab-opaque",
    )["failureIsolation"]["status"] == "FAIL"
