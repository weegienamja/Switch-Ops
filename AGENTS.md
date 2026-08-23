# Agent rules for SwitchOps

SwitchOps is a local-only Tauri v2 desktop application with a Next.js frontend
and a FastAPI sidecar for Cisco IOS Catalyst switches.

## Non-negotiable boundaries

1. Never add a raw CLI, shell, or generic command endpoint. Device commands are
   referenced by fixed symbolic allowlists and parsed into typed results.
2. Never commit credentials, host keys, runtime databases, telemetry, audit
   output, configuration backups, device policy, real network identifiers, or
   development-machine paths.
3. Keep the backend on loopback. Do not widen CORS or TrustedHost policy beyond
   the packaged local application without an explicit security review.
4. New devices and interfaces are UNMANAGED. Only a validated physical
   interface explicitly marked OPERABLE in the local per-device policy may be
   changed. PROTECTED and invalid/missing policy fail closed.
5. Controlled writes default off. Every process starts locked. Both gates must
   be checked by the backend, not inferred from the UI.
6. Back up, classify IOS output, verify, audit, and roll back every bounded
   change. Never report SSH transport success as configuration success.
7. Never save startup configuration automatically. Saving is a distinct,
   explicit, confirmed operator action.
8. Redact passwords, enable secrets, SNMP values, and configuration secrets
   from logs and API errors.
9. Production builds must not include or activate sample-output fixtures. Mock
   behavior is source-test infrastructure only.

## Implementation expectations

- Backend request and response contracts use Pydantic models.
- Interface text is normalized and injection-checked before it can enter a
  command template.
- Credential access goes through `credential_store.py`; policy access goes
  through `interface_policy.py`.
- IOS parsers tolerate unsupported or irregular output without inventing data.
- Frontend components handle loading, error, empty, stale, and offline states.
- Desktop capabilities stay minimal: bundled sidecar spawn and localhost UI.
- Release binaries, checksums, and local recovery bundles are never committed.

## Required validation

Before a release, run backend tests, frontend tests, TypeScript, ESLint, the
production frontend build, Rust/Tauri tests, sidecar packaging, NSIS/MSI builds,
secret/privacy scans, clean-user and clean-clone checks, installer inspection,
and an installed-app smoke test. Review the complete Git history intended for
publication, not only the working tree.
