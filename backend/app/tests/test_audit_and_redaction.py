import pytest

from app.audit_store import AuditStore
from app.logging_config import redact, register_secret
from app.tools import read_only


def test_redact_inline_password():
    register_secret("Hunter22LongEnough")
    s = "user supplied password=Hunter22LongEnough during connect"
    out = redact(s)
    assert "Hunter22LongEnough" not in out
    assert "<redacted>" in out


def test_redact_unregistered_enable_secret_and_description():
    output = redact("enable secret 5 unknown-value\ndescription private-device-name")
    assert "unknown-value" not in output
    assert "private-device-name" not in output


def test_audit_store_writes(tmp_path):
    store = AuditStore(db_path=tmp_path / "a.sqlite", jsonl_path=tmp_path / "a.jsonl")
    ev = store.record(
        actor="t",
        action="read:show_version",
        commands=["show version"],
        success=True,
        duration_ms=12,
    )
    assert ev.id is not None
    recent = store.recent()
    assert len(recent) == 1
    assert recent[0].action == "read:show_version"
    assert (tmp_path / "a.jsonl").exists()


def test_audit_redacts_secrets(tmp_path):
    register_secret("Hunter22LongEnough")
    store = AuditStore(db_path=tmp_path / "b.sqlite", jsonl_path=tmp_path / "b.jsonl")
    ev = store.record(
        actor="t",
        action="write:test",
        commands=["enable secret 5 Hunter22LongEnough"],
        success=True,
        duration_ms=1,
        before_state="password=Hunter22LongEnough",
    )
    blob = (tmp_path / "b.jsonl").read_text(encoding="utf-8")
    assert "Hunter22LongEnough" not in blob
    assert "<redacted>" in blob


def test_read_audit_does_not_persist_raw_library_exception(monkeypatch, tmp_path):
    store = AuditStore(db_path=tmp_path / "c.sqlite", jsonl_path=tmp_path / "c.jsonl")
    monkeypatch.setattr(read_only, "get_audit_store", lambda: store)

    class BrokenClient:
        def run(self, _symbol):
            raise RuntimeError("socket failed for 192.0.2.44 at C:\\private\\driver.py")

    with pytest.raises(RuntimeError):
        read_only.run_and_audit(BrokenClient(), symbol="show_version")

    event = store.recent(limit=1)[0]
    assert event.error_type == "RuntimeError"
    assert event.error_message == "The device operation failed."
    assert "192.0.2.44" not in (tmp_path / "c.jsonl").read_text(encoding="utf-8")
