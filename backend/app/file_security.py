"""Best-effort private permissions for sensitive local runtime files."""
from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path


def harden_private_file(path: Path) -> None:
    """Restrict a runtime file to the current user (and SYSTEM on Windows)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

    if os.name != "nt":
        return

    domain = os.environ.get("USERDOMAIN", "").strip()
    username = os.environ.get("USERNAME", "").strip() or getpass.getuser()
    account = f"{domain}\\{username}" if domain else username
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{account}:(F)",
                "/grant:r",
                "*S-1-5-18:(F)",
            ],
            check=False,
            capture_output=True,
            timeout=10,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        # The enclosing per-user AppData directory is still private by default.
        pass
