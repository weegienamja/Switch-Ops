---
name: network-automation
description: Safe Cisco IOS automation patterns for the SWITCHOPS-TEST-SW1 switch. Use whenever adding a new command, parser, or write action.
---

# Network automation skill

## Core principles

1. **Allowlist or refuse.** Every Cisco command must exist in `backend/app/command_registry.py` by symbolic name. Never construct CLI strings from user input.
2. **Read-only is the default.** Only escalate to write mode behind `settings.enable_write_actions`.
3. **Backup before write.** Sequence: `terminal length 0` → `show running-config` → save backup → action → `write memory` → verify.
4. **Protected interfaces** (`GigabitEthernet0/1`, `GigabitEthernet0/2`, `Vlan1`) raise `ProtectedInterfaceError` at the registry, not at the route.

## Adding a new read-only command

1. Add the literal IOS command to `READ_ONLY_COMMANDS` in `command_registry.py`.
2. Add a tolerant parser in `app/parsers/<name>.py`.
3. Add a sample output in `app/sample_outputs/<name>.txt` matching the observed health state.
4. Add a FastAPI route in `app/main.py` that returns a typed Pydantic model.
5. Add a pytest covering the parser against the sample output.

## Adding a new write action

1. Define the action in `SAFE_WRITE_ACTIONS` with the *exact* config command sequence.
2. Verify the target interface is in `ALLOWLISTED_INTERFACES` and NOT in `PROTECTED_INTERFACES`.
3. Build the action through `tools/safe_write.py` which always runs `backup_running_config` first.
4. Audit-log before and after `show running-config` snippets, not the secret values.
5. Require frontend confirmation modal.

## Legacy SSH

The switch (IOS 12.2(55)EX2) requires deprecated KEX/cipher/MAC suites. Use the legacy Paramiko transport in `legacy_ssh_client.py`; do not weaken settings outside that module.

## Never

- Build a `/execute` or `/raw` endpoint.
- Expose an LLM tool that takes a free-form command string.
- Print credentials, even at debug level.
