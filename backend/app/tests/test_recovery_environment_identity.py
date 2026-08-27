"""Correlating a provisioned environment with the Windows adapter it became.

The original model stored a display name as identity and could not answer the
only question that matters: is *this* live adapter the one we created? The
VirtualBox host-only interface name is the Windows InterfaceDescription, never
the InterfaceAlias, so a record naming "VirtualBox Host-Only Ethernet Adapter #2"
never matched an operator typing "Ethernet 3".

Identity is now the interface GUID, which VirtualBox and Windows both report for
the same adapter. Everything else -- alias, description, subnet, ifIndex -- is an
observation that can change or be coincidentally shared.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.recovery_lab.environment import (
    OWNED_CREATOR,
    DisposableEnvironment,
    EnvironmentRegistry,
    WindowsAdapter,
    assess_test_authority,
    normalise_guid,
    reconcile_environment,
)

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
VBOX_NAME = "VirtualBox Host-Only Ethernet Adapter #2"
GUID = "11111111-2222-4333-8444-555555555555"
OTHER_GUID = "66666666-7777-4888-8999-aaaaaaaaaaaa"
PRODUCTION_GUID = "cccccccc-dddd-4eee-8fff-000000000000"


def _registry(tmp_path: Path) -> EnvironmentRegistry:
    return EnvironmentRegistry(tmp_path / "environments.json")


def _environment(**overrides) -> DisposableEnvironment:
    payload = {
        "environment_id": "recovery-env-abc123",
        "hostonly_name": VBOX_NAME,
        "network_cidr": "192.168.57.0/24",
        "created_at": NOW.isoformat().replace("+00:00", "Z"),
        "created_by": OWNED_CREATOR,
    }
    payload.update(overrides)
    return DisposableEnvironment(**payload)


def _adapter(**overrides) -> WindowsAdapter:
    payload = {
        "interface_guid": GUID,
        "alias": "Ethernet 3",
        "description": VBOX_NAME,
        "interface_index": 58,
    }
    payload.update(overrides)
    return WindowsAdapter(**payload)


PRODUCTION = WindowsAdapter(
    interface_guid=PRODUCTION_GUID,
    alias="Ethernet",
    description="Intel(R) Ethernet Controller (3) I225-V",
    interface_index=16,
)


# --- GUID normalisation ----------------------------------------------------

def test_virtualbox_and_windows_guid_spellings_compare_equal():
    # VirtualBox prints bare lowercase; Windows prints braced uppercase.
    assert normalise_guid(GUID) == normalise_guid("{11111111-2222-4333-8444-555555555555}")


@pytest.mark.parametrize("value", [None, "", "not-a-guid", "1234", "{}"])
def test_unusable_guids_normalise_to_none(value):
    assert normalise_guid(value) is None


# --- schema migration ------------------------------------------------------

def test_a_v1_record_migrates_without_inventing_a_windows_identity(tmp_path):
    # The real record on this machine: a VirtualBox name in adapter_alias and
    # no resolved Windows identity at all.
    path = tmp_path / "environments.json"
    path.write_text(
        json.dumps(
            [
                {
                    "environment_id": "recovery-env-000000000001",
                    "adapter_alias": VBOX_NAME,
                    "hostonly_name": VBOX_NAME,
                    "network_cidr": "192.168.57.0/24",
                    "created_at": "2026-08-26T23:51:23.410619Z",
                    "created_by": "backend.recovery_lab",
                    "interface_index": None,
                    "notes": [],
                }
            ]
        ),
        encoding="utf-8",
    )
    migrated = EnvironmentRegistry(path).all()[0]
    assert migrated.schema_version == 2
    assert migrated.hostonly_name == VBOX_NAME
    # No Windows identity is fabricated from the name.
    assert migrated.interface_guid is None
    assert migrated.observed_alias is None
    assert migrated.has_stable_identity is False
    assert any("reconciliation" in note for note in migrated.notes)


# --- reconciliation --------------------------------------------------------

def test_an_unresolved_record_is_correlated_through_virtualbox(tmp_path):
    # The ownership chain: we created interface X; VirtualBox says X is GUID G;
    # Windows says G is this adapter.
    result = reconcile_environment(
        _environment(),
        hostonly_guids={VBOX_NAME: GUID},
        adapters=[PRODUCTION, _adapter()],
        now=NOW,
    )
    assert result.outcome == "RECONCILED"
    assert result.environment is not None
    assert result.environment.interface_guid == GUID
    assert result.environment.observed_alias == "Ethernet 3"
    assert result.environment.interface_index == 58
    joined = " ".join(result.evidence)
    assert "VirtualBox reports" in joined and "same GUID" in joined


def test_reconciliation_works_when_the_alias_differs_from_the_description():
    # This is the defect: alias "Ethernet 3", description "VirtualBox ... #2".
    result = reconcile_environment(
        _environment(),
        hostonly_guids={VBOX_NAME: GUID},
        adapters=[_adapter(alias="Ethernet 3", description=VBOX_NAME)],
        now=NOW,
    )
    assert result.outcome == "RECONCILED"
    assert result.environment.observed_alias == "Ethernet 3"


def test_an_already_resolved_record_is_reported_as_such():
    resolved = _environment(
        interface_guid=GUID, observed_alias="Ethernet 3", interface_index=58
    )
    result = reconcile_environment(
        resolved, hostonly_guids={VBOX_NAME: GUID}, adapters=[_adapter()], now=NOW
    )
    assert result.outcome == "ALREADY_RESOLVED"


def test_a_moved_interface_index_still_correlates_on_stable_identity():
    resolved = _environment(
        interface_guid=GUID, observed_alias="Ethernet 3", interface_index=58
    )
    result = reconcile_environment(
        resolved,
        hostonly_guids={VBOX_NAME: GUID},
        adapters=[_adapter(interface_index=99, alias="Ethernet 7")],
        now=NOW,
    )
    assert result.outcome == "RECONCILED"
    assert result.environment.interface_index == 99
    assert result.environment.observed_alias == "Ethernet 7"


def test_a_vanished_virtualbox_interface_fails_closed():
    result = reconcile_environment(
        _environment(), hostonly_guids={}, adapters=[_adapter()], now=NOW
    )
    assert result.outcome == "ENVIRONMENT_ADAPTER_CHANGED"
    assert result.environment is None


def test_a_guid_with_no_live_adapter_fails_closed():
    result = reconcile_environment(
        _environment(), hostonly_guids={VBOX_NAME: GUID}, adapters=[PRODUCTION], now=NOW
    )
    assert result.outcome == "ENVIRONMENT_ADAPTER_CHANGED"


def test_duplicate_guids_are_ambiguous_rather_than_resolved():
    result = reconcile_environment(
        _environment(),
        hostonly_guids={VBOX_NAME: GUID},
        adapters=[_adapter(alias="Ethernet 3"), _adapter(alias="Ethernet 4")],
        now=NOW,
    )
    assert result.outcome == "ENVIRONMENT_IDENTITY_AMBIGUOUS"
    assert result.environment is None


def test_a_same_named_adapter_with_a_different_guid_is_not_claimed():
    # Description matches, identity does not. Ownership is not a name match.
    result = reconcile_environment(
        _environment(),
        hostonly_guids={VBOX_NAME: GUID},
        adapters=[_adapter(interface_guid=OTHER_GUID, description=VBOX_NAME)],
        now=NOW,
    )
    assert result.outcome == "ENVIRONMENT_ADAPTER_CHANGED"


# --- authority -------------------------------------------------------------

def _authority(registry, adapter, experiment_type="DHCP_COEXISTENCE", now=NOW,
               hostonly_guids=None):
    return assess_test_authority(
        experiment_type=experiment_type,
        adapter=adapter,
        registry=registry,
        now=now,
        hostonly_guids={VBOX_NAME: GUID} if hostonly_guids is None else hostonly_guids,
    )


def test_a_reconciled_environment_grants_dhcp_authority(tmp_path):
    registry = _registry(tmp_path)
    registry.record(_environment(interface_guid=GUID, observed_alias="Ethernet 3",
                                 interface_index=58))
    result = _authority(registry, _adapter())
    assert result.granted is True
    assert result.provenance == "DISPOSABLE_DHCP_ENVIRONMENT"
    assert result.environment_id == "recovery-env-abc123"


def test_live_duplicate_guids_cannot_grant_experiment_authority(tmp_path):
    registry = _registry(tmp_path)
    registry.record(
        _environment(
            interface_guid=GUID,
            observed_alias="Ethernet 3",
            interface_index=58,
        )
    )
    adapters = [_adapter(), _adapter(alias="Ethernet 4", interface_index=59)]
    result = assess_test_authority(
        experiment_type="DHCP_COEXISTENCE",
        adapter=adapters[0],
        registry=registry,
        now=NOW,
        hostonly_guids={VBOX_NAME: GUID},
        live_adapters=adapters,
    )
    assert result.granted is False
    assert "ENVIRONMENT_IDENTITY_AMBIGUOUS" in result.blockers


def test_production_ethernet_is_never_owned(tmp_path):
    registry = _registry(tmp_path)
    registry.record(_environment(interface_guid=GUID, observed_alias="Ethernet 3"))
    result = _authority(registry, PRODUCTION)
    assert result.granted is False
    assert "ENVIRONMENT_NOT_OWNED" in result.blockers


def test_an_arbitrary_dhcp_adapter_is_never_owned(tmp_path):
    registry = _registry(tmp_path)
    registry.record(_environment(interface_guid=GUID, observed_alias="Ethernet 3"))
    stranger = WindowsAdapter(
        interface_guid=OTHER_GUID, alias="Ethernet 9",
        description="Some Other Adapter", interface_index=42,
    )
    assert _authority(registry, stranger).granted is False


def test_an_unresolved_record_blocks_rather_than_guessing(tmp_path):
    # A record exists but has never been correlated. That is a reason to
    # reconcile, not a reason to proceed.
    registry = _registry(tmp_path)
    registry.record(_environment())
    result = _authority(registry, _adapter())
    assert result.granted is False
    assert "ENVIRONMENT_IDENTITY_NOT_RESOLVED" in result.blockers
    assert "reconcile" in " ".join(result.evidence)


def test_an_empty_registry_blocks(tmp_path):
    result = _authority(_registry(tmp_path), _adapter())
    assert result.granted is False
    assert "ENVIRONMENT_NOT_OWNED" in result.blockers


def test_a_missing_adapter_blocks(tmp_path):
    result = _authority(_registry(tmp_path), None)
    assert result.granted is False
    assert "ENVIRONMENT_IDENTITY_NOT_RESOLVED" in result.blockers


def test_an_adapter_without_a_usable_guid_blocks(tmp_path):
    registry = _registry(tmp_path)
    registry.record(_environment(interface_guid=GUID))
    result = _authority(registry, _adapter(interface_guid="not-a-guid"))
    assert result.granted is False
    assert "ENVIRONMENT_IDENTITY_NOT_RESOLVED" in result.blockers


def test_a_stale_environment_is_refused(tmp_path):
    registry = _registry(tmp_path)
    old = (NOW - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    registry.record(_environment(interface_guid=GUID, created_at=old))
    result = _authority(registry, _adapter())
    assert result.granted is False
    assert "ENVIRONMENT_RECORD_STALE" in result.blockers


def test_disposable_provenance_does_not_authorise_other_experiments(tmp_path):
    registry = _registry(tmp_path)
    registry.record(_environment(interface_guid=GUID, observed_alias="Ethernet 3"))
    result = _authority(registry, _adapter(), experiment_type="EPHEMERAL_PRIMITIVE")
    assert result.granted is False
    assert "EXPERIMENT_TYPE_NOT_AUTHORISED" in result.blockers


def test_a_moved_index_is_noted_but_does_not_refuse(tmp_path):
    registry = _registry(tmp_path)
    registry.record(_environment(interface_guid=GUID, interface_index=58))
    result = _authority(registry, _adapter(interface_index=99))
    assert result.granted is True
    assert "ifIndex moved" in " ".join(result.evidence)


# --- ownership is never inferred from supporting evidence ------------------

@pytest.mark.parametrize(
    "adapter",
    [
        # Looks disposable in every way except identity.
        WindowsAdapter(interface_guid=OTHER_GUID, alias="Ethernet 3",
                       description=VBOX_NAME, interface_index=58),
        WindowsAdapter(interface_guid=OTHER_GUID, alias="Ethernet 4",
                       description="VirtualBox Host-Only Ethernet Adapter #9",
                       interface_index=60),
    ],
)
def test_name_and_description_never_grant_ownership(tmp_path, adapter):
    registry = _registry(tmp_path)
    registry.record(_environment(interface_guid=GUID, observed_alias="Ethernet 3"))
    assert _authority(registry, adapter).granted is False


def test_registry_lookup_by_guid_ignores_spelling(tmp_path):
    registry = _registry(tmp_path)
    registry.record(_environment(interface_guid=GUID))
    assert registry.find_by_guid("{11111111-2222-4333-8444-555555555555}") is not None
    assert registry.find_by_guid(OTHER_GUID) is None
    assert registry.find_by_guid(None) is None


# --- CLI rendering ---------------------------------------------------------
#
# The identity refactor renamed a field and the `environments` command still
# referenced the old one, which only surfaced when a human ran it. These
# exercise the render paths so a field rename cannot pass the suite again.

def test_environments_command_renders_a_resolved_record(tmp_path, capsys):
    import argparse

    from backend.recovery_lab.__main__ import command_environments

    registry = _registry(tmp_path)
    registry.record(
        _environment(interface_guid=GUID, observed_alias="Ethernet 3", interface_index=58)
    )
    assert command_environments(
        argparse.Namespace(registry=str(registry.path))
    ) == 0
    output = capsys.readouterr().out
    assert "recovery-env-abc123" in output
    assert GUID in output
    assert "Ethernet 3" in output


def test_environments_command_renders_an_unresolved_record(tmp_path, capsys):
    import argparse

    from backend.recovery_lab.__main__ import command_environments

    registry = _registry(tmp_path)
    registry.record(_environment())
    command_environments(argparse.Namespace(registry=str(registry.path)))
    output = capsys.readouterr().out
    # It must say so rather than printing a blank identity.
    assert "UNRESOLVED" in output
    assert "reconcile" in output


def test_environments_command_handles_an_empty_registry(tmp_path, capsys):
    import argparse

    from backend.recovery_lab.__main__ import command_environments

    registry = _registry(tmp_path)
    assert command_environments(argparse.Namespace(registry=str(registry.path))) == 0
    assert "No disposable environments" in capsys.readouterr().out


def test_reconciliation_is_contemporaneous_even_for_a_resolved_record():
    # A record that once resolved does not stay authoritative: the VirtualBox
    # interface may have been removed, and a reused display name is not ours.
    resolved = _environment(
        interface_guid=GUID, observed_alias="Ethernet 3", interface_index=58
    )
    result = reconcile_environment(
        resolved, hostonly_guids={}, adapters=[_adapter()], now=NOW
    )
    assert result.outcome == "ENVIRONMENT_ADAPTER_CHANGED"
    assert result.environment is None


def test_a_reused_virtualbox_name_pointing_at_a_new_guid_is_refused():
    resolved = _environment(interface_guid=GUID, observed_alias="Ethernet 3")
    result = reconcile_environment(
        resolved,
        hostonly_guids={VBOX_NAME: OTHER_GUID},
        adapters=[_adapter(interface_guid=OTHER_GUID)],
        now=NOW,
    )
    assert result.outcome == "ENVIRONMENT_ADAPTER_CHANGED"


def test_authority_requires_the_live_virtualbox_mapping(tmp_path):
    # Without a live mapping the chain cannot be re-proven, so authority fails
    # closed rather than trusting the stored GUID.
    registry = _registry(tmp_path)
    registry.record(_environment(interface_guid=GUID, observed_alias="Ethernet 3"))
    result = _authority(registry, _adapter(), hostonly_guids={})
    assert result.granted is False
    assert "ENVIRONMENT_ADAPTER_CHANGED" in result.blockers


def test_a_record_without_harness_provenance_is_not_owned(tmp_path):
    # A hand-written or migrated record with no created_by marker must not
    # grant authority just because its GUID happens to match.
    registry = _registry(tmp_path)
    registry.record(
        _environment(interface_guid=GUID, observed_alias="Ethernet 3", created_by="")
    )
    result = _authority(registry, _adapter())
    assert result.granted is False
