"""Configuration backup tool."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from ..audit_store import get_audit_store
from ..config import BACKUP_DIR
from ..file_security import harden_private_file
from ..models import BackupResult
from ..parsers.config_parser import redact_config
from ..switch_client import SwitchClient


def backup_running_config(
    client: SwitchClient,
    *,
    hostname: str = "SWITCHOPS-TEST-SW1",
    actor: str = "system",
    config_text: str | None = None,
) -> BackupResult:
    start = time.monotonic()
    # ensure paging disabled
    try:
        client.run("terminal_length_0")
    except Exception:
        pass
    if config_text is None:
        config_text = client.run("show_running_config")
    ts = datetime.now()
    safe_hostname = "".join(
        char if char.isalnum() or char in ("-", "_") else "_" for char in hostname
    ).strip("_") or "switch"
    fname = f"{safe_hostname}-running-config-{ts.strftime('%Y-%m-%d-%H%M%S-%f')}.txt"
    path: Path = BACKUP_DIR / fname
    with path.open("x", encoding="utf-8") as handle:
        handle.write(config_text)
    harden_private_file(path)
    size = path.stat().st_size

    preview = redact_config(config_text)
    if len(preview) > 4000:
        preview = preview[:4000] + "\n... (truncated)"

    duration_ms = int((time.monotonic() - start) * 1000)
    get_audit_store().record(
        actor=actor,
        action="backup_config",
        commands=["terminal length 0", "show running-config"],
        success=True,
        duration_ms=duration_ms,
        output_path=str(path),
    )

    return BackupResult(
        filename=fname,
        path=str(path),
        sizeBytes=size,
        timestamp=ts,
        redactedPreview=preview,
    )
