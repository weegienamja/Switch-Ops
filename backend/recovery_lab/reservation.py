"""A narrowly-scoped reservation authority for disposable Gate 3 experiments.

Gate 3 asks a question the earlier gates never had to: *before* an address is
created, what evidence proves the specific candidate is authorised for temporary
use? A real answer needs a real authority source, and the harness cannot borrow
production infrastructure to get one. So it issues its own -- and the whole
design of this module is about making sure that authority can never travel
outside the disposable environment it was issued for.

A lab reservation is a positive claim: *candidate C, in prefix P, is reserved
for disposable environment E until time T by authority A*. It is not a report
that nothing answered a ping. That distinction is the point of Gate 3, so the
record deliberately has nowhere to put probe results.

This is harness state. It is stored under the already-ignored ``state``
directory, it is never read by the SwitchOps runtime, and the authority type it
issues (``LAB_HARNESS_RESERVED``) is rejected outright by the product assessor
whenever the scope is production.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
import ipaddress
import json
import os
import secrets
import uuid

from .ownership_lock import exclusive

#: Bumped whenever the on-disk shape changes. A record written by a future
#: version must not be silently reinterpreted by an older one.
SCHEMA_VERSION = 1

#: The only authority type this module may issue, and the only one the product
#: assessor accepts exclusively inside a disposable environment.
LAB_AUTHORITY = "LAB_HARNESS_RESERVED"
LAB_ATTESTOR = "LAB_HARNESS"
LAB_SCOPE = "DISPOSABLE_LAB_ENVIRONMENT"

#: Short by design. A reservation that outlives the experiment it was issued for
#: is exactly the stale authority Gate 3 exists to reject, and a lab has no
#: reason to hold one open for longer than a run takes.
DEFAULT_VALIDITY = timedelta(minutes=30)

#: Documentation space (RFC 5737). A lab reservation for anything else would be
#: a claim about somebody's real network, which this harness has no standing to
#: make.
DOCUMENTATION_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)

IssueOutcome = Literal[
    "ISSUED",
    "CANDIDATE_MALFORMED",
    "CANDIDATE_OUTSIDE_DOCUMENTATION_SPACE",
    "CANDIDATE_OUTSIDE_PREFIX",
    "CANDIDATE_STRUCTURALLY_UNSAFE",
    "CANDIDATE_ALREADY_RESERVED",
    "ENVIRONMENT_NOT_NAMED",
]

_ANY_BINDING = object()


class ReservationStateError(RuntimeError):
    """A reservation changed, vanished, or was replayed across operations."""


@dataclass
class LabReservation:
    """One positive, time-bounded claim about one address in one environment."""

    reservation_id: str
    schema_version: int
    #: The exact address this authorises. Nothing else.
    address: str
    prefix_length: int
    #: The prefix the address must be on-link within.
    target_prefix: str
    #: Which disposable environment. Authority does not cross environments.
    environment_id: str
    authority: str
    attestor_type: str
    attested_by: str
    scope: str
    evidence_reference: str
    declared_at: str
    reserved_until: str
    #: Optional operation binding, set when the reservation is claimed by a run
    #: so it cannot afterwards be replayed into a different one.
    operation_binding: str | None = None
    released_at: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def is_released(self) -> bool:
        return self.released_at is not None


def new_reservation_id() -> str:
    return f"gate3-res-{secrets.token_hex(6)}"


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def _structurally_safe(
    candidate: ipaddress.IPv4Address,
    network: ipaddress.IPv4Network,
    gateway: str | None,
) -> str | None:
    """Reasons an address is unusable regardless of who attests it."""
    if candidate in (network.network_address, network.broadcast_address):
        return "the network or broadcast address"
    if gateway and str(candidate) == gateway:
        return "the gateway address"
    return None


class LabReservationRegistry:
    """Durable, local-only store of disposable Gate 3 reservations."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def lock_path(self) -> Path:
        return self.path.with_name(self.path.name + ".lock")

    # --- persistence -------------------------------------------------------

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # An unreadable registry must never look like "nothing is reserved",
            # because that is the state in which an experiment would refuse.
            # Refusing loudly is correct; refusing quietly is not.
            raise RuntimeError(
                f"The Gate 3 reservation registry at {self.path.name} is "
                "unreadable. Resolve it manually before running an experiment."
            )
        if not isinstance(payload, list) or any(
            not isinstance(record, dict) for record in payload
        ):
            raise RuntimeError(
                f"The Gate 3 reservation registry at {self.path.name} has an "
                "invalid shape. Resolve it manually before running an experiment."
            )
        for record in payload:
            version = record.get("schema_version")
            if version != SCHEMA_VERSION:
                raise RuntimeError(
                    f"A reservation in {self.path.name} declares schema version "
                    f"{version!r}, but this harness understands "
                    f"{SCHEMA_VERSION}. Refusing to reinterpret it."
                )
        return payload

    def _write(self, records: list[dict]) -> None:
        # The reservation binding is part of the crash evidence. Give it the
        # same process-death semantics as the journal: a unique temporary file,
        # flush/fsync, then atomic replacement. This deliberately does not claim
        # power-loss or filesystem-crash durability.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        )
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(records, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    # --- reads -------------------------------------------------------------

    def all(self) -> list[LabReservation]:
        return [LabReservation(**record) for record in self._read()]

    def find(
        self, *, address: str, environment_id: str, now: datetime
    ) -> LabReservation | None:
        """The live reservation for exactly this address in this environment.

        Released and expired records are not returned. They are not weaker
        authority; they are none, and returning one would leave the caller to
        remember to check.
        """
        matches = []
        for item in self.all():
            if item.address != address or item.environment_id != environment_id:
                continue
            if item.is_released:
                continue
            if datetime.fromisoformat(item.reserved_until) <= now:
                continue
            matches.append(item)
        if len(matches) > 1:
            raise ReservationStateError(
                "More than one live reservation claims the same address and "
                "environment; authority is ambiguous."
            )
        return matches[0] if matches else None

    # --- writes ------------------------------------------------------------

    def issue(
        self,
        *,
        address: str,
        target_prefix: str,
        environment_id: str,
        attested_by: str,
        evidence_reference: str,
        now: datetime | None = None,
        validity: timedelta = DEFAULT_VALIDITY,
        gateway: str | None = None,
        operation_binding: str | None = None,
    ) -> tuple[IssueOutcome, LabReservation | None, list[str]]:
        """Record a positive claim, or refuse to make one.

        Every refusal here happens before any reservation exists, so an
        experiment that later finds no reservation refuses for the right reason.
        """
        moment = now or datetime.now(timezone.utc)
        evidence: list[str] = []

        if not environment_id or len(environment_id) < 3:
            return (
                "ENVIRONMENT_NOT_NAMED",
                None,
                ["A reservation must name the disposable environment it covers."],
            )

        try:
            network = ipaddress.ip_network(target_prefix, strict=False)
            candidate = ipaddress.ip_address(address)
        except ValueError:
            return ("CANDIDATE_MALFORMED", None, ["The address or prefix is malformed."])
        if not isinstance(candidate, ipaddress.IPv4Address) or not isinstance(
            network, ipaddress.IPv4Network
        ):
            return (
                "CANDIDATE_MALFORMED",
                None,
                ["Only IPv4 lab reservations are supported."],
            )

        if not any(candidate in item for item in DOCUMENTATION_NETWORKS):
            return (
                "CANDIDATE_OUTSIDE_DOCUMENTATION_SPACE",
                None,
                [
                    f"{address} is outside RFC 5737 documentation space. This "
                    "harness may only reserve addresses that cannot belong to a "
                    "real network."
                ],
            )
        if candidate not in network:
            return (
                "CANDIDATE_OUTSIDE_PREFIX",
                None,
                [f"{address} is not inside {target_prefix}."],
            )

        unsafe = _structurally_safe(candidate, network, gateway)
        if unsafe is not None:
            return (
                "CANDIDATE_STRUCTURALLY_UNSAFE",
                None,
                [f"{address} is {unsafe} for {target_prefix}."],
            )

        reservation = LabReservation(
            reservation_id=new_reservation_id(),
            schema_version=SCHEMA_VERSION,
            address=address,
            prefix_length=network.prefixlen,
            target_prefix=str(network),
            environment_id=environment_id,
            authority=LAB_AUTHORITY,
            attestor_type=LAB_ATTESTOR,
            attested_by=attested_by,
            scope=LAB_SCOPE,
            evidence_reference=evidence_reference,
            declared_at=_now_iso(moment),
            reserved_until=_now_iso(moment + validity),
            operation_binding=operation_binding,
        )
        with exclusive(self.lock_path):
            records = self._read()
            existing = [LabReservation(**record) for record in records]
            if any(
                item.address == address
                and item.environment_id == environment_id
                and not item.is_released
                and datetime.fromisoformat(item.reserved_until) > moment
                for item in existing
            ):
                return (
                    "CANDIDATE_ALREADY_RESERVED",
                    None,
                    [
                        f"{address} is already reserved in {environment_id}. Two "
                        "live reservations for one address would make ownership "
                        "ambiguous."
                    ],
                )
            records.append(asdict(reservation))
            self._write(records)
        evidence.append(
            f"{address}/{network.prefixlen} reserved in {environment_id} until "
            f"{reservation.reserved_until}."
        )
        return ("ISSUED", reservation, evidence)

    def bind(self, reservation_id: str, operation_id: str) -> None:
        """Tie a reservation to one operation, once that operation exists.

        After this, the reservation authorises that operation and no other, so a
        later run cannot inherit it.
        """
        with exclusive(self.lock_path):
            records = self._read()
            record = _one_reservation(records, reservation_id)
            current = record.get("operation_binding")
            if record.get("released_at") is not None:
                raise ReservationStateError("A released reservation cannot be bound.")
            if current not in (None, operation_id):
                raise ReservationStateError(
                    f"Reservation {reservation_id} is already bound to another "
                    "operation and cannot be inherited."
                )
            record["operation_binding"] = operation_id
            self._write(records)

    def claim(
        self,
        reservation_id: str,
        operation_id: str,
        *,
        expected_binding: str | None,
        now: datetime,
    ) -> None:
        """Atomically transfer the observed reservation into one operation.

        The compare-and-set closes the interval in which two Phase A processes
        could both assess the same unbound record and then overwrite each
        other's binding. Exactly one can change the value it observed.
        """
        with exclusive(self.lock_path):
            records = self._read()
            record = _one_reservation(records, reservation_id)
            if record.get("operation_binding") != expected_binding:
                raise ReservationStateError(
                    "The reservation binding changed after authority was assessed."
                )
            if record.get("released_at") is not None:
                raise ReservationStateError("The reservation was already released.")
            if datetime.fromisoformat(record["reserved_until"]) <= now:
                raise ReservationStateError("The reservation expired before binding.")
            record["operation_binding"] = operation_id
            self._write(records)

    def release(
        self,
        reservation_id: str,
        *,
        now: datetime | None = None,
        expected_binding: str | None | object = _ANY_BINDING,
    ) -> bool:
        """Close a reservation. Released records are kept, not deleted.

        The audit trail of what was authorised is more useful than a tidy file,
        and a released record still fails closed everywhere it is read.
        """
        moment = now or datetime.now(timezone.utc)
        with exclusive(self.lock_path):
            records = self._read()
            record = _one_reservation(records, reservation_id)
            if (
                expected_binding is not _ANY_BINDING
                and record.get("operation_binding") != expected_binding
            ):
                return False
            if record.get("released_at") is not None:
                return False
            record["released_at"] = _now_iso(moment)
            self._write(records)
            return True


def _one_reservation(records: list[dict], reservation_id: str) -> dict:
    matches = [
        record
        for record in records
        if record.get("reservation_id") == reservation_id
    ]
    if len(matches) != 1:
        raise ReservationStateError(
            f"Reservation {reservation_id} is not present exactly once."
        )
    return matches[0]


def to_product_reservation(reservation: LabReservation) -> dict:
    """Render a lab record in the shape the product assessor validates.

    The product assessor is the one that decides whether this authorises
    anything. Handing it a plain payload rather than a pre-blessed object keeps
    the lab from being able to assert its own conclusion.
    """
    return {
        "address": reservation.address,
        "prefixLength": reservation.prefix_length,
        "managementPrefix": reservation.target_prefix,
        "authority": reservation.authority,
        "attestorType": reservation.attestor_type,
        "attestedBy": reservation.attested_by,
        "scope": reservation.scope,
        "networkScopeId": reservation.environment_id,
        "evidenceReference": reservation.evidence_reference,
        "declaredAt": reservation.declared_at,
        "reservedUntil": reservation.reserved_until,
        "planBinding": reservation.operation_binding,
    }
