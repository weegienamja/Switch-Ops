"""Build and runtime provenance for the SwitchOps backend.

SwitchOps runs its backend two ways: as a plain Python module during
development, and as a frozen PyInstaller sidecar inside the Tauri desktop
shell. Both bind 127.0.0.1:8765, so it is easy to leave an old process
listening and never notice that the desktop application is talking to stale
code. This module exposes the minimum non-secret facts needed to prove which
build is answering.

Privacy: no absolute paths, no Windows user name, no credentials. The
executable is reported by base name only.
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Human-facing contract version. Bump when the JSON contract changes in a way
# a frontend must know about.
API_SCHEMA_VERSION = 2

# Unique to this process. The desktop shell passes a nonce through the
# environment and compares it, which is how it proves the backend answering
# /health is the sidecar it actually spawned rather than a stray listener.
SIDECAR_TOKEN_ENV = "SWITCHOPS_SIDECAR_TOKEN"

STARTED_AT = datetime.now(timezone.utc)
FROZEN = bool(getattr(sys, "frozen", False))
RUNTIME_MODE = "frozen-sidecar" if FROZEN else "development"

_APP_ROOT = Path(__file__).resolve().parent


def _frozen_build_id() -> str:
    """Identify a PyInstaller one-file build by its executable fingerprint.

    The Python sources live inside the archive, so hashing ``*.py`` is not
    possible. Size plus modification time identifies a specific built binary
    without reading 30 MB on every start.
    """
    try:
        stat = Path(sys.executable).stat()
        seed = f"{stat.st_size}:{int(stat.st_mtime)}"
    except OSError:
        seed = "unknown"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _development_build_id() -> str:
    """Fingerprint the on-disk ``app`` package so edited source shows up."""
    digest = hashlib.sha256()
    for path in sorted(_APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        rel = path.relative_to(_APP_ROOT).as_posix()
        digest.update(f"{rel}:{stat.st_size}:{stat.st_mtime_ns}\n".encode("utf-8"))
    return digest.hexdigest()[:12]


def _compute_build_id() -> str:
    return _frozen_build_id() if FROZEN else _development_build_id()


BUILD_ID = _compute_build_id()


def expected_sidecar_token() -> str | None:
    """Return the nonce the desktop shell asked this process to echo."""
    token = os.environ.get(SIDECAR_TOKEN_ENV)
    return token or None
