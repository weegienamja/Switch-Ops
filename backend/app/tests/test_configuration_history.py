from datetime import datetime, timedelta, timezone

from backend.app.configuration_history import ConfigurationHistoryStore


BASE_CONFIG = """hostname SYNTH-SW1
enable secret 5 __LOCAL_SECRET_HASH__
interface GigabitEthernet0/4
 description Lab AP
 shutdown
"""


def test_first_observation_creates_private_version_without_drift(tmp_path):
    store = ConfigurationHistoryStore(
        tmp_path / "config-history.sqlite",
        tmp_path / "versions",
    )
    entry, changed = store.observe(
        device_id="switch-synthetic",
        hostname="SYNTH-SW1",
        config_text=BASE_CONFIG,
        observed_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )

    assert changed is False
    assert entry.change_detected is False
    assert entry.source == "initial_observation"
    assert (tmp_path / "versions" / entry.filename).exists()
    assert len(entry.fingerprint) == 64


def test_unchanged_configuration_does_not_duplicate_history(tmp_path):
    store = ConfigurationHistoryStore(tmp_path / "history.sqlite", tmp_path / "versions")
    first, _ = store.observe(
        device_id="switch-synthetic", hostname="SYNTH-SW1", config_text=BASE_CONFIG
    )
    second, changed = store.observe(
        device_id="switch-synthetic", hostname="SYNTH-SW1", config_text=BASE_CONFIG
    )

    assert changed is False
    assert second.id == first.id
    assert len(store.recent()) == 1


def test_changed_configuration_has_redacted_line_diff_and_unknown_source(tmp_path):
    store = ConfigurationHistoryStore(tmp_path / "history.sqlite", tmp_path / "versions")
    first_time = datetime(2026, 8, 22, tzinfo=timezone.utc)
    store.observe(
        device_id="switch-synthetic",
        hostname="SYNTH-SW1",
        config_text=BASE_CONFIG,
        observed_at=first_time,
    )
    changed_config = BASE_CONFIG.replace(" description Lab AP", " description Updated AP").replace(
        "__LOCAL_SECRET_HASH__", "__DIFFERENT_LOCAL_HASH__"
    )
    entry, changed = store.observe(
        device_id="switch-synthetic",
        hostname="SYNTH-SW1",
        config_text=changed_config,
        observed_at=first_time + timedelta(minutes=5),
    )

    diff = "\n".join(entry.redacted_diff)
    assert changed is True
    assert entry.change_detected is True
    assert entry.source == "external_or_unknown"
    assert "+ description Updated AP" in diff
    assert "__LOCAL_SECRET_HASH__" not in diff
    assert "__DIFFERENT_LOCAL_HASH__" not in diff
    assert "<redacted>" in diff


def test_known_good_marker_is_unique_per_device(tmp_path):
    store = ConfigurationHistoryStore(tmp_path / "history.sqlite", tmp_path / "versions")
    first, _ = store.observe(
        device_id="switch-synthetic", hostname="SYNTH-SW1", config_text=BASE_CONFIG
    )
    second, _ = store.observe(
        device_id="switch-synthetic",
        hostname="SYNTH-SW1",
        config_text=BASE_CONFIG.replace("Lab AP", "Updated AP"),
    )
    store.mark_known_good(first.id)
    marked = store.mark_known_good(second.id)

    entries = store.recent(device_id="switch-synthetic")
    assert marked.known_good is True
    assert [entry.id for entry in entries if entry.known_good] == [second.id]


def test_management_context_matches_target_without_returning_configuration(tmp_path):
    store = ConfigurationHistoryStore(tmp_path / "history.sqlite", tmp_path / "versions")
    store.observe(
        device_id="switch-synthetic",
        hostname="SYNTH-SW1",
        config_text=(
            BASE_CONFIG
            + "interface Vlan1\n"
            + " ip address 192.0.2.10 255.255.255.0\n"
            + "ip default-gateway 192.0.2.1\n"
        ),
        observed_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
    )

    context = store.management_context_for_target("192.0.2.10")
    assert context == {
        "device_id": "switch-synthetic",
        "observed_at": datetime(2026, 8, 25, tzinfo=timezone.utc),
        "management_ip": "192.0.2.10",
        "management_mask": "255.255.255.0",
        "gateway": "192.0.2.1",
    }
    assert store.management_context_for_target("203.0.113.10") is None
