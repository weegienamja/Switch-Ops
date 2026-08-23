# Backend — SwitchOps

FastAPI sidecar. Bind 127.0.0.1 only. Allowlist-driven. See [../AGENTS.md](../AGENTS.md).

## Dev

```powershell
py -3.11 -m venv ..\.venv
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
..\.venv\Scripts\python.exe -m app.main
```

## Tests

```powershell
..\.venv\Scripts\python.exe -m pytest -v
```

The API binds to `127.0.0.1:8765`. API documentation is disabled unless `SWITCHOPS_ENABLE_API_DOCS=true` is set for local development.

With no stored credentials, the API remains in setup state and presents no
device data. Synthetic sample output is used only by automated tests; a source
developer may opt into it with `SWITCH_MOCK_MODE=true`. Packaged sidecars force
that setting off and do not contain the fixture directory.
