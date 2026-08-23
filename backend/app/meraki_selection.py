"""Private local persistence for the selected Meraki organization/network."""
from __future__ import annotations

import json
from pathlib import Path

from .config import DATA_DIR
from .file_security import harden_private_file
from .meraki_models import MerakiSelection


SELECTION_FILE = DATA_DIR / "meraki-selection.json"


class MerakiSelectionStore:
    def __init__(self, path: Path = SELECTION_FILE) -> None:
        self._path = path

    def load(self) -> MerakiSelection | None:
        if not self._path.exists():
            return None
        try:
            return MerakiSelection.model_validate_json(self._path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def save(self, selection: MerakiSelection) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(
            selection.model_dump_json(by_alias=True, indent=2),
            encoding="utf-8",
        )
        harden_private_file(temporary)
        try:
            temporary.replace(self._path)
            harden_private_file(self._path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()


_store: MerakiSelectionStore | None = None


def get_meraki_selection_store() -> MerakiSelectionStore:
    global _store
    if _store is None:
        _store = MerakiSelectionStore()
    return _store
