# Claude context for SwitchOps

Use [AGENTS.md](AGENTS.md) as the canonical repository policy and
[README.md](README.md) for product and build instructions.

The core safety model is local and device-specific: every new interface is
UNMANAGED, only validated physical interfaces may be marked OPERABLE, protected
interfaces remain read-only, controlled writes default off, and every process
starts locked. Do not introduce a public fixed port layout or a UI-only safety
decision.

Prefer read-only observation and explicit uncertainty. Interface descriptions
are intent, MAC learning is reachability evidence, and only direct protocol
evidence may identify a neighbour. Production builds never use sample-output
fixtures.

Never add raw CLI, expose the backend beyond loopback, auto-save configuration,
log secrets, or commit runtime/release artifacts.
