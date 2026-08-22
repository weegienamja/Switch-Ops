# Backend — SwitchOps

FastAPI sidecar. Bind 127.0.0.1 only. Allowlist-driven. See [../AGENTS.md](../AGENTS.md).

## Dev

```powershell
py -3.11 -m venv ..\.venv
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:SWITCH_MOCK_MODE = "true"
..\.venv\Scripts\python.exe -m app.main
```

## Tests

```powershell
..\.venv\Scripts\python.exe -m pytest -v
```

The API binds to `127.0.0.1:8765`. API documentation is disabled unless `SWITCHOPS_ENABLE_API_DOCS=true` is set for local development.
