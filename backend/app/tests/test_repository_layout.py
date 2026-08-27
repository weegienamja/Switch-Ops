"""The test suite must import identically from any working directory.

`backend/` has no __init__.py, so `backend.app.*` only resolves when the
repository root is on sys.path. That used to depend on the working directory,
so running pytest from inside backend/ failed collection on ~20 modules and
looked like a broken suite. The pythonpath setting below is the fix; these
tests stop it being removed by accident.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"


def _pytest_config() -> dict:
    payload = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return payload["tool"]["pytest"]["ini_options"]


def test_pytest_config_puts_the_repository_root_on_the_path():
    config = _pytest_config()
    assert config["pythonpath"] == [".."], (
        "pythonpath is resolved against rootdir (backend/), so '..' is the "
        "repository root. Removing it reintroduces the directory-sensitive "
        "collection failure."
    )


def test_backend_pyproject_is_the_only_pytest_configuration():
    # Two configurations would mean rootdir depends on how pytest is invoked,
    # which is the ambiguity this layout is meant to remove.
    competing = [
        REPO_ROOT / "pytest.ini",
        REPO_ROOT / "tox.ini",
        REPO_ROOT / "setup.cfg",
    ]
    assert [path.name for path in competing if path.exists()] == []

    root_pyproject = REPO_ROOT / "pyproject.toml"
    if root_pyproject.exists():
        payload = tomllib.loads(root_pyproject.read_text(encoding="utf-8"))
        assert "pytest" not in payload.get("tool", {})


def test_the_repository_root_is_importable_during_collection():
    resolved = {Path(entry).resolve() for entry in sys.path if entry}
    assert REPO_ROOT.resolve() in resolved


def test_backend_is_a_namespace_package_not_a_module_directory():
    # If someone adds backend/__init__.py the import semantics change silently,
    # because pytest would then insert a different directory on sys.path.
    assert not (BACKEND_ROOT / "__init__.py").exists()
    assert (BACKEND_ROOT / "app" / "__init__.py").exists()
    assert (BACKEND_ROOT / "resilience_lab" / "__init__.py").exists()


def test_both_test_entry_points_are_importable():
    import backend.app.main  # noqa: F401
    import backend.resilience_lab.runner  # noqa: F401
