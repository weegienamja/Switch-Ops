"""Provisioning and teardown of the disposable DHCP environment.

VBoxManage is injected so the whole lifecycle can be exercised without creating
adapters. The tests that matter most are the refusals: this tool must never
build a network that shadows real addressing, and must never remove an adapter
it did not create.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.recovery_lab.environment import DisposableEnvironment, EnvironmentRegistry, now_iso
from backend.recovery_lab.provision import (
    DEFAULT_NETWORK,
    LeaseObservation,
    network_is_permitted,
    parse_created_interface,
    provision_dhcp_environment,
    teardown_dhcp_environment,
)

CREATED = "VirtualBox Host-Only Ethernet Adapter #2"
CREATE_OUTPUT = f"Interface '{CREATED}' was successfully created\n"


def _registry(tmp_path: Path) -> EnvironmentRegistry:
    return EnvironmentRegistry(tmp_path / "environments.json")


class FakeVBox:
    """Records the commands issued and returns scripted results."""

    def __init__(self, failures: dict[str, int] | None = None):
        self.calls: list[list[str]] = []
        self.failures = failures or {}

    def __call__(self, args):
        self.calls.append(list(args))
        joined = " ".join(args)
        for marker, code in self.failures.items():
            if marker in joined:
                return code, f"failed: {marker}"
        if "hostonlyif create" in joined:
            return 0, CREATE_OUTPUT
        return 0, ""


GUID = "11111111-2222-4333-8444-555555555555"
WINDOWS_ALIAS = "Ethernet 3"


def _lease(address="192.168.57.101", *, origin=("DHCP", "DHCP"),
           dad="PREFERRED", lifetime=3600, index=20, alias=WINDOWS_ALIAS):
    return LeaseObservation(
        interface_index=index, address=address, alias=alias,
        prefix_origin=origin[0], suffix_origin=origin[1],
        dad_state=dad, valid_lifetime=lifetime,
    )


def _provision(tmp_path, *, vbox=None, elevated=True, lease=..., **kwargs):
    if lease is ...:
        lease = _lease()
    registry = _registry(tmp_path)
    return registry, provision_dhcp_environment(
        registry=registry,
        elevated=elevated,
        vboxmanage=Path(__file__),        # exists, so the check passes
        run=vbox or FakeVBox(),
        lease_probe=(lambda _guid: lease) if lease is not None else None,
        hostonly_guids={CREATED: GUID},
        sleep=lambda _s: None,
        lease_timeout=4.0,
        **kwargs,
    )


# --- refusals --------------------------------------------------------------

@pytest.mark.parametrize(
    "network",
    ["192.168.254.0/24", "192.168.0.0/24", "192.168.56.0/24"],
)
def test_networks_that_shadow_real_addressing_are_refused(network):
    # Production LAN, the Catalyst management prefix, and the pre-existing
    # host-only segment respectively.
    assert network_is_permitted(network) is False


def test_the_default_test_network_is_permitted():
    assert network_is_permitted(DEFAULT_NETWORK) is True


def test_a_forbidden_network_is_refused_before_anything_is_created(tmp_path):
    vbox = FakeVBox()
    _, result = _provision(tmp_path, vbox=vbox, network_cidr="192.168.0.0/24")
    assert result.outcome == "NETWORK_FORBIDDEN"
    assert vbox.calls == []


def test_provisioning_without_elevation_creates_nothing(tmp_path):
    vbox = FakeVBox()
    _, result = _provision(tmp_path, vbox=vbox, elevated=False)
    assert result.outcome == "ELEVATION_UNAVAILABLE"
    assert vbox.calls == []


# --- the happy path --------------------------------------------------------

def test_provisioning_creates_records_and_leases(tmp_path):
    vbox = FakeVBox()
    registry, result = _provision(tmp_path, vbox=vbox)
    assert result.outcome == "PROVISIONED"
    assert result.environment is not None
    assert result.environment.hostonly_name == CREATED
    assert result.environment.interface_guid == GUID
    assert result.environment.observed_alias == WINDOWS_ALIAS
    assert result.environment.interface_index == 20

    issued = [" ".join(call) for call in vbox.calls]
    assert any("hostonlyif create" in call for call in issued)
    assert any("dhcpserver add" in call and "--enable" in call for call in issued)
    assert any("ipconfig" in call and "--dhcp" in call for call in issued)

    # The registry is what makes the adapter recognisable later.
    stored = registry.find_by_hostonly_name(CREATED)
    assert stored is not None and stored.interface_index == 20


def test_the_adapter_is_recorded_before_it_is_configured(tmp_path):
    # If DHCP setup fails, teardown must still know the adapter is ours.
    vbox = FakeVBox(failures={"dhcpserver add": 1})
    registry, result = _provision(tmp_path, vbox=vbox)
    assert result.outcome == "DHCP_SERVER_FAILED"
    assert registry.find_by_hostonly_name(CREATED) is not None


def test_an_apipa_fallback_is_reported_rather_than_accepted(tmp_path):
    # An adapter that never got a lease cannot answer the coexistence question.
    _, result = _provision(tmp_path, lease=_lease("169.254.10.5", origin=("WELL_KNOWN", "LINK_LAYER_ADDRESS"), lifetime=0xFFFFFFFF))
    assert result.outcome == "DHCP_LEASE_NOT_OBTAINED"
    assert "VBoxNetDHCP" in result.detail


def test_no_lease_at_all_is_reported(tmp_path):
    _, result = _provision(tmp_path, lease=None)
    assert result.outcome == "DHCP_LEASE_NOT_OBTAINED"


def test_interface_name_is_parsed_from_vboxmanage_output():
    assert parse_created_interface(CREATE_OUTPUT) == CREATED
    assert parse_created_interface("something unexpected") is None


def test_an_unparseable_create_is_a_failure_not_a_silent_success(tmp_path):
    vbox = FakeVBox()
    vbox.failures = {}

    def weird(args):
        vbox.calls.append(list(args))
        return 0, "created something, who knows what"

    registry = _registry(tmp_path)
    result = provision_dhcp_environment(
        registry=registry, elevated=True, vboxmanage=Path(__file__), run=weird,
        lease_probe=lambda _g: _lease(),
        sleep=lambda _s: None,
    )
    assert result.outcome == "ADAPTER_CREATE_FAILED"
    assert registry.all() == []


# --- teardown --------------------------------------------------------------

def test_teardown_removes_only_what_we_created(tmp_path):
    vbox = FakeVBox()
    registry, _ = _provision(tmp_path, vbox=vbox)
    result = teardown_dhcp_environment(
        registry=registry, adapter_alias=CREATED, elevated=True,
        vboxmanage=Path(__file__), run=vbox, hostonly_guids={CREATED: GUID},
    )
    assert result.outcome == "REMOVED"
    assert registry.find_by_hostonly_name(CREATED) is None
    issued = [" ".join(call) for call in vbox.calls]
    assert any("hostonlyif remove" in call for call in issued)


def test_the_preexisting_host_only_adapter_is_never_removed(tmp_path):
    # It predates this work and belongs to the operator.
    vbox = FakeVBox()
    registry = _registry(tmp_path)
    result = teardown_dhcp_environment(
        registry=registry,
        adapter_alias="VirtualBox Host-Only Ethernet Adapter",
        elevated=True, vboxmanage=Path(__file__), run=vbox,
    )
    assert result.outcome == "NOT_OURS"
    assert vbox.calls == []


def test_production_ethernet_is_never_removed(tmp_path):
    vbox = FakeVBox()
    result = teardown_dhcp_environment(
        registry=_registry(tmp_path), adapter_alias="Ethernet", elevated=True,
        vboxmanage=Path(__file__), run=vbox,
    )
    assert result.outcome == "NOT_OURS"
    assert vbox.calls == []


def test_teardown_without_elevation_changes_nothing(tmp_path):
    vbox = FakeVBox()
    registry, _ = _provision(tmp_path, vbox=FakeVBox())
    before = len(vbox.calls)
    result = teardown_dhcp_environment(
        registry=registry, adapter_alias=CREATED, elevated=False,
        vboxmanage=Path(__file__), run=vbox, hostonly_guids={CREATED: GUID},
    )
    assert result.outcome == "ELEVATION_UNAVAILABLE"
    assert len(vbox.calls) == before
    assert registry.find_by_hostonly_name(CREATED) is not None


def test_a_failed_removal_keeps_the_record_so_it_can_be_retried(tmp_path):
    registry, _ = _provision(tmp_path, vbox=FakeVBox())
    failing = FakeVBox(failures={"hostonlyif remove": 1})
    result = teardown_dhcp_environment(
        registry=registry, adapter_alias=CREATED, elevated=True,
        vboxmanage=Path(__file__), run=failing, hostonly_guids={CREATED: GUID},
    )
    assert result.outcome == "REMOVE_FAILED"
    assert registry.find_by_hostonly_name(CREATED) is not None


def test_a_missing_dhcp_server_does_not_block_adapter_removal(tmp_path):
    registry, _ = _provision(tmp_path, vbox=FakeVBox())
    partial = FakeVBox(failures={"dhcpserver remove": 1})
    result = teardown_dhcp_environment(
        registry=registry, adapter_alias=CREATED, elevated=True,
        vboxmanage=Path(__file__), run=partial, hostonly_guids={CREATED: GUID},
    )
    assert result.outcome == "REMOVED"
    assert registry.find_by_hostonly_name(CREATED) is None


# --- teardown ownership must be re-proven, not remembered ------------------

OTHER_GUID = "99999999-8888-4777-8666-555555555555"


def test_teardown_refuses_when_virtualbox_reports_a_different_guid(tmp_path):
    # The name still matches, so a name-based teardown would have deleted it.
    vbox = FakeVBox()
    registry, _ = _provision(tmp_path, vbox=vbox)
    calls_before = len(vbox.calls)
    result = teardown_dhcp_environment(
        registry=registry, adapter_alias=CREATED, elevated=True,
        vboxmanage=Path(__file__), run=vbox, hostonly_guids={CREATED: OTHER_GUID},
    )
    assert result.outcome == "ADAPTER_CHANGED"
    assert len(vbox.calls) == calls_before, "nothing may be removed"
    assert registry.find_by_hostonly_name(CREATED) is not None


def test_teardown_refuses_when_virtualbox_no_longer_reports_the_interface(tmp_path):
    vbox = FakeVBox()
    registry, _ = _provision(tmp_path, vbox=vbox)
    calls_before = len(vbox.calls)
    result = teardown_dhcp_environment(
        registry=registry, adapter_alias=CREATED, elevated=True,
        vboxmanage=Path(__file__), run=vbox, hostonly_guids={},
    )
    assert result.outcome == "ADAPTER_CHANGED"
    assert len(vbox.calls) == calls_before


def test_teardown_refuses_a_record_with_no_resolved_identity(tmp_path):
    from backend.recovery_lab.environment import OWNED_CREATOR

    registry = _registry(tmp_path)
    registry.record(
        DisposableEnvironment(
            environment_id="recovery-env-unresolved",
            hostonly_name=CREATED,
            network_cidr="192.168.57.0/24",
            created_at=now_iso(),
            created_by=OWNED_CREATOR,
        )
    )
    vbox = FakeVBox()
    result = teardown_dhcp_environment(
        registry=registry, adapter_alias=CREATED, elevated=True,
        vboxmanage=Path(__file__), run=vbox, hostonly_guids={CREATED: GUID},
    )
    assert result.outcome == "IDENTITY_NOT_RESOLVED"
    assert vbox.calls == []
