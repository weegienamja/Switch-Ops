from __future__ import annotations

import json
from pathlib import Path

import pytest

import backend.app.management_path as management_path
from backend.resilience_lab.catalog import (
    _validate_fixture_privacy,
    load_catalog,
    scenario_by_id,
)
from backend.resilience_lab.provider import ImmutableScenarioProvider, ScenarioOrderError
from backend.resilience_lab.runner import ResilienceScenarioRunner


PRIORITY_SCENARIOS = {
    "DHCP_SUBNET_CHANGE",
    "DHCP_RENEW_SAME_NETWORK",
    "ROUTE_REMOVED",
    "VPN_ROUTE_TAKEOVER",
    "WIFI_BECOMES_PREFERRED",
    "SSH_HALF_OPEN_SESSION",
    "SSH_AUTH_FAILURE",
    "SSH_HOST_KEY_CHANGE",
    "ENDPOINT_PORT_MOVE",
    "NEW_DEVICE_REPLACES_OLD_DEVICE",
    "SAME_MAC_VISIBLE_MULTIPLE_PORTS",
    "MX_API_UNAVAILABLE",
    "MERAKI_CURRENT_STATE_CONFLICTS_WITH_HISTORY",
    "BACKEND_RESTART",
    "RECOVERY_PLAN_STALE_AFTER_DHCP_CHANGE",
    "CONFLICTING_EVIDENCE_PRODUCES_INDETERMINATE",
    "RECOVERY_PLAN_STALE_AFTER_ROUTE_CHANGE",
}

# Scenarios that carry the environment back to a working state. Failure alone
# does not prove the product recovers its own understanding, so these each end
# in a restored phase.
RECOVERY_SCENARIOS = {
    "ROUTE_RESTORED",
    "ENDPOINT_DISCONNECT_RECONNECT_SAME_PORT",
    "ENDPOINT_DISCONNECT_RECONNECT_NEW_PORT",
    "DHCP_UNAVAILABLE_APIPA_RECOVERY",
    "DEVICE_RELOAD_RECONNECT",
    # Models the environment being restored by any means. SwitchOps has no
    # executor, so the scenario asserts it never claims to have performed one.
    "MANAGEMENT_PATH_RESTORED_AFTER_HOST_RETURNS",
}


# Scenarios produced by the privacy-safe exporter from a real incident and then
# committed as regressions. They carry no real addressing.
EXPORTED_SCENARIOS = {
    "REAL_DHCP_SUBNET_MOVE",
}


def test_catalogue_validates_and_contains_the_priority_scenarios():
    catalog = load_catalog()
    assert {item.id for item in catalog.scenarios} == (
        PRIORITY_SCENARIOS | RECOVERY_SCENARIOS | EXPORTED_SCENARIOS
    )
    assert all(len(item.phases) >= 2 for item in catalog.scenarios)


def test_recovery_scenarios_return_to_a_healthy_or_reconciled_final_phase():
    catalog = load_catalog()
    by_id = {item.id: item for item in catalog.scenarios}
    for scenario_id in RECOVERY_SCENARIOS:
        scenario = by_id[scenario_id]
        assert len(scenario.phases) >= 3, scenario_id
        final = scenario.phases[-1]
        diagnosis = final.expected.management_diagnosis
        # Either the management path is healthy again, or the scenario proves a
        # reconciled attachment. A recovery scenario that ends degraded with
        # nothing reconciled would not demonstrate recovery at all.
        recovered = diagnosis == "MANAGEMENT_PATH_HEALTHY"
        reconciled = final.expected.current_attachment is not None
        assert recovered or reconciled, f"{scenario_id} does not end recovered"
        assert "RECOVERY_EXECUTED" not in final.expected.must_claim


def test_complete_catalogue_passes_without_writes():
    runner = ResilienceScenarioRunner()
    results = [runner.run(scenario) for scenario in load_catalog().scenarios]
    assert [(item.scenario_id, item.status) for item in results] == [
        (scenario.id, "PASS") for scenario in load_catalog().scenarios
    ]
    assert all(
        phase.actual.writes_performed == 0
        for result in results
        for phase in result.phases
    )


def test_same_scenario_produces_byte_equivalent_deterministic_result():
    scenario = scenario_by_id("DHCP_SUBNET_CHANGE")
    first = ResilienceScenarioRunner().run(scenario)
    second = ResilienceScenarioRunner().run(scenario)
    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)
    assert first.generated_at == scenario.phases[-1].at


def test_provider_enforces_declared_transition_order_and_returns_copies():
    scenario = scenario_by_id("ENDPOINT_PORT_MOVE")
    provider = ImmutableScenarioProvider(scenario)
    with pytest.raises(ScenarioOrderError, match="Expected phase"):
        provider.advance(scenario.phases[1].id)
    first = provider.advance(scenario.phases[0].id)
    first.transition = "mutated caller copy"
    second = provider.advance(scenario.phases[1].id)
    assert second.at > scenario.phases[0].at
    assert provider.complete
    provider.reset()
    assert provider.advance(scenario.phases[0].id).transition != "mutated caller copy"


def test_runner_invokes_production_management_reasoning(monkeypatch):
    called = 0
    production = management_path.diagnose_management_path

    def recording_reasoner(**kwargs):
        nonlocal called
        called += 1
        return production(**kwargs)

    monkeypatch.setattr(management_path, "diagnose_management_path", recording_reasoner)
    result = ResilienceScenarioRunner().run(scenario_by_id("DHCP_SUBNET_CHANGE"))
    assert result.status == "PASS"
    assert called == 2
    assert "backend.app.management_path.diagnose_management_path" in result.reasoning_modules


def test_production_runtime_has_no_resilience_lab_import_boundary():
    app_root = Path(__file__).parents[1]
    runtime_files = [
        path
        for path in app_root.rglob("*.py")
        if "tests" not in path.parts and "__pycache__" not in path.parts
    ]
    imports = [
        path.name
        for path in runtime_files
        if "resilience_lab" in path.read_text(encoding="utf-8")
    ]
    assert imports == []


@pytest.mark.parametrize(
    "text",
    [
        # A trailing sentence period once defeated the address lookahead, so a
        # private address at the end of a description was never examined.
        "The host moved to 192.168.1.10.",
        "The host moved to 192.168.1.10",
        "The gateway (10.8.0.4) answered.",
        "Reached 8.8.8.8.",
        "Source 172.16.4.5, gateway 172.16.4.1.",
    ],
)
def test_privacy_validator_detects_addresses_in_any_sentence_position(text):
    with pytest.raises(ValueError):
        _validate_fixture_privacy({"transition": text})


def test_privacy_validator_still_allows_documentation_addresses():
    _validate_fixture_privacy({"transition": "Source 192.0.2.95 via 192.0.2.1."})
    _validate_fixture_privacy({"transition": "Self-assigned 169.254.10.20."})


@pytest.mark.parametrize(
    "private_value",
    ["10.8.0.4", "172.16.4.5", "192.168.1.10"],
)
def test_catalogue_rejects_private_lab_addresses(tmp_path, private_value):
    payload = {
        "schemaVersion": 1,
        "scenarios": [
            {
                "id": "PRIVATE_FIXTURE",
                "description": "Must be rejected.",
                "purpose": "Prove private addresses cannot enter fixtures.",
                "phases": [
                    {
                        "id": "one",
                        "at": "2026-08-26T00:00:00Z",
                        "transition": "Invalid private fixture.",
                        "evidence": {"management": {"target": private_value}},
                        "expected": {},
                    }
                ],
            }
        ],
    }
    fixture = tmp_path / "private.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="RFC documentation"):
        load_catalog(fixture)


def test_historical_timestamps_survive_backend_reconstruction():
    scenario = scenario_by_id("BACKEND_RESTART")
    result = ResilienceScenarioRunner().run(scenario)
    assert result.phases[-1].actual.last_known_good_observed_at == scenario.phases[0].at
    assert result.phases[-1].actual.last_known_good_observed_at < result.phases[-1].at


def test_stale_evidence_remains_stale():
    scenario = scenario_by_id("MX_API_UNAVAILABLE").model_copy(deep=True)
    scenario.id = "STALE_MERAKI_TEST"
    final = scenario.phases[-1]
    assert final.evidence.meraki is not None
    final.evidence.meraki.state = "partial"
    final.evidence.meraki.freshness = "stale"
    final.expected.meraki_state = "partial"
    final.expected.meraki_freshness = "stale"
    final.expected.must_claim = []
    result = ResilienceScenarioRunner().run(scenario)
    assert result.status == "PASS"
    assert result.phases[-1].actual.meraki_freshness == "stale"


def test_endpoint_move_retains_identity_and_ordered_attachment_history():
    result = ResilienceScenarioRunner().run(scenario_by_id("ENDPOINT_PORT_MOVE"))
    moved = result.phases[-1].actual
    assert result.status == "PASS"
    assert moved.topology_transitions == ["ENDPOINT_MOVED"]
    assert moved.identity_retained is True
    assert (moved.previous_attachment, moved.current_attachment) == ("Gi0/2", "Gi0/5")
    assert moved.duplicate_entity_ids == 0


def test_ambiguous_multiport_mac_never_claims_move_or_replacement():
    result = ResilienceScenarioRunner().run(
        scenario_by_id("SAME_MAC_VISIBLE_MULTIPLE_PORTS")
    )
    actual = result.phases[-1].actual
    assert actual.topology_transitions == ["ATTACHMENT_CONFLICT"]
    assert "INDETERMINATE" in actual.claims
    assert "ENDPOINT_MOVED" not in actual.claims
    assert "DEVICE_REPLACED" not in actual.claims


def test_forbidden_claim_failure_names_the_failed_expectation():
    scenario = scenario_by_id("DHCP_SUBNET_CHANGE").model_copy(deep=True)
    scenario.id = "FORBIDDEN_CLAIM_TEST"
    scenario.phases[-1].expected.must_not_claim.append("HOST_NETWORK_CHANGED")
    result = ResilienceScenarioRunner().run(scenario)
    failures = [
        assertion
        for assertion in result.phases[-1].assertions
        if not assertion.passed
    ]
    assert result.status == "FAIL"
    assert [(item.expectation, item.actual) for item in failures] == [
        (
            "MUST NOT CLAIM HOST_NETWORK_CHANGED",
            ", ".join(result.phases[-1].actual.claims),
        )
    ]


def test_unexpected_write_fails_unsafe_action_prevention_dimension():
    result = ResilienceScenarioRunner(write_probe=lambda _scenario, _phase: 1).run(
        scenario_by_id("DHCP_RENEW_SAME_NETWORK")
    )
    failures = [
        assertion
        for phase in result.phases
        for assertion in phase.assertions
        if not assertion.passed
    ]
    assert result.status == "FAIL"
    assert failures
    assert {item.dimension for item in failures} == {"unsafe-action-prevention"}
    assert {item.expectation for item in failures} == {"writes performed=0"}


def test_missing_or_conflicting_evidence_is_indeterminate():
    result = ResilienceScenarioRunner().run(
        scenario_by_id("CONFLICTING_EVIDENCE_PRODUCES_INDETERMINATE")
    )
    actual = result.phases[-1].actual
    assert actual.management_diagnosis == "INDETERMINATE"
    assert actual.confidence == "INDETERMINATE"
    assert "DEVICE_OFFLINE" not in actual.claims


# --- do the recovery assertions actually bite? -----------------------------
#
# A recovery scenario that only asserted "not failed" would pass even if the
# product never recovered. These mutate a passing scenario and require it to
# fail, which is the only way to know the assertions are load-bearing.

def _scenario(scenario_id: str):
    return scenario_by_id(scenario_id)


def test_route_restored_fails_if_the_path_does_not_actually_recover():
    scenario = _scenario("ROUTE_RESTORED")
    degraded = scenario.phases[1].evidence.model_copy(deep=True)
    scenario.phases[-1].evidence = degraded
    result = ResilienceScenarioRunner().run(scenario)
    assert result.status == "FAIL"
    assert result.phases[-1].status == "FAIL"


def test_route_restored_fails_if_a_stale_plan_still_binds_after_recovery():
    # Binding validity is asserted False on the final phase. Flipping the
    # expectation must fail, proving the plan really is re-bound on recovery.
    scenario = _scenario("ROUTE_RESTORED")
    scenario.phases[-1].expected.binding_valid = True
    result = ResilienceScenarioRunner().run(scenario)
    assert result.status == "FAIL"


def test_apipa_recovery_fails_if_the_lease_never_returns():
    scenario = _scenario("DHCP_UNAVAILABLE_APIPA_RECOVERY")
    scenario.phases[-1].evidence = scenario.phases[1].evidence.model_copy(deep=True)
    result = ResilienceScenarioRunner().run(scenario)
    assert result.status == "FAIL"


def test_device_reload_fails_if_the_device_never_answers_again():
    scenario = _scenario("DEVICE_RELOAD_RECONNECT")
    scenario.phases[-1].evidence.management = scenario.phases[1].evidence.management
    result = ResilienceScenarioRunner().run(scenario)
    assert result.status == "FAIL"


def test_same_port_reconnect_fails_if_the_endpoint_lands_somewhere_else():
    # Proves the final attachment assertion is real rather than decorative.
    scenario = _scenario("ENDPOINT_DISCONNECT_RECONNECT_SAME_PORT")
    topology = scenario.phases[-1].evidence.topology
    assert topology is not None
    for entry in topology.macs:
        entry.port = "Gi0/5"
    result = ResilienceScenarioRunner().run(scenario)
    assert result.status == "FAIL"


def test_new_port_reconnect_fails_if_identity_is_not_retained():
    # Identity must survive an attachment change. If the runner reported a
    # replacement instead, this expectation has to fail.
    scenario = _scenario("ENDPOINT_DISCONNECT_RECONNECT_NEW_PORT")
    topology = scenario.phases[-1].evidence.topology
    assert topology is not None
    for entry in topology.macs:
        entry.mac = "0000.5e00.53bb"
    for adapter in topology.adapters:
        adapter.mac = "0000.5e00.53bb"
    result = ResilienceScenarioRunner().run(scenario)
    assert result.status == "FAIL"


def test_recovery_scenarios_are_detected_from_their_phases():
    from backend.resilience_lab.__main__ import _is_recovery

    catalog = {item.id: item for item in load_catalog().scenarios}
    for scenario_id in RECOVERY_SCENARIOS:
        assert _is_recovery(catalog[scenario_id]), scenario_id
    # A two-phase failure scenario is not a recovery arc.
    assert not _is_recovery(catalog["ROUTE_REMOVED"])


def test_summary_output_names_the_failing_dimension():
    from backend.resilience_lab.__main__ import _summarize

    scenario = _scenario("ROUTE_RESTORED")
    scenario.phases[-1].evidence = scenario.phases[1].evidence.model_copy(deep=True)
    result = ResilienceScenarioRunner().run(scenario)
    summary = _summarize(result)
    assert "FAIL" in summary
    assert "ROUTE_RESTORED" in summary
    assert "phase=" in summary
    assert "dimension=" in summary
    assert "expected:" in summary and "actual:" in summary


def test_no_scenario_claims_a_recovery_was_executed():
    # The Lab may model execution semantics, but the product is planning-only.
    # A fixture asserting RECOVERY_EXECUTED would quietly legitimise a claim
    # SwitchOps has no mechanism to make.
    for scenario in load_catalog().scenarios:
        for phase in scenario.phases:
            assert "RECOVERY_EXECUTED" not in phase.expected.must_claim
            assert "TEMPORARY_ADDRESS_APPLIED" not in phase.expected.must_claim
            assert "ROLLBACK_COMPLETED" not in phase.expected.must_claim


def test_the_restoration_scenario_invalidates_the_plan_built_while_degraded():
    scenario = scenario_by_id("MANAGEMENT_PATH_RESTORED_AFTER_HOST_RETURNS")
    final = scenario.phases[-1].expected
    assert final.management_diagnosis == "MANAGEMENT_PATH_HEALTHY"
    assert final.binding_valid is False
    assert "TEMPORARY_ADDRESS_APPLIED" in final.must_not_claim
