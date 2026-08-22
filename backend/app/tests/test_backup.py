from datetime import datetime
import re
from app.tools.backup import backup_running_config
from app.switch_client import MockSwitchClient


def test_backup_filename_format(tmp_path, monkeypatch):
    from app import tools
    from app.tools import backup as backup_mod
    monkeypatch.setattr(backup_mod, "BACKUP_DIR", tmp_path)
    client = MockSwitchClient()
    result = backup_running_config(client)
    assert result.filename.startswith("SWITCHOPS-TEST-SW1-running-config-")
    assert result.filename.endswith(".txt")
    assert re.fullmatch(
        r"SWITCHOPS-TEST-SW1-running-config-\d{4}-\d{2}-\d{2}-\d{6}-\d{6}\.txt",
        result.filename,
    )
    assert result.size_bytes > 0
    assert "<redacted>" in result.redacted_preview


def test_backups_do_not_overwrite_within_the_same_minute(tmp_path, monkeypatch):
    from app.tools import backup as backup_mod

    monkeypatch.setattr(backup_mod, "BACKUP_DIR", tmp_path)
    first = backup_running_config(MockSwitchClient())
    second = backup_running_config(MockSwitchClient())
    assert first.path != second.path
    assert len(list(tmp_path.glob("*.txt"))) == 2
