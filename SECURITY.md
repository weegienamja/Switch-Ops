# Security policy

## Supported versions

Security fixes are provided for the latest published SwitchOps release. The
currently supported public release line is 0.6.0.

## Report a vulnerability

Use GitHub's private vulnerability reporting flow from the repository's
**Security** tab when it is available. Do not publish credentials, device
configuration, host keys, network identifiers, or exploit details in a public
issue. If private reporting is unavailable, open a public issue containing no
sensitive detail and ask the repository owner to establish a private channel.

Include the affected version, Windows version, reproduction boundary, expected
behavior, and observed behavior. Redact all device-specific values.

## Product security boundary

- The backend is designed to bind only to `127.0.0.1` and accepts only the
  packaged UI's local origins.
- Device access is serialized through one worker. Commands resolve from fixed
  symbolic allowlists; no API accepts arbitrary IOS CLI.
- Host keys are pinned on first use and changes fail closed.
- Controlled writes require a valid local policy, global opt-in, an OPERABLE
  physical interface, and a process-local unlock. Each process starts locked.
- New devices and interfaces are UNMANAGED. Invalid policy data disables
  writes.
- Operations back up, verify, audit, and roll back where the bounded inverse is
  available. Startup configuration is never saved automatically.
- Production builds disable fixture mode and do not bundle sample IOS output.
- The optional Meraki integration accepts its API key only through Windows
  Credential Manager. It implements named, allowlisted Dashboard API GET
  operations, validates pagination origin, and has no generic proxy or write
  method.
- Meraki evidence cannot grant IOS write authority. A Meraki outage, rate limit,
  malformed response, or persistence failure leaves the Catalyst-only paths
  available.
- Cross-provider serials, hardware MACs, management addresses, and recent-client
  addresses are protected with a per-install HMAC before persistence. Raw
  Meraki responses and privacy-rich client fields are discarded.

## Operator responsibilities

Switch configuration backups, telemetry, audit records, host-key pins,
interface policy, selected Meraki scope, normalized provider evidence, and
local identity decisions are sensitive local data under
`%LOCALAPPDATA%\SwitchOps`.
Protect the Windows account and disk, restrict switch SSH to a trusted
management network, use a least-privileged device account where practical, and
review backups before sharing them.

Legacy SSH compatibility is scoped to the SwitchOps backend process, but the
underlying algorithms remain weak. Network isolation is the compensating
control; compatibility is not a substitute for upgrading device software or
hardware.

## Out of scope

SwitchOps does not claim to secure a compromised Windows account, a compromised
switch, or traffic outside its localhost process boundary. It is not a remote
access service, credential vault, firewall, or substitute for device backups
and change-control procedures.
