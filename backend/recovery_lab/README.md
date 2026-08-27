# SwitchOps Recovery Lab

Development-only validation of the temporary-address recovery primitive. This
is **not** the SwitchOps recovery executor, and the product remains
planning-only: `RecoveryExecutionArchitecture.mode` is `PLANNING_ONLY`,
`executorImplemented` is `False`, and there is no approval control.

The question this package exists to answer is narrow:

> Can a Windows host temporarily regain **read-only** management reachability to
> a Catalyst whose management address is on a prefix the host has left, without
> disturbing DHCP, the default route, or DNS?

## Runtime boundary

* nothing under `backend.app` imports this package (pinned by test);
* it has no API route and is not bundled into the PyInstaller sidecar (pinned);
* `CreateUnicastIpAddressEntry` / `DeleteUnicastIpAddressEntry` appear in no
  production module (pinned);
* the packaged sidecar runs unelevated, and Windows refuses the mutating calls
  to unelevated callers — measured, not assumed.

## Why this primitive

`New-NetIPAddress` and WMI `EnableStatic` reconfigure an interface's addressing
and thereby disable DHCP on it. `CreateUnicastIpAddressEntry` adds one row to
the unicast address table and leaves the DHCP-learned row alone. That is the
entire reason it is a candidate.

## What has been measured on this machine

Windows 11 Home build 26200, Intel I225-V and virtual adapters:

| Fact | Measured value |
| --- | --- |
| `sizeof(MIB_UNICASTIPADDRESS_ROW)` | 80 bytes (x64); offsets validated against known addresses |
| `InitializeUnicastIpAddressEntry` defaults | `OnLinkPrefixLength=255`, `PrefixOrigin=16`, `SuffixOrigin=16`, lifetimes `0xffffffff`, `DadState=0` |
| `CreateUnicastIpAddressEntry` unelevated | `5 ERROR_ACCESS_DENIED` — fails closed, nothing created |
| DHCP-learned address | `PrefixOrigin=DHCP`, `SuffixOrigin=DHCP`, **finite** lifetime |
| Manually configured address | `PrefixOrigin=MANUAL`, `SuffixOrigin=MANUAL`, infinite lifetime |

Two consequences shaped the design:

**The /32 trap.** `OnLinkPrefixLength` initialises to 255, which Microsoft
documents as producing a /32. An executor that forgot to set it would create an
address with no on-link route to the management prefix: the recovery would look
successful and still reach nothing. The harness treats an observed prefix length
that differs from the requested one as `ROUTE_NOT_ESTABLISHED` and rolls back.

**DHCP preservation is observable.** Because the DHCP row is identified by
origin and carries a finite lease, "DHCP was preserved" is something to
*re-read and assert*, not something to assume from the mechanism.

## What the elevated run settled, and what it did not

An elevated run on 2026-08-27 measured the primitive end to end on the isolated
statically addressed adapter. Real IPv4 DAD, the `TENTATIVE → PREFERRED`
transition, explicit on-link prefix behaviour, exact deletion, and clean
rollback are therefore **observed**, not inferred.

Gate 2 has since been measured as well, on a disposable VirtualBox DHCP adapter
(see below). Still unvalidated:

* process-death/new-process crash ownership reconciliation. The mechanism and
  adversarial tests now exist, but the deliberate two-process experiment has
  **not** been run;
* anything at all on a production adapter, by design.

`production_recovery_validated` therefore remains **false**: it is the
conjunction of every required capability, and two are still `NOT_ATTEMPTED` --
`CRASH_OWNERSHIP_RECONCILIATION` and `PRODUCTION_ADAPTER_CLASS`. The second is a
required capability on purpose. Every measurement so far was taken on a
disposable virtual adapter, so a field named for production must not go true
while no production adapter has ever been touched.

## Running it

```powershell
python -m backend.recovery_lab inspect          # read-only, no elevation needed
python -m backend.recovery_lab restart-check    # compare journal descriptions (read-only)
python -m backend.recovery_lab crash-status     # inspect crash state (read-only)

# Mutating. Requires elevation and an explicit --allow.
python -m backend.recovery_lab experiment `
    --interface "Ethernet 2" --address 192.0.2.250 --prefix-length 24 `
    --allow "Ethernet 2"
```

`inspect` labels each adapter `PRODUCTION` or `candidate`. On this machine
`Ethernet` (default route + DHCP) and `WiFi` (default route) are production; the
VirtualBox Host-Only adapter is the intended experiment target.

## Safety model

A destination is eligible only on positive evidence. It is refused when it
carries a default route, holds a DHCP lease, was not explicitly named with
`--allow`, or when the address is outside RFC 5737 documentation space or
already present. Those blockers are independent of elevation: being
administrator does not make the production interface eligible, because "we were
not admin at the time" is not a safety property.

## Crash ownership and reconciliation

A temporary unicast row is not tied to the lifetime of the process that called
`CreateUnicastIpAddressEntry`. A process can therefore die after creation and
before ordinary rollback. The replacement process must reconstruct authority;
it may not inherit the dead process's belief that a row was its own.

### Why intent is not ownership

The journal is written before create. That ordering closes the opposite and
more dangerous gap: a row must never exist with no durable indication that an
operation might have created it. An intent record is still only a description.
Consider this same-boot sequence:

1. the harness creates `192.0.2.250` and dies;
2. that row disappears for an unrelated reason;
3. another actor creates `192.0.2.250` on the same adapter.

LUID, ifIndex, address and prefix can all match the old intent. Deleting on
those fields would delete the other actor's row.

Windows exposes `CreationTimeStamp` in `MIB_UNICASTIPADDRESS_ROW`. Microsoft
defines it as the time the address was created. It is **not documented as a
unique, immutable, or cryptographic object identifier**. In the narrow same-
boot experiment it is useful as an additional discriminator: a later-created
row was observed with a different value. Immediately after create, Phase A
therefore records the positive timestamp and a fingerprint of:

* InterfaceLuid;
* interface index;
* address and prefix length;
* creation timestamp;
* prefix and suffix origin.

DAD state is intentionally excluded because a legitimate row changes from
`TENTATIVE` to `PREFERRED`. Any mismatch in the recorded properties removes
deletion authority; a timestamp match alone does not grant it.

There is an unavoidable user-space interval between the successful create and
the durable post-apply write. If the process dies there, the journal contains
intent only. A present matching row is then `OWNERSHIP_UNPROVABLE`, receives
zero deletes, and requires human reconciliation. The deliberate experiment
does not intentionally crash at that checkpoint.

### Exact deletion predicates

`DELETE_AUTHORISED` is the only verdict that permits a mutation. All of these
must hold at the same time:

1. the journal parses under the exact known schema and the record is
   outstanding;
2. the operation lock can be acquired, proving no cooperating owner still
   holds it;
3. live environment authority is re-established from the harness-created
   environment, not copied from the journal;
4. VirtualBox still reports the recorded host-only interface GUID and exactly
   one Windows adapter reports that `InterfaceGuid`;
5. that GUID-correlated adapter's current LUID and ifIndex exactly equal the
   values recorded by Phase A;
6. the record contains the exact pre-operation DHCP primary plus address and
   network baseline fingerprints;
7. no second outstanding record claims the same row;
8. exactly one live row matches LUID, ifIndex, address and prefix;
9. durable post-apply timestamp and row fingerprint both match;
10. the live row remains `MANUAL/MANUAL`.

The table and journal are read again immediately before delete. The Windows
delete API is keyed by address plus interface LUID (ifIndex is the fallback);
it is not an atomic compare-and-delete over the timestamp or fingerprint. The
pre-delete recheck closes races visible before the call, but a non-cooperating
external actor replacing the row in the final interval is an operating-system
TOCTOU the user-space API cannot eliminate. This limitation is one reason the
scope is a harness-owned disposable adapter with controlled actors.

After the call, the complete adjudication runs again. The record closes only if
the exact row remains absent, the same DHCP primary address/prefix is still
`DHCP/DHCP`, `PREFERRED`, and finite-lease, the interface address fingerprint
matches, and the routes/default routes/DNS/source-selection fingerprint matches
the pre-operation snapshot. A mismatch is reported and left untouched.

There is no subnet cleanup, documentation-range cleanup, `MANUAL`-row sweep,
candidate cycling, adapter reset, DHCP change, DNS change, or route change.

### Durable state and concurrency

Journal and reservation writes use this process-death sequence:

1. take a kernel byte-range lock around the complete read/modify/write;
2. write a uniquely named temporary file;
3. flush Python buffers and call `fsync` on that file;
4. atomically replace the destination.

The lock prevents concurrent writers from losing one another's records. Unique
temporary names prevent writers from sharing a partial file. A malformed,
truncated, duplicate-ID, unknown-field, impossible-state, or unknown-schema
journal raises and is never interpreted as an empty journal. Operation IDs are
hashed before becoming lock filenames, so journal content cannot escape the
lock directory.

The journal is structurally validated, not cryptographically signed. Its trust
boundary is the local Recovery Lab account and its private ignored state
directory. A user able to rewrite that state can forge a well-formed record;
this mechanism is not suitable for a shared or adversarial host and is not a
product authority store.

This is a **process-death durability claim on the same boot only**. The parent
directory is not separately synced and the experiment has not measured power
loss, an OS crash, filesystem crash consistency, reboot, NIC reset, driver
restart, adapter recreation, or upgrade.

Three kernel locks have deliberately different lifetimes:

* a short journal or reservation lock serialises one durable update;
* the per-operation lock is acquired before intent and held until normal
  completion or deliberate `os._exit`; the kernel releases it on process
  death, so lock-file existence is never treated as liveness;
* one Phase B reconciliation lock spans observation, adjudication, delete,
  verification, reservation release and journal close. A second reconciler
  gets `BLOCKED` with zero deletes.

Phase B then takes and retains each dead operation's lock while processing it.
Work discovery is not authority: it reloads that record and all claimants only
after acquiring the lock. Tests exercise two actual processes contending for
the same reconciliation and two actual processes writing intents concurrently.

### Interface identity and absence

An alias is only a label. Renaming it does not defeat ownership when GUID, LUID
and ifIndex still match. GUID change, ambiguous GUID, LUID change, or ifIndex
change is a contradiction and receives zero deletes. This deliberately refuses
adapter removal/recreation and re-enumeration even when a display name is
reused.

If the exact row is absent, Phase B performs no delete and rechecks absence
before closure. It may close as `ALREADY_ABSENT` only after environment, GUID,
live LUID/ifIndex, DHCP primary, address baseline, and network baseline are all
re-proven. It does not infer why the row disappeared. The same address on a
different adapter is reported and left untouched; absence never becomes a
search for something similar. The same address with the wrong prefix on the
recorded interface is a contradiction, not absence.

`restart-check` is retained as a legacy read-only description comparison. Its
outcomes are `RECORDED_ROW_PRESENT` and `RECORDED_ROW_ABSENT`; neither is an
ownership adjudication, neither closes the journal, and neither claims a cause.
Use `crash-status` for intermediate crash evidence and `crash-reconcile` for the
strict decision.

### Reservation lifecycle

Environment authority, address reservation authority, and row ownership remain
three separate questions. A fresh `LAB_HARNESS_RESERVED` reservation authorises
creation only in its named disposable environment. Phase A atomically compares
and binds the observed reservation to a newly locked operation; two processes
cannot both inherit an unbound reservation.

If the process dies after the binding replace but before journal intent, no
address has been created and no ownership record exists. The reservation stays
bound to the dead operation and cannot authorise a new one; an operator must
close that bookkeeping record manually. This safe but non-automatic interval is
separate from the create-to-post-apply ownership gap.

Before create, a refusal or baseline/default-route/lock failure consumes the
selected one-shot reservation when it is still unchanged. A binding race never
overwrites or releases the winning operation's binding. An intent or create
failure closes bookkeeping only after the delete-key row is proven absent and
the baseline is restored. After a row exists, missing post-apply evidence
causes zero deletes and retains the bound reservation plus intent for manual
handling. DAD failure or post-apply persistence failure uses normal same-process
exact rollback; if rollback cannot be proven complete, durable state remains
open.

Crash cleanup does not need an unexpired reservation to remove an already-owned
row: reservation authority answered whether creation was allowed, while row
ownership answers whether deletion is allowed. Phase B releases only the
reservation whose ID and operation binding both match the crashed record, and
does so before closing the journal. A crash between those two writes is safe to
replay. A release persistence error leaves the journal open. An expired,
released, or old bound reservation can never authorise a new operation.

### Crash windows

| Window | Durable/live possibilities | Automatic result |
| --- | --- | --- |
| A. Intent durable, crash before create | Intent; row normally absent | `ALREADY_ABSENT` only after full environment and baseline re-proof; zero deletes |
| B. Create succeeds, crash before post-apply write | Intent; matching row may be present | Present is `OWNERSHIP_UNPROVABLE`, zero deletes, manual handling. Absent may close after full re-proof |
| C. Post-apply write succeeds while DAD is settling | Exact evidence; row may become Preferred, remain tentative, duplicate, or disappear | Fingerprint ignores DAD. Exact present row can reconcile; contradiction blocks; absence follows strict absence path |
| D. DAD reaches Preferred, crash before assurance | Exact evidence and settled row | Exact row can reconcile; surrounding baseline is verified after delete |
| E. Assurance succeeds, crash before normal delete | Exact evidence and row | Same as D: at most one exact delete |
| F. Delete succeeds, crash before journal close | Record outstanding; row absent | Zero deletes, then strict absence/baseline closure |
| G. Reservation release or journal-close persistence is interrupted | Atomic old-or-new file at process-death scope | Replay from outstanding state; reservation is released before close so its linkage is not stranded |

No window turns incomplete evidence into permission. Some process crashes are
therefore intentionally manual rather than automatically recoverable.

### Two-process experiment (prepared, not run)

**The real crash experiment has not run.** Consequently
`CRASH_OWNERSHIP_RECONCILIATION` remains `NOT_ATTEMPTED / NONE`.

Phase A is a Recovery Lab-only command. It re-proves the disposable environment,
requires a fresh exact reservation, refuses any target interface carrying a
default route, captures the baseline, acquires the operation lock, binds the
reservation, persists intent, creates exactly one RFC 5737 row, persists its
post-apply identity, waits for real DAD to reach Preferred, flushes its report,
and calls `os._exit(89)`. That bypasses `finally`, `atexit`, destructors, normal
rollback, reservation release and journal closure. A subprocess test verifies
those Python cleanup paths do not run.

Use placeholders resolved from the live environment; never paste host-specific
GUIDs, indices, LUIDs, leases, or environment IDs into source:

```powershell
$LabInterface = "<current disposable DHCP adapter alias>"
$Candidate = "192.0.2.250"
$RunId = "crash-process-death-<unique-id>"

python -m backend.recovery_lab reserve `
    --interface $LabInterface --address $Candidate --prefix-length 24 `
    --attested-by "recovery lab operator" `
    --evidence-reference $RunId

python -m backend.recovery_lab reservations

# FUTURE ELEVATED PHASE A. Expected process exit code: 89.
python -m backend.recovery_lab crash-create `
    --interface $LabInterface --address $Candidate --prefix-length 24 `
    --run-id $RunId
$LASTEXITCODE
```

Between processes, these commands are read-only:

```powershell
python -m backend.recovery_lab crash-status
python -m backend.recovery_lab reservations
python -m backend.recovery_lab inspect
Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias $LabInterface |
    Where-Object IPAddress -eq $Candidate |
    Format-List IPAddress,PrefixLength,InterfaceIndex,PrefixOrigin,SuffixOrigin,AddressState
```

`crash-status` reports whether the kernel operation lock is free, whether the
exact row is present and matches the post-apply evidence, whether the journal is
outstanding, and whether the reservation is still bound/unreleased. It never
creates a missing state directory or lock file and performs no mutation.

Phase B must be a new elevated process:

```powershell
python -m backend.recovery_lab crash-reconcile --interface $LabInterface
python -m backend.recovery_lab crash-status
python -m backend.recovery_lab reservations
python -m backend.recovery_lab inspect
```

Successful Phase B reports `RECONCILED`, exactly one delete, zero outstanding
records, the exact DHCP/network baseline preserved, and the matching reservation
released. Any incomplete or contradictory proof reports `BLOCKED` and does not
broaden cleanup.

## Relationship to the product

The Recovery Lab validates a primitive. `backend/app/recovery_execution.py`
decides, separately and without any I/O, whether SwitchOps could honestly *ask*
an operator to approve using it. `READY` there means "the evidence is good
enough to put the question to a human" — never "this will work", and never
permission to act.

## Gate status

| Gate | Question | Status |
| --- | --- | --- |
| 1 | Does the IP Helper create/DAD/delete primitive work on this platform? | **PROVEN** |
| 2 | Does it coexist with a DHCP-controlled primary on the same interface? | **PROVEN** |
| 3 | Can SwitchOps require, validate, bind and consume reservation authority before mutation? | **PROVEN** |
| 3a | Is there an authoritative collision-safe address on the *real* management prefix? | NOT AVAILABLE |
| Crash ownership prerequisite | Can a new process prove and reconcile a dead process's exact row? | NOT ATTEMPTED; mechanism ready |
| 4 | Is there an operator-approved elevated production executor? | NOT IMPLEMENTED |
| 5 | Does physical acceptance have live Catalyst topology? | BLOCKED |

Gate 1 was proven by an elevated run on 2026-08-27 against the isolated
statically addressed host-only adapter: create succeeded, real (non-optimistic)
DAD settled to preferred in about 3.5 seconds, an explicit `/24` was honoured,
exact-object deletion succeeded, the address was confirmed absent, and the
journal cleared itself.

That run used `MANUAL/MANUAL` addressing, so it proved the primitive and
nothing about DHCP.

## Gate 2: same-interface DHCP coexistence (measured)

An elevated run on a **disposable VirtualBox DHCP adapter** -- never a
production one -- then answered the DHCP question directly. Measured:

* the temporary RFC 5737 address reached **Preferred** after real duplicate
  address detection (~3.5s), not optimistic DAD;
* it coexisted as an **independent `MANUAL/MANUAL` row**, distinct from the
  lease;
* the DHCP-controlled primary stayed **`DHCP/DHCP`** and **Preferred**;
* its **finite lease survived and continued counting down**;
* the requested **`/24` on-link route** appeared;
* **default routes and DNS were unchanged**, as was surrounding interface
  state;
* the **exact** temporary row was deleted, its absence verified, and the DHCP
  primary confirmed to have survived cleanup.

That settles the question the whole primitive rests on: unlike the standard
configuration API, adding this row does not disturb a working DHCP lease. The
Windows DHCP/static coexistence setting governs that standard API, so recovery
planning no longer treats it as a blocker.

**This does not validate production recovery.** It was measured on one
disposable adapter, on one platform class, with no production adapter involved.
Crash-ownership reconciliation remains unexercised, no authoritative
collision-safe address exists for the real management prefix, and no executor is
implemented -- so the product remains planning-only and the real recovery plan
remains blocked.

## Gate 3: reservation authority (measured)

Gates 1 and 2 asked what the Windows primitive does. Gate 3 asks something the
primitive cannot answer: *before* an address is created, what evidence proves
that this specific candidate is authorised for temporary use?

It is not an address-discovery problem. It is an authority problem, and the
failure it exists to prevent is a single bad inference:

> we did not see a conflict  →  we are authorised to use this address

### Two independent controls

| | Question it answers | When | Who answers |
| --- | --- | --- | --- |
| **Authority** | May we use this exact address? | Before any mutation | A person or system that took responsibility for keeping it free |
| **DAD** | Did Windows detect a duplicate at creation time? | At creation | The operating system, on the wire |

Neither substitutes for the other. Authority without DAD would trust a record
over reality. DAD without authority would turn "nothing objected in three
seconds" into "we are allowed" — which is exactly the inference above, and no
amount of runtime probing repairs it. A powered-off host, a firewall that drops
ICMP, and a genuinely free address are indistinguishable to any probe.

So none of the following are ever authority, and each is enumerated in
`REJECTED_COLLISION_EVIDENCE` rather than merely left off the accepted list:
ICMP silence, ARP silence, a stale ARP entry, an apparently unused address, a
free-looking DHCP range, discovery confidence, a model inference, a network
description, or DAD having found no duplicate.

### Accepted authority classes

Each is a *positive claim about the specific address*, and each names exactly
one kind of attestor so that one class cannot be filed as another.

| Authority | Attestor | Means | Limitation |
| --- | --- | --- | --- |
| `OPERATOR_DECLARED` | `NAMED_OPERATOR` | An identified operator with authority over this network explicitly attests that this exact address is reserved for this recovery operation | It is a declaration, not an observation. It is only as good as the person's actual authority over the network, and it expires. |
| `DHCP_EXCLUSION_ATTESTED` | `DHCP_SERVICE_RECORD` | The DHCP service is attested to exclude this address from its pool | Covers only what DHCP would hand out. A statically configured host inside the excluded range is invisible to it. |
| `INFRASTRUCTURE_ATTESTED` | `IPAM_RECORD` | IPAM or a controller attests the address is reserved | Only as current as the record. An IPAM that nobody updates is a stale claim, not a live one. |
| `LAB_HARNESS_RESERVED` | `LAB_HARNESS` | The Recovery Lab reserved this address inside a disposable environment it created | Real authority there and **none at all** anywhere else. Rejected outright whenever the scope is production. |

`OPERATOR_DECLARED` explicitly does **not** mean "the operator thinks this
address looks free". Operator guesswork fails closed like every other absence of
evidence. The declaration has to be deliberate, scoped, attributable and fresh,
which is what the required fields enforce.

### What an attestation must carry

Each field closes one specific way of turning a weaker claim into authority:

| Field | Closes |
| --- | --- |
| `address` | Evidence for one address authorising another |
| `prefixLength`, `managementPrefix` | An on-link route other than the one attested |
| `authority`, `attestorType` | One class of evidence masquerading as another |
| `attestedBy` | An anonymous claim nobody is accountable for |
| `scope`, `networkScopeId` | A lab or another network's reservation authorising this one |
| `evidenceReference` | A claim that cannot be checked afterwards |
| `declaredAt`, `reservedUntil` | Stale or not-yet-valid authority |
| `planBinding` (optional) | Replay of one operation's reservation into another |

Freshness is two independent limits, because they fail differently. The
attestation carries its own `reservedUntil`, and once that passes it is not
weaker authority — it is none. Separately, `declaredAt` must be in the past and
recent enough that somebody has looked at the claim within the re-attestation
window.

### Structural checks that no authority can override

A candidate is refused regardless of who attests it when it is malformed, not
IPv4, outside the target prefix, the network or broadcast address, the gateway,
the target device's own address, already held by this host, or already owned by
another in-flight recovery operation.

### Three identities, deliberately not merged

| Question | Answered by |
| --- | --- |
| Is this Windows interface the disposable environment we think it is? | The VirtualBox GUID → Windows `InterfaceGuid` chain (Gate 2) |
| Are we authorised to use this exact candidate address in this exact prefix? | The reservation (Gate 3) |
| Is this exact `MIB_UNICASTIPADDRESS_ROW` ours to delete? | The journal plus `InterfaceLuid`/index/address/prefix |

A reservation does not make an interface ours, environment ownership does not
reserve an address, and neither decides which row may be deleted.

### The isolated experiment

`gate3` adds the authority prerequisite *in front of* the already-proven Gate 2
runner rather than reimplementing it — a second copy of create/DAD/verify/
rollback would be a second thing to get wrong and would prove nothing new.

```
python -m backend.recovery_lab reserve --interface "<disposable alias>" \
    --address 192.0.2.250 --prefix-length 24 \
    --attested-by "recovery lab harness" \
    --evidence-reference "gate3-isolated-experiment"

python -m backend.recovery_lab gate3 --interface "<disposable alias>" \
    --address 192.0.2.250 --prefix-length 24 --run-id gate3-run-0001
```

The reservation source is the harness's own registry, stored beside the journal
under the ignored `state/` directory. It may only reserve RFC 5737
documentation addresses — reserving anything else would be a claim about
somebody's real network, which this harness has no standing to make — and it
issues `LAB_HARNESS_RESERVED` at `DISPOSABLE_LAB_ENVIRONMENT` scope, which the
product assessor rejects outright for production. The record is validated by the
*product* assessor, not by the lab, so the harness cannot bless its own
attestation.

Sequence: prove the environment by GUID chain; load the live reservation for the
exact candidate; verify its type, attestor, freshness, address, prefix,
environment and operation binding; reject structural conflicts; bind it to this
run; then hand over to the Gate 2 runner for journal, create, real DAD, on-link
verification, DHCP preservation, exact-row deletion and baseline restoration;
then release the reservation.

Every refusal happens before the first create, so each of these leaves
`creates: 0`:

`ENVIRONMENT_NOT_AUTHORISED`, `AUTHORITY_ABSENT`, `AUTHORITY_STALE`,
`AUTHORITY_INVALID`, `AUTHORITY_SCOPE_MISMATCH`, `CANDIDATE_NOT_RESERVED`,
`CANDIDATE_STRUCTURALLY_UNSAFE`.

If a reserved address nonetheless comes back `DUPLICATE`, the run reports
`AUTHORITY_CONTRADICTED_BY_DAD`: the record and the network disagree, and the
record does not win. It cleans up exactly its own row, and it does **not** try a
different address. There is no candidate cycling anywhere in this design.

A reservation is bound to one run before anything is created, so a run that
crashes leaves a record belonging to a finished operation. The next run, with a
new id, gets `RESERVATION_BINDING_MISMATCH`. A stale outstanding reservation
never broadens into permission for a new operation.

### What was measured

Both halves of the gate were exercised elevated, on the harness-owned disposable
DHCP environment. A gate that had only ever been observed succeeding would not
have been shown to be a gate, so the refusal was measured first.

**Negative observation — elevated, disposable environment, no reservation:**

```
test authority : DISPOSABLE_DHCP_ENVIRONMENT
evaluator      : GATE3_RESERVATION_AUTHORITY
outcome        : AUTHORITY_ABSENT
creates        : 0
restored       : True

PASS environment-authority
FAIL reservation-authority   NO_RESERVATION
```

The run held privilege sufficient to mutate the adapter and declined to use it.
Its evidence stated that SwitchOps would not select an address by probing, and
that silence is not proof an address is free. **No authoritative reservation →
zero creates**, enforced at the privilege level where it matters.

**Positive observation — elevated, disposable environment, valid harness
reservation:** the reservation was bound to the exact address, a `/24` prefix
and the disposable environment, issued as `LAB_HARNESS_RESERVED` by a
`LAB_HARNESS` attestor, fresh and time-limited. The run used a new run id.

```
evaluator      : GATE3_RESERVATION_AUTHORITY
outcome        : SUCCESS
creates        : 1
restored       : True
dad            : PREFERRED
coexistence    : SUCCESS

PASS environment-authority     PASS on-link-prefix
PASS reservation-authority     PASS coexistence
PASS reservation-binding       PASS delete
PASS authority                 PASS rollback-verify
PASS dhcp-baseline             PASS baseline-restored
PASS journal-intent            PASS reservation-release
PASS create                    PASS dad
```

Measured: reservation authority proven *before* mutation; the reservation bound
to that exact run; exactly one temporary RFC 5737 address created; Windows DAD
reaching Preferred in about 3.5 seconds; the disposable DHCP primary staying
`DHCP/DHCP` and Preferred with its finite lease still counting down; the
temporary address present independently as `MANUAL/MANUAL`; the expected `/24`
connected route appearing; default routes and DNS unchanged; exact-row deletion
succeeding and the address confirmed absent; the DHCP baseline restored; the
disposable reservation released.

### Authority and DAD, again

The successful run reached Preferred, and that changes nothing about what
authority is for:

> **Authority answers whether SwitchOps may use this exact address.**
> **DAD answers whether Windows detected a duplicate when that authorised
> address was created.**

Neither replaces the other. The negative run shows why: it never got as far as
DAD, because there was nothing authorising it to create anything to test.
`DAD_FOUND_NO_DUPLICATE` remains enumerated in `REJECTED_COLLISION_EVIDENCE`.

### Status

Gate 3 is **VALIDATED**, environment `DISPOSABLE_DHCP_ADAPTER`.

**This does not provide a production recovery address and does not validate
production recovery.** Two different questions are involved, and only the first
has been answered:

| Question | Answer |
| --- | --- |
| Can SwitchOps correctly require, validate, bind and consume authoritative reservation evidence before mutation? | Measured. Yes. |
| Does this particular production recovery plan currently possess valid reservation authority for a specific address? | No. Nothing supplies one. |

So the live production plan remains `BLOCKED` on
`COLLISION_SAFE_ADDRESS_UNAVAILABLE`, and a validated mechanism makes that
blocker more trustworthy rather than removable — refusing when there is nothing
to consume is precisely the behaviour that was measured. The reservation the
successful run consumed was `LAB_HARNESS_RESERVED` at disposable scope, which
the product assessor rejects outright for production.

No production-scoped reservation can be inferred from historical topology, from
the subnet a Catalyst management interface once used, or from anything else that
is not a positive claim somebody is accountable for. `production_recovery_validated`
remains false on two counts: a deliberate crash has never been exercised, and no
production adapter has ever been touched. No executor exists.

## Disposable DHCP environment

Gate 2 needs a Windows interface whose primary address is genuinely controlled
by the DHCP client, isolated from production. This machine can build one from
VirtualBox host-only networking:

```powershell
# All of these require an elevated shell.
python -m backend.recovery_lab provision                  # creates the adapter + DHCP server
python -m backend.recovery_lab environments               # what we created
python -m backend.recovery_lab experiment `
    --interface "VirtualBox Host-Only Ethernet Adapter #2" `
    --address 192.0.2.250 --prefix-length 24 `
    --allow "VirtualBox Host-Only Ethernet Adapter #2" `
    --dhcp-coexistence
python -m backend.recovery_lab teardown `
    --adapter "VirtualBox Host-Only Ethernet Adapter #2"
```

`provision` builds `192.168.57.0/24` -- inside VirtualBox's default permitted
host-only range and distinct from the pre-existing `192.168.56.0/24` segment,
the production LAN, and the Catalyst management prefix. The temporary recovery
address stays in RFC 5737 space, so the experiment models the real shape: a DHCP
primary on one prefix, a temporary management address on another.

One known uncertainty: `VBoxNetDHCP` may only run while a VM is attached to the
network. If the adapter falls back to APIPA, `provision` reports
`DHCP_LEASE_NOT_OBTAINED` rather than continuing -- an adapter that never got a
lease cannot answer the question, and a confident result from it would be
worthless.

### Why a DHCP interface is allowed here and nowhere else

The guard was not relaxed to "DHCP interfaces are fine now". An interface may be
DHCP-controlled *and* eligible only when this harness provisioned it, recorded
it at the time, and the live VirtualBox GUID to Windows `InterfaceGuid` chain is
still unique and current. Alias and ifIndex are observations, not environment
identity. Provenance is the authority; `--allow` on its own is not. Production
Ethernet has no record, and would still be refused for carrying a default route
even if it had one.

## What DHCP preservation means

Not "the same IP is still there". A primary address can survive as a string
while having quietly stopped being a lease, which is exactly the failure mode
that makes `EnableStatic` unsafe. Preservation is asserted on properties:
`PrefixOrigin`/`SuffixOrigin` still `DHCP`, DAD still preferred, prefix length
unchanged, and a **finite** lifetime that is still counting down. Lease time
naturally decreases, so it is never compared for equality -- only an implausible
*increase* is flagged, since that suggests the address was released and
re-acquired rather than left alone.
