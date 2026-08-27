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

* that a created address survives the creating process exiting -- Microsoft
  documents it, and successful transactions cleared their own journals, but a
  deliberate crash has not been exercised;
* anything at all on a production adapter, by design.

`production_recovery_validated` therefore remains **false**: it is the
conjunction of every required capability, and crash-ownership reconciliation is
still `NOT_ATTEMPTED`.

## Running it

```powershell
python -m backend.recovery_lab inspect          # read-only, no elevation needed
python -m backend.recovery_lab restart-check    # do we still own a temporary address?

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

## Ownership and the journal

Microsoft states the address "exists only as long as the adapter object
exists" — it survives the death of the creating process and is destroyed by
reboot, NIC reset, and some PnP events. A harness that crashed between create
and delete would therefore leave an address behind with nothing recording who
created it.

So the claim is journalled **before** the address is created, and cleared only
once the address is confirmed gone. Ownership matches on interface LUID,
interface index, address, and prefix length together; any mismatch means "not
ours" and nothing is removed. There is no "remove everything in this prefix"
path, because that would eventually delete somebody else's address during a
rollback.

`restart-check` distinguishes two outcomes that look similar and are not:

* `OWNED_STATE_DETECTED` — our address is still present and must be removed;
* `OWNED_STATE_ABSENT` — the record is stale because Windows already reclaimed
  the address on reboot or NIC reset.

A corrupt journal raises rather than reading as "nothing owned".

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
| 3 | Is there an authoritative collision-safe address on the real management prefix? | NOT AVAILABLE |
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
collision-safe address exists, and no executor is implemented -- so the product
remains planning-only and the real recovery plan remains blocked.

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
it at the time, and that record still matches the machine -- same alias, same
interface index, recent enough to still describe reality. Provenance is the
authority; `--allow` on its own is not. Production Ethernet has no record, and
would still be refused for carrying a default route even if it had one.

## What DHCP preservation means

Not "the same IP is still there". A primary address can survive as a string
while having quietly stopped being a lease, which is exactly the failure mode
that makes `EnableStatic` unsafe. Preservation is asserted on properties:
`PrefixOrigin`/`SuffixOrigin` still `DHCP`, DAD still preferred, prefix length
unchanged, and a **finite** lifetime that is still counting down. Lease time
naturally decreases, so it is never compared for equality -- only an implausible
*increase* is flagged, since that suggests the address was released and
re-acquired rather than left alone.
