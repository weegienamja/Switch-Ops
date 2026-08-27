# EWPS v0.2.4 Alpha — Recovery Safety Architecture

**Experimental pre-release:** `ewps-v0.2.4-alpha`

This is a patch-level release on the `research/ewps-v0.2` line. The EWPS
Observatory is unchanged: same model version `0.2.0`, same equations, same
schema, same shadow-mode boundary.

The substantive change is a new subsystem that has nothing to do with EWPS. It
is the beginning of an answer to a question SwitchOps has deliberately refused
to act on until now: **if a management-path change goes wrong, can the tool get
back in?**

## Why this exists

Every remote switch change carries the same risk: the change succeeds, and the
operator loses the path they made it over. Most tooling answers this with a
rollback timer and hope. SwitchOps will not ship an automatic recovery it cannot
prove, so the work started at the other end — build the primitive, then try hard
to break it, and record only what survived.

The result is a Recovery Lab: a development-only harness that performs real,
elevated Windows address operations on a disposable adapter it created itself,
and a product-side evidence model that records capability by capability what has
actually been observed. Nothing in the shipped product mutates an address.

## What is now experimentally validated

On Windows, measured on a disposable DHCP-controlled adapter:

- **Temporary address creation** via the IP Helper unicast API, with real
  (non-optimistic) duplicate address detection settling to Preferred.
- **DHCP same-interface coexistence.** The temporary address lives as an
  independent `MANUAL/MANUAL` row beside a DHCP-controlled primary. The primary
  stays `DHCP/DHCP` and Preferred with its lease still counting down.
- **Explicit reservation authority.** No address is created without a live,
  scoped, time-bounded reservation bound to that exact candidate, prefix,
  environment and operation. With no reservation the run refuses and creates
  nothing.
- **Process-death ownership persistence.** A process that dies holding a
  temporary address leaves durable evidence of exactly what it owned.
- **New-process ownership reconstruction.** A second, unrelated process rebuilds
  that ownership from durable state alone — re-proving the adapter identity and
  matching every predicate on the exact row.
- **Exact owned-row deletion.** One delete, of one row it can prove it owns,
  confirmed absent afterwards.
- **Baseline preservation.** The original DHCP primary, the interface
  addressing, the routes, and DNS are all re-verified intact after cleanup.
- **Fail-closed reconciliation.** Where ownership cannot be proven, the result
  is zero deletes and a record left open for a human. Incomplete evidence never
  becomes permission.

## What was actually measured

The crash experiment was run by hand, elevated, and observed directly:

- **Scope:** Windows · same boot · a harness-owned disposable DHCP adapter ·
  deliberate process death · reconciliation by a new process.
- One process created a temporary RFC 5737 address, reached Preferred, and then
  terminated through a crash path that bypasses rollback, cleanup and journal
  closure entirely.
- Windows then showed both rows at once: the untouched DHCP lease, and a
  temporary address that had outlived the process that made it.
- A new process reconstructed ownership, deleted exactly that row, and confirmed
  the original DHCP and network baseline intact.

Earlier gates were measured the same way. Each capability records the
environment it was observed in, because a result from one adapter class does not
carry to another.

## Limitations — read these

This release does **not** demonstrate, and must not be read as:

- production automatic recovery, or any production execution;
- any behaviour on a production adapter;
- production-scoped address authority — there is none, and the live production
  recovery plan remains `BLOCKED` on `COLLISION_SAFE_ADDRESS_UNAVAILABLE`;
- recovery across a **reboot**, an OS or machine crash, or a power loss;
- recovery across a NIC reset, a driver restart, or an adapter recreated
  underneath the ownership record;
- an approved elevated production executor. There is no executor. Recovery is
  planning-only.

`production_recovery_validated` is **false**. One capability,
`PRODUCTION_ADAPTER_CLASS`, is still `NOT_ATTEMPTED` and no lab experiment can
retire it — satisfying it requires evidence from a real production adapter,
which this project has deliberately never gathered.

The Windows `CreationTimeStamp` used during reconciliation is an additional
**same-boot** discriminator alongside adapter GUID, LUID and index checks. It is
not a cryptographic value and not a permanent object identity.

## Explicit non-changes

- No EWPS equations, confidence weights, loss handling, evidence thresholds,
  hysteresis, impairment profiles, or scenario progression changed.
- No EWPS schema change. Experiments recorded by `ewps-v0.2.3-alpha` continue to
  export as schema v4, unchanged.
- No routing or traffic steering was added. The Observatory remains shadow mode
  only.
- The shipped product still contains no address-mutation primitive at all. The
  Recovery Lab is a separate development-only harness.
- No interface write policy, credential, or privacy boundary changed.

## What comes next

- Production-scoped address authority (Gate 3a): currently unavailable.
- An operator-approved elevated production executor (Gate 4): not implemented.
- Evidence from a production adapter class: not attempted, by design.

Until all three exist, recovery stays exactly where it is — planning-only, and
honest about it.
