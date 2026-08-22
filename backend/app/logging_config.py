"""Logging setup with a secret-redaction filter."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable

from .file_security import harden_private_file

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(password\s*[:=]\s*)\S+", re.IGNORECASE), r"\1<redacted>"),
    (
        re.compile(r"(enable\s+(?:secret|password)(?:\s+\d+)?\s+)\S+", re.IGNORECASE),
        r"\1<redacted>",
    ),
    (re.compile(r"(secret\s*[:=]\s*)\S+", re.IGNORECASE), r"\1<redacted>"),
    (
        re.compile(r"((?:switchPassword|switchEnableSecret)[\"']?\s*[:=]\s*[\"']?)[^\s\"']+", re.IGNORECASE),
        r"\1<redacted>",
    ),
    (re.compile(r"^(\s*description\s+).+$", re.IGNORECASE | re.MULTILINE), r"\1<redacted>"),
]

_DYNAMIC_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    """Register a live secret value so the redaction filter masks it.

    Values shorter than 4 chars are ignored to avoid false positives.
    """
    if value and len(value) >= 4:
        _DYNAMIC_SECRETS.add(value)


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for pat, replacement in _SECRET_PATTERNS:
        out = pat.sub(replacement, out)
    for s in _DYNAMIC_SECRETS:
        if s and s in out:
            out = out.replace(s, "<redacted>")
    return out


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                record.args = tuple(
                    redact(a) if isinstance(a, str) else a for a in record.args
                )
        except Exception:
            pass
        return True


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if getattr(root, "_switchops_configured", False):
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s :: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    log_path = log_dir / "server.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    harden_private_file(log_path)
    handlers: Iterable[logging.Handler] = (
        logging.StreamHandler(),
        file_handler,
    )
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(_RedactingFilter())
        root.addHandler(h)
    root._switchops_configured = True  # type: ignore[attr-defined]
