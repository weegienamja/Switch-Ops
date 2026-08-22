# CLAUDE.md — Claude-specific context for SwitchOps

You are working inside a **local-only** network operations dashboard for a Cisco Catalyst WS-C3560CG-8PC-S. See [AGENTS.md](AGENTS.md) for the canonical agent rules and [README.md](README.md) for project shape.

## Use the local skills

Skills live in `.claude/skills/`:

- `network-automation/SKILL.md` — safe Cisco IOS automation patterns.
- `desktop-packaging/SKILL.md` — Tauri v2 + FastAPI sidecar packaging.
- `secure-local-ops/SKILL.md` — credential handling, keyring, redaction.
- `ui-animation-polish/SKILL.md` — dashboard visual language.

Read the relevant SKILL.md before producing code in that domain.

## Safety boundaries (Claude must enforce)

- Never propose a "raw command" or "exec" endpoint.
- Never inline a real password into source, tests, sample outputs, or prompts. Use `__REPLACE_WITH_LOCAL_SECRET__`.
- Never widen CORS beyond `localhost` / `tauri://localhost`.
- Never weaken `protected_interfaces` to allow Gi0/1, Gi0/2, or Vlan1.
- Never expose the backend on a non-loopback interface.

## Build phases

1. Scaffold + docs.
2. Backend core: config, models, credential store, audit, allowlist, parsers, mock client, FastAPI routes.
3. Real switch clients: Netmiko + legacy Paramiko.
4. Frontend: setup wizard, dashboard, panels, Motion animations.
5. Tauri shell + sidecar packaging.
6. Tests + secret scan + commit + push.

## When in doubt

- Prefer mock mode.
- Prefer read-only.
- Prefer adding an allowlisted action over loosening the registry.
- Prefer logging "redacted" over logging the value.
