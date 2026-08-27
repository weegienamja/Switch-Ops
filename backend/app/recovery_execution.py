"""Pure safety contracts for a future Windows management-path recovery executor.

This module deliberately performs no I/O and exposes no mutation primitive.  It
defines the authority boundary, transaction contract, progressive verification,
ownership-safe rollback selection, and restart behavior that an executor would
have to satisfy before it could be added to SwitchOps.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Mapping, Sequence

from pydantic import BaseModel, Field


AuthorityLevel = Literal[
    "LEVEL_0_READ_ONLY",
    "LEVEL_1_SESSION_RECOVERY",
    "LEVEL_2_EPHEMERAL_HOST_NETWORK",
    "LEVEL_3_PERSISTENT_HOST_NETWORK",
    "LEVEL_4_DEVICE_CHANGE_ASSURANCE",
]
RecoveryPolicy = Literal["MANUAL_ONLY", "OPERATOR_APPROVED", "POLICY_AUTOMATIC"]
VerificationStatus = Literal["PASS", "FAIL", "WAIT", "NOT_REACHED"]


class AuthorityDefinition(BaseModel):
    level: AuthorityLevel
    supported: bool
    summary: str


class RecoveryAuthorityContract(BaseModel):
    current_policy: RecoveryPolicy = Field(alias="currentPolicy")
    future_policy_ceiling: RecoveryPolicy = Field(alias="futurePolicyCeiling")
    required_level: AuthorityLevel = Field(alias="requiredLevel")
    administrator_required: bool = Field(alias="administratorRequired")
    explicit_operator_approval_required: bool = Field(
        alias="explicitOperatorApprovalRequired"
    )
    automatic_execution_enabled: bool = Field(alias="automaticExecutionEnabled")
    levels: list[AuthorityDefinition]

    model_config = {"populate_by_name": True}


class RecoveryPrimitiveAssessment(BaseModel):
    selected_primitive: Literal["NONE"] = Field(alias="selectedPrimitive")
    future_candidate: Literal["IP_HELPER_EPHEMERAL_UNICAST"] = Field(
        alias="futureCandidate"
    )
    candidate_status: Literal["ISOLATED_VALIDATION_REQUIRED"] = Field(
        alias="candidateStatus"
    )
    rationale: list[str]

    model_config = {"populate_by_name": True}


class CollisionSafetyContract(BaseModel):
    required_assurance: Literal["AUTHORITATIVE_DEDICATED_RESERVATION"] = Field(
        alias="requiredAssurance"
    )
    accepted_evidence: list[str] = Field(alias="acceptedEvidence")
    rejected_evidence: list[str] = Field(alias="rejectedEvidence")
    freshness_required: bool = Field(alias="freshnessRequired")

    model_config = {"populate_by_name": True}


class RecoveryOwnershipContract(BaseModel):
    identity_fields: list[str] = Field(alias="identityFields")
    preexisting_object_must_be_absent: bool = Field(
        alias="preexistingObjectMustBeAbsent"
    )
    exact_post_apply_fingerprint_required: bool = Field(
        alias="exactPostApplyFingerprintRequired"
    )
    broad_cleanup_allowed: bool = Field(alias="broadCleanupAllowed")
    ambiguity_behavior: Literal["REQUIRE_OPERATOR_RECONCILIATION"] = Field(
        alias="ambiguityBehavior"
    )

    model_config = {"populate_by_name": True}


class RecoveryTransactionContract(BaseModel):
    journal_required_before_apply: bool = Field(alias="journalRequiredBeforeApply")
    sequence: list[str]
    captured_state: list[str] = Field(alias="capturedState")
    preservation_invariants: list[str] = Field(alias="preservationInvariants")
    rollback_triggers: list[str] = Field(alias="rollbackTriggers")
    restart_behavior: str = Field(alias="restartBehavior")

    model_config = {"populate_by_name": True}


class ExecutionGateDecision(BaseModel):
    allowed: Literal[False] = False
    disposition: Literal["BLOCKED", "NOT_IMPLEMENTED"]
    reasons: list[str]


class RecoveryExecutionArchitecture(BaseModel):
    mode: Literal["PLANNING_ONLY"] = "PLANNING_ONLY"
    executor_implemented: Literal[False] = Field(False, alias="executorImplemented")
    approval_available: Literal[False] = Field(False, alias="approvalAvailable")
    authority: RecoveryAuthorityContract
    primitive: RecoveryPrimitiveAssessment
    collision_safety: CollisionSafetyContract = Field(alias="collisionSafety")
    ownership: RecoveryOwnershipContract
    transaction: RecoveryTransactionContract
    gate: ExecutionGateDecision

    model_config = {"populate_by_name": True}


def assess_execution_gate(
    *,
    plan_status: str,
    binding_valid: bool,
    blocker_codes: Sequence[str] = (),
    incomplete_transaction: bool = False,
) -> ExecutionGateDecision:
    """Fail closed even for an otherwise READY plan: no executor exists."""
    reasons: list[str] = []
    if plan_status != "READY":
        reasons.append("PLAN_NOT_READY")
    reasons.extend(f"PLAN_BLOCKER:{code}" for code in blocker_codes)
    if not binding_valid:
        reasons.append("PLAN_BINDING_CHANGED")
    if incomplete_transaction:
        reasons.append("INCOMPLETE_TRANSACTION_REQUIRES_RECONCILIATION")
    reasons.append("EXECUTOR_NOT_IMPLEMENTED")
    return ExecutionGateDecision(
        disposition="BLOCKED" if len(reasons) > 1 else "NOT_IMPLEMENTED",
        reasons=reasons,
    )


def build_planning_architecture(
    *, plan_status: str, blocker_codes: Sequence[str]
) -> RecoveryExecutionArchitecture:
    """Describe the supported Stage 3 boundary without granting mutation power."""
    return RecoveryExecutionArchitecture(
        authority=RecoveryAuthorityContract(
            currentPolicy="MANUAL_ONLY",
            futurePolicyCeiling="OPERATOR_APPROVED",
            requiredLevel="LEVEL_2_EPHEMERAL_HOST_NETWORK",
            administratorRequired=True,
            explicitOperatorApprovalRequired=True,
            automaticExecutionEnabled=False,
            levels=[
                AuthorityDefinition(
                    level="LEVEL_0_READ_ONLY",
                    supported=True,
                    summary="Observe and diagnose without changing host or device state.",
                ),
                AuthorityDefinition(
                    level="LEVEL_1_SESSION_RECOVERY",
                    supported=True,
                    summary=(
                        "Reconnect the serialized SwitchOps session without changing "
                        "networking."
                    ),
                ),
                AuthorityDefinition(
                    level="LEVEL_2_EPHEMERAL_HOST_NETWORK",
                    supported=False,
                    summary="Future elevated, explicitly approved, ownership-bound host recovery.",
                ),
                AuthorityDefinition(
                    level="LEVEL_3_PERSISTENT_HOST_NETWORK",
                    supported=False,
                    summary=(
                        "Persistent DHCP, DNS, gateway, adapter, and route changes "
                        "are unsupported."
                    ),
                ),
                AuthorityDefinition(
                    level="LEVEL_4_DEVICE_CHANGE_ASSURANCE",
                    supported=True,
                    summary=(
                        "Device writes remain governed by the separate Change Assurance "
                        "workflow."
                    ),
                ),
            ],
        ),
        primitive=RecoveryPrimitiveAssessment(
            selectedPrimitive="NONE",
            futureCandidate="IP_HELPER_EPHEMERAL_UNICAST",
            candidateStatus="ISOLATED_VALIDATION_REQUIRED",
            rationale=[
                "The standard Windows address commands can disable DHCP on this interface.",
                (
                    "Enabling DHCP/static coexistence is an interface-wide prerequisite, "
                    "not a recovery side effect."
                ),
                (
                    "The IP Helper candidate is volatile and exactly addressable, and "
                    "DHCP preservation has been measured on a disposable DHCP adapter: "
                    "the lease survived unchanged. It is still unproven across the "
                    "production adapter and driver class."
                ),
            ],
        ),
        collisionSafety=CollisionSafetyContract(
            requiredAssurance="AUTHORITATIVE_DEDICATED_RESERVATION",
            acceptedEvidence=[
                "A current reservation or exclusion explicitly dedicated to SwitchOps recovery.",
                "A current authoritative address inventory tied to the bound adapter and plan.",
            ],
            rejectedEvidence=[
                "Historical address use",
                "Failed ping",
                "Absent ARP or neighbor entry",
                "Uncorroborated operator guess",
            ],
            freshnessRequired=True,
        ),
        ownership=RecoveryOwnershipContract(
            identityFields=[
                "plan ID",
                "operation ID",
                "adapter stable identity and interface LUID",
                "address and prefix",
                "creation time",
                "previous-state fingerprint",
                "exact post-apply object fingerprint",
            ],
            preexistingObjectMustBeAbsent=True,
            exactPostApplyFingerprintRequired=True,
            broadCleanupAllowed=False,
            ambiguityBehavior="REQUIRE_OPERATOR_RECONCILIATION",
        ),
        transaction=RecoveryTransactionContract(
            journalRequiredBeforeApply=True,
            sequence=[
                "PRE_FLIGHT",
                "REVALIDATE_PLAN_BINDING",
                "SNAPSHOT_RELEVANT_STATE",
                "JOURNAL_INTENT",
                "APPLY_ONE_OWNED_OPERATION",
                "WAIT_FOR_DAD_PREFERRED",
                "VERIFY_HOST_AND_INTERNET_INVARIANTS",
                "VERIFY_MANAGEMENT_PATH",
                "RECONNECT_SERIALIZED_SSH",
                "VERIFY_DEVICE_IDENTITY_READ_ONLY",
                "COMMIT_OR_ROLLBACK",
            ],
            capturedState=[
                "primary IPv4 and prefix",
                "DHCP lease and server",
                "default gateway and complete route fingerprint",
                "DNS servers",
                "adapter state and metrics",
                "baseline Internet reachability",
            ],
            preservationInvariants=[
                "DHCP primary address and lease remain acceptable",
                "default route, gateway, DNS, adapter state, and metrics remain unchanged",
                "baseline Internet path remains available",
                "only the exact planned on-link address and route effect may appear",
            ],
            rollbackTriggers=[
                "DAD duplicate, invalid state, or timeout",
                "missing expected on-link route",
                "DHCP, DNS, default route, gateway, adapter, metric, or Internet change",
                "TCP/22 remains unreachable",
                "SSH authentication or host-key failure",
                "read-only observation or device identity verification failure",
            ],
            restartBehavior=(
                "Any incomplete journal blocks new recovery. Re-observe the exact owned object; "
                "remove it only when its post-apply fingerprint still matches, otherwise "
                "require operator reconciliation."
            ),
        ),
        gate=assess_execution_gate(
            plan_status=plan_status,
            binding_valid=True,
            blocker_codes=blocker_codes,
        ),
    )


AddressReadiness = Literal["ABSENT", "TENTATIVE", "PREFERRED", "DUPLICATE", "INVALID"]
SshVerification = Literal[
    "NOT_ATTEMPTED",
    "CONNECTED",
    "UNREACHABLE",
    "AUTHENTICATION_FAILED",
    "HOST_KEY_CHANGED",
]


class RecoveryVerificationInput(BaseModel):
    address_state: AddressReadiness = Field(alias="addressState")
    dad_timed_out: bool = Field(default=False, alias="dadTimedOut")
    on_link_route_present: bool = Field(alias="onLinkRoutePresent")
    primary_address_unchanged: bool = Field(alias="primaryAddressUnchanged")
    default_route_unchanged: bool = Field(alias="defaultRouteUnchanged")
    dns_unchanged: bool = Field(alias="dnsUnchanged")
    dhcp_preserved: bool = Field(alias="dhcpPreserved")
    internet_preserved: bool = Field(alias="internetPreserved")
    neighbor_plausible: bool = Field(alias="neighborPlausible")
    tcp22_reachable: bool = Field(alias="tcp22Reachable")
    ssh: SshVerification
    read_only_observation_succeeded: bool = Field(
        alias="readOnlyObservationSucceeded"
    )
    device_identity_matches: bool = Field(alias="deviceIdentityMatches")
    management_path_healthy: bool = Field(alias="managementPathHealthy")

    model_config = {"populate_by_name": True}


class RecoveryVerificationCheck(BaseModel):
    code: str
    status: VerificationStatus


class RecoveryVerificationResult(BaseModel):
    outcome: Literal["SUCCESS", "WAIT", "ROLLBACK_REQUIRED"]
    checks: list[RecoveryVerificationCheck]
    rollback_reasons: list[str] = Field(default_factory=list, alias="rollbackReasons")

    model_config = {"populate_by_name": True}


def evaluate_recovery_verification(
    observed: RecoveryVerificationInput,
) -> RecoveryVerificationResult:
    """Evaluate mandatory checks in order and fail closed at the first violation."""
    checks: list[RecoveryVerificationCheck] = []

    def stop(code: str, status: Literal["FAIL", "WAIT"]) -> RecoveryVerificationResult:
        checks.append(RecoveryVerificationCheck(code=code, status=status))
        return RecoveryVerificationResult(
            outcome="WAIT" if status == "WAIT" else "ROLLBACK_REQUIRED",
            checks=checks,
            rollbackReasons=[] if status == "WAIT" else [code],
        )

    if observed.address_state == "TENTATIVE":
        code = "DAD_TIMEOUT" if observed.dad_timed_out else "DAD_PENDING"
        status = "FAIL" if observed.dad_timed_out else "WAIT"
        return stop(code, status)
    if observed.address_state != "PREFERRED":
        return stop(f"ADDRESS_{observed.address_state}", "FAIL")
    checks.append(RecoveryVerificationCheck(code="ADDRESS_PREFERRED", status="PASS"))

    ordered_boolean_checks = (
        ("ON_LINK_ROUTE_PRESENT", observed.on_link_route_present),
        ("PRIMARY_ADDRESS_UNCHANGED", observed.primary_address_unchanged),
        ("DEFAULT_ROUTE_UNCHANGED", observed.default_route_unchanged),
        ("DNS_UNCHANGED", observed.dns_unchanged),
        ("DHCP_PRESERVED", observed.dhcp_preserved),
        ("INTERNET_PRESERVED", observed.internet_preserved),
        ("NEIGHBOR_PLAUSIBLE", observed.neighbor_plausible),
        ("TCP22_REACHABLE", observed.tcp22_reachable),
    )
    for code, passed in ordered_boolean_checks:
        if not passed:
            return stop(code, "FAIL")
        checks.append(RecoveryVerificationCheck(code=code, status="PASS"))

    if observed.ssh == "NOT_ATTEMPTED":
        return stop("SSH_NOT_ATTEMPTED", "WAIT")
    if observed.ssh != "CONNECTED":
        return stop(f"SSH_{observed.ssh}", "FAIL")
    checks.append(RecoveryVerificationCheck(code="SSH_CONNECTED", status="PASS"))

    final_checks = (
        ("READ_ONLY_OBSERVATION_SUCCEEDED", observed.read_only_observation_succeeded),
        ("DEVICE_IDENTITY_MATCHES", observed.device_identity_matches),
        ("MANAGEMENT_PATH_HEALTHY", observed.management_path_healthy),
    )
    for code, passed in final_checks:
        if not passed:
            return stop(code, "FAIL")
        checks.append(RecoveryVerificationCheck(code=code, status="PASS"))
    return RecoveryVerificationResult(outcome="SUCCESS", checks=checks)


class RecoveryOwnershipRecord(BaseModel):
    plan_id: str = Field(alias="planId")
    operation_id: str = Field(alias="operationId")
    adapter_id: str = Field(alias="adapterId")
    interface_luid: int = Field(alias="interfaceLuid", ge=1)
    address: str
    prefix_length: int = Field(alias="prefixLength", ge=1, le=30)
    created_at: datetime = Field(alias="createdAt")
    previous_state_fingerprint: str = Field(alias="previousStateFingerprint")
    system_object_key: str = Field(alias="systemObjectKey")
    post_apply_fingerprint: str = Field(alias="postApplyFingerprint")

    model_config = {"populate_by_name": True}


class RollbackDecision(BaseModel):
    disposition: Literal[
        "REMOVE_EXACT_OWNED_OBJECT",
        "ALREADY_ABSENT",
        "MANUAL_RECONCILIATION_REQUIRED",
    ]
    system_object_key: str | None = Field(default=None, alias="systemObjectKey")
    reason: str

    model_config = {"populate_by_name": True}


def select_owned_rollback(
    owner: RecoveryOwnershipRecord,
    observed_object_fingerprints: Mapping[str, str],
) -> RollbackDecision:
    """Select at most the exact journal-owned object; never search by subnet."""
    observed = observed_object_fingerprints.get(owner.system_object_key)
    if observed is None:
        return RollbackDecision(
            disposition="ALREADY_ABSENT",
            reason="The exact journal-owned object is no longer present.",
        )
    if observed != owner.post_apply_fingerprint:
        return RollbackDecision(
            disposition="MANUAL_RECONCILIATION_REQUIRED",
            reason=(
                "The object key exists but its fingerprint no longer proves SwitchOps "
                "ownership."
            ),
        )
    return RollbackDecision(
        disposition="REMOVE_EXACT_OWNED_OBJECT",
        systemObjectKey=owner.system_object_key,
        reason="The exact object key and post-apply fingerprint match the ownership journal.",
    )


JournalState = Literal[
    "PLANNED",
    "APPLYING",
    "VERIFYING",
    "ROLLING_BACK",
    "SUCCEEDED",
    "ROLLED_BACK",
]


class RecoveryJournalRecord(BaseModel):
    transaction_id: str = Field(alias="transactionId")
    state: JournalState
    ownership: RecoveryOwnershipRecord | None = None

    model_config = {"populate_by_name": True}


class RestartAssessment(BaseModel):
    disposition: Literal["CLEAR", "OPERATOR_RECONCILIATION_REQUIRED"]
    incomplete_transaction_ids: list[str] = Field(
        default_factory=list, alias="incompleteTransactionIds"
    )
    new_recovery_allowed: bool = Field(alias="newRecoveryAllowed")

    model_config = {"populate_by_name": True}


def assess_recovery_restart(records: Sequence[RecoveryJournalRecord]) -> RestartAssessment:
    terminal = {"SUCCEEDED", "ROLLED_BACK"}
    incomplete = [record.transaction_id for record in records if record.state not in terminal]
    if incomplete:
        return RestartAssessment(
            disposition="OPERATOR_RECONCILIATION_REQUIRED",
            incompleteTransactionIds=incomplete,
            newRecoveryAllowed=False,
        )
    return RestartAssessment(
        disposition="CLEAR",
        incompleteTransactionIds=[],
        newRecoveryAllowed=True,
    )


# --- Collision-safe recovery addressing ------------------------------------
#
# A recovery address may never be chosen by probing. A silent host, a firewall
# that drops ICMP, and a powered-off device all look identical to a ping, and
# stale ARP proves only that something answered once. The only acceptable basis
# is an explicit reservation somebody is accountable for.

ReservationAuthority = Literal[
    # An identified operator with authority over the network explicitly attests
    # that this exact address is reserved for this recovery operation. Not
    # "it looks free" -- a deliberate, attributable declaration.
    "OPERATOR_DECLARED",
    # The DHCP service is attested to exclude this address from its pool.
    "DHCP_EXCLUSION_ATTESTED",
    # Infrastructure (IPAM, controller) attests the address is reserved.
    "INFRASTRUCTURE_ATTESTED",
    # A Recovery Lab harness reserved this address inside a disposable
    # environment it created. Real authority there, none at all anywhere else.
    "LAB_HARNESS_RESERVED",
]

#: Who or what made the claim. Kept separate from the authority type so that one
#: kind of evidence cannot be filed under another: an operator opinion must not
#: be recorded as an IPAM record, and an IPAM record must not be recorded as a
#: DHCP exclusion.
AttestorType = Literal[
    "NAMED_OPERATOR",
    "DHCP_SERVICE_RECORD",
    "IPAM_RECORD",
    "LAB_HARNESS",
]

#: The class of network the attestation covers. A reservation is authority over
#: one network, not authority in general.
ReservationScope = Literal["PRODUCTION_NETWORK", "DISPOSABLE_LAB_ENVIRONMENT"]

#: Exactly one attestor kind per authority type. Without this, a free-text
#: attestor field would let any authority label be applied to any evidence.
REQUIRED_ATTESTOR: Mapping[str, str] = {
    "OPERATOR_DECLARED": "NAMED_OPERATOR",
    "DHCP_EXCLUSION_ATTESTED": "DHCP_SERVICE_RECORD",
    "INFRASTRUCTURE_ATTESTED": "IPAM_RECORD",
    "LAB_HARNESS_RESERVED": "LAB_HARNESS",
}

#: Authority types that may never speak for a production network, however valid
#: they are inside their own scope.
DISPOSABLE_ONLY_AUTHORITY = ("LAB_HARNESS_RESERVED",)

#: Rejected outright, however tempting. Each of these can be true of an address
#: that is nonetheless in use. They report an absence of evidence, and authority
#: requires a positive claim about the specific address.
REJECTED_COLLISION_EVIDENCE = (
    "ICMP_SILENCE",
    "STALE_ARP_ABSENCE",
    "ABSENT_FROM_LOCAL_NEIGHBOR_CACHE",
    "UNUSED_IN_LAST_OBSERVED_MAC_TABLE",
    "APPARENTLY_UNUSED_ADDRESS",
    "FREE_LOOKING_DHCP_RANGE",
    "DISCOVERY_CONFIDENCE",
    "MODEL_INFERENCE",
    "NETWORK_DESCRIPTION",
    "DAD_FOUND_NO_DUPLICATE",
)

ReservationBlocker = Literal[
    "NO_RESERVATION",
    "RESERVATION_MALFORMED",
    "RESERVATION_ADDRESS_MISMATCH",
    "RESERVATION_OUTSIDE_MANAGEMENT_PREFIX",
    "RESERVATION_PREFIX_LENGTH_MISMATCH",
    "RESERVATION_IS_TARGET_ADDRESS",
    "RESERVATION_IS_GATEWAY_ADDRESS",
    "RESERVATION_IS_NETWORK_OR_BROADCAST",
    "RESERVATION_CONFLICTS_WITH_LOCAL_ADDRESS",
    "RESERVATION_CONFLICTS_WITH_OWNED_ADDRESS",
    "RESERVATION_EVIDENCE_STALE",
    "RESERVATION_EXPIRED",
    "RESERVATION_NOT_YET_VALID",
    "RESERVATION_SCOPE_MISMATCH",
    "RESERVATION_ATTESTOR_INVALID",
    "RESERVATION_AUTHORITY_UNSUPPORTED",
    "RESERVATION_BINDING_MISMATCH",
]


class RecoveryAddressReservation(BaseModel):
    """An address somebody has taken responsibility for keeping free.

    Every field exists to close one specific way of turning a weaker claim into
    authority: the wrong address, the wrong network, an expired declaration, an
    anonymous attestor, or a reservation replayed into an operation it was never
    issued for.
    """

    address: str
    prefix_length: int = Field(alias="prefixLength", ge=1, le=32)
    management_prefix: str = Field(alias="managementPrefix")
    authority: ReservationAuthority
    #: What kind of thing attested it. Must match the authority type.
    attestor_type: AttestorType = Field(alias="attestorType")
    #: Who or what attested it, for the audit trail. Long enough to identify
    #: somebody: "n/a" is not an attestor.
    attested_by: str = Field(alias="attestedBy", min_length=3, max_length=200)
    #: The class of network this attestation covers.
    scope: ReservationScope
    #: Which network or environment instance, so that evidence for one cannot
    #: authorise another of the same class.
    network_scope_id: str = Field(alias="networkScopeId", min_length=3, max_length=120)
    #: Where the claim can be checked: a DHCP scope, IPAM record, or ticket.
    evidence_reference: str = Field(
        alias="evidenceReference", min_length=3, max_length=200
    )
    declared_at: datetime = Field(alias="declaredAt")
    #: When the reservation stops being authority. Explicit, never inferred.
    reserved_until: datetime = Field(alias="reservedUntil")
    #: Optional. Ties the reservation to one plan or operation so that a
    #: reservation issued for one recovery cannot be replayed into another.
    plan_binding: str | None = Field(default=None, alias="planBinding")

    model_config = {"populate_by_name": True}


class ReservationAssessment(BaseModel):
    usable: bool
    blockers: list[ReservationBlocker] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


def assess_recovery_reservation(
    reservation: "RecoveryAddressReservation | None",
    *,
    candidate_address: str,
    management_prefix: str,
    target_address: str,
    gateway_address: str | None,
    local_addresses: Sequence[str],
    now: datetime,
    expected_scope: ReservationScope = "PRODUCTION_NETWORK",
    expected_network_scope_id: str | None = None,
    expected_plan_binding: str | None = None,
    owned_addresses: Sequence[str] = (),
    max_age_days: int = 400,
) -> ReservationAssessment:
    """Decide whether a reservation authorises creating `candidate_address`.

    Absence of a reservation is a blocker, never a licence to pick something.
    The scope defaults to production, so a caller that forgets to say where it
    is running cannot accidentally have a lab reservation accepted.
    """
    import ipaddress

    blockers: list[ReservationBlocker] = []
    evidence: list[str] = []

    if reservation is None:
        return ReservationAssessment(
            usable=False,
            blockers=["NO_RESERVATION"],
            evidence=[
                "No recovery address is reserved. SwitchOps will not select one "
                "by probing: silence is not proof that an address is free."
            ],
        )

    # --- does this attestation even talk about the address we want? ---------
    if reservation.address != candidate_address:
        blockers.append("RESERVATION_ADDRESS_MISMATCH")
        evidence.append(
            f"The reservation authorises {reservation.address}, but "
            f"{candidate_address} is the address that would be created."
        )

    try:
        network = ipaddress.ip_network(management_prefix, strict=False)
        candidate = ipaddress.ip_address(reservation.address)
    except ValueError:
        return ReservationAssessment(
            usable=False,
            blockers=["RESERVATION_MALFORMED"],
            evidence=["The reservation address or management prefix is malformed."],
        )
    if not isinstance(candidate, ipaddress.IPv4Address):
        return ReservationAssessment(
            usable=False,
            blockers=["RESERVATION_MALFORMED"],
            evidence=["Only IPv4 recovery addresses are supported."],
        )

    # --- scope: authority over one network, not authority in general --------
    if reservation.scope != expected_scope:
        blockers.append("RESERVATION_SCOPE_MISMATCH")
        evidence.append(
            f"The reservation is scoped to {reservation.scope} but would be used "
            f"on a {expected_scope}."
        )
    if (
        expected_network_scope_id is not None
        and reservation.network_scope_id != expected_network_scope_id
    ):
        blockers.append("RESERVATION_SCOPE_MISMATCH")
        evidence.append(
            "The reservation names a different network or environment than the "
            "one this operation would run in."
        )
    if (
        reservation.authority in DISPOSABLE_ONLY_AUTHORITY
        and expected_scope != "DISPOSABLE_LAB_ENVIRONMENT"
    ):
        blockers.append("RESERVATION_AUTHORITY_UNSUPPORTED")
        evidence.append(
            f"{reservation.authority} is authority inside a disposable "
            "environment only. It can never authorise a production address."
        )

    # --- attestor: the claim must come from the kind of thing it claims to ---
    if REQUIRED_ATTESTOR.get(reservation.authority) != reservation.attestor_type:
        blockers.append("RESERVATION_ATTESTOR_INVALID")
        evidence.append(
            f"{reservation.authority} requires a "
            f"{REQUIRED_ATTESTOR.get(reservation.authority)} attestor, but this "
            f"was attested by a {reservation.attestor_type}."
        )

    # --- replay: a reservation issued for one operation is not a licence -----
    if reservation.plan_binding is not None and (
        reservation.plan_binding != expected_plan_binding
    ):
        blockers.append("RESERVATION_BINDING_MISMATCH")
        evidence.append(
            "The reservation was issued for a different plan or operation and "
            "cannot be replayed into this one."
        )

    # --- structural safety, whatever the authority says ----------------------
    if candidate not in network:
        blockers.append("RESERVATION_OUTSIDE_MANAGEMENT_PREFIX")
        evidence.append(
            f"{reservation.address} is not inside {management_prefix}, so it would "
            "not create an on-link path to the target."
        )
    else:
        evidence.append(f"{reservation.address} lies inside {management_prefix}.")
        if candidate in (network.network_address, network.broadcast_address):
            blockers.append("RESERVATION_IS_NETWORK_OR_BROADCAST")
            evidence.append("The reservation is the network or broadcast address.")

    if reservation.prefix_length != network.prefixlen:
        blockers.append("RESERVATION_PREFIX_LENGTH_MISMATCH")
        evidence.append(
            f"The reservation carries /{reservation.prefix_length} but the "
            f"management prefix is /{network.prefixlen}, so the on-link route "
            "would not be the one that was attested."
        )

    if reservation.address == target_address:
        blockers.append("RESERVATION_IS_TARGET_ADDRESS")
        evidence.append("The reservation is the Catalyst's own management address.")
    if gateway_address and reservation.address == gateway_address:
        blockers.append("RESERVATION_IS_GATEWAY_ADDRESS")
        evidence.append("The reservation is the gateway address for that prefix.")
    if reservation.address in set(local_addresses):
        blockers.append("RESERVATION_CONFLICTS_WITH_LOCAL_ADDRESS")
        evidence.append(
            "This host already holds the reserved address, so a created address "
            "could not afterwards be identified as ours."
        )
    if reservation.address in set(owned_addresses):
        blockers.append("RESERVATION_CONFLICTS_WITH_OWNED_ADDRESS")
        evidence.append(
            "Another recovery operation already owns this address, so two "
            "operations could not both roll it back safely."
        )

    # --- freshness: two independent limits ----------------------------------
    if reservation.reserved_until <= reservation.declared_at:
        blockers.append("RESERVATION_MALFORMED")
        evidence.append("The reservation expires no later than it was declared.")
    if reservation.declared_at > now:
        blockers.append("RESERVATION_NOT_YET_VALID")
        evidence.append(
            "The reservation is dated in the future, so it cannot be a record of "
            "something that has already been attested."
        )
    elif reservation.reserved_until <= now:
        blockers.append("RESERVATION_EXPIRED")
        evidence.append(
            "The reservation window has closed. An expired reservation is not "
            "weaker authority; it is no authority."
        )
    else:
        age = now - reservation.declared_at
        if age.days > max_age_days:
            blockers.append("RESERVATION_EVIDENCE_STALE")
            evidence.append(
                f"The reservation was attested {age.days} days ago and needs "
                "re-attestation before it can authorise an address."
            )
        else:
            evidence.append(
                f"Reserved by {reservation.authority} ({reservation.attestor_type}), "
                f"attested {age.days} day(s) ago, valid until "
                f"{reservation.reserved_until.isoformat()}."
            )

    return ReservationAssessment(
        usable=not blockers, blockers=blockers, evidence=evidence
    )


# --- What READY would require ----------------------------------------------
#
# READY does not mean "this will probably work". It means SwitchOps has enough
# evidence that asking a human to approve the operation is a reasonable thing
# to do. Anything unproven keeps the plan BLOCKED.

ExecutionReadiness = Literal["READY", "BLOCKED", "NOT_SUPPORTED"]

ReadinessRequirement = Literal[
    "PRIMITIVE_VALIDATED_FOR_PLATFORM",
    "DHCP_COEXISTENCE_VALIDATED",
    "ELEVATION_AVAILABLE",
    "MANAGEMENT_PREFIX_KNOWN",
    "TARGET_IDENTITY_TRUSTED",
    "HOST_ADAPTER_IDENTIFIED",
    "HOST_ADAPTER_UP",
    "DHCP_STATE_ESTABLISHED",
    "RESERVATION_USABLE",
    "PLAN_BINDING_CURRENT",
    "DEFAULT_ROUTE_BASELINE_CAPTURED",
    "DNS_BASELINE_CAPTURED",
    "ROLLBACK_VERIFIED",
    "JOURNAL_AVAILABLE",
]


class ExecutionReadinessInput(BaseModel):
    """Evidence the planner has gathered. Every field defaults to unproven."""

    #: The temporary-address primitive has been validated on this OS and
    #: adapter class by the Recovery Lab. Without it the answer is
    #: NOT_SUPPORTED rather than BLOCKED: no amount of other evidence helps.
    primitive_validated: bool = Field(default=False, alias="primitiveValidated")
    #: Separate from the above on purpose. The isolated experiment proved the
    #: primitive on a statically addressed adapter; it proved nothing about an
    #: interface whose primary address is controlled by DHCP, which is the only
    #: configuration production recovery would run in.
    dhcp_coexistence_validated: bool = Field(
        default=False, alias="dhcpCoexistenceValidated"
    )
    elevation_available: bool = Field(default=False, alias="elevationAvailable")
    management_prefix_known: bool = Field(default=False, alias="managementPrefixKnown")
    target_identity_trusted: bool = Field(default=False, alias="targetIdentityTrusted")
    host_adapter_identified: bool = Field(default=False, alias="hostAdapterIdentified")
    host_adapter_up: bool = Field(default=False, alias="hostAdapterUp")
    dhcp_state_established: bool = Field(default=False, alias="dhcpStateEstablished")
    reservation_usable: bool = Field(default=False, alias="reservationUsable")
    plan_binding_current: bool = Field(default=False, alias="planBindingCurrent")
    default_route_baseline_captured: bool = Field(
        default=False, alias="defaultRouteBaselineCaptured"
    )
    dns_baseline_captured: bool = Field(default=False, alias="dnsBaselineCaptured")
    rollback_verified: bool = Field(default=False, alias="rollbackVerified")
    journal_available: bool = Field(default=False, alias="journalAvailable")

    model_config = {"populate_by_name": True}


class ExecutionReadinessDecision(BaseModel):
    readiness: ExecutionReadiness
    #: Present only when READY, and even then it authorises asking, never acting.
    may_request_operator_approval: bool = Field(alias="mayRequestOperatorApproval")
    satisfied: list[ReadinessRequirement] = Field(default_factory=list)
    unmet: list[ReadinessRequirement] = Field(default_factory=list)
    summary: str

    model_config = {"populate_by_name": True}


_REQUIREMENT_FIELDS: tuple[tuple[str, str], ...] = (
    ("PRIMITIVE_VALIDATED_FOR_PLATFORM", "primitive_validated"),
    ("DHCP_COEXISTENCE_VALIDATED", "dhcp_coexistence_validated"),
    ("ELEVATION_AVAILABLE", "elevation_available"),
    ("MANAGEMENT_PREFIX_KNOWN", "management_prefix_known"),
    ("TARGET_IDENTITY_TRUSTED", "target_identity_trusted"),
    ("HOST_ADAPTER_IDENTIFIED", "host_adapter_identified"),
    ("HOST_ADAPTER_UP", "host_adapter_up"),
    ("DHCP_STATE_ESTABLISHED", "dhcp_state_established"),
    ("RESERVATION_USABLE", "reservation_usable"),
    ("PLAN_BINDING_CURRENT", "plan_binding_current"),
    ("DEFAULT_ROUTE_BASELINE_CAPTURED", "default_route_baseline_captured"),
    ("DNS_BASELINE_CAPTURED", "dns_baseline_captured"),
    ("ROLLBACK_VERIFIED", "rollback_verified"),
    ("JOURNAL_AVAILABLE", "journal_available"),
)


def evaluate_execution_readiness(
    observed: ExecutionReadinessInput,
) -> ExecutionReadinessDecision:
    """Decide whether SwitchOps could honestly ask an operator to approve.

    This is a planning judgement. It grants no execution authority: SwitchOps
    still has no executor, and READY only means the question may be asked.
    """
    satisfied: list[str] = []
    unmet: list[str] = []
    for code, field_name in _REQUIREMENT_FIELDS:
        (satisfied if getattr(observed, field_name) else unmet).append(code)

    # Capability gaps are NOT_SUPPORTED rather than BLOCKED: no amount of extra
    # network evidence can close them, so telling the operator to gather more
    # would be misleading. They are closed by running an experiment.
    if not observed.primitive_validated:
        return ExecutionReadinessDecision(
            readiness="NOT_SUPPORTED",
            mayRequestOperatorApproval=False,
            satisfied=satisfied,
            unmet=unmet,
            summary=(
                "The temporary-address primitive has not been validated for this "
                "platform and adapter class, so no recovery can be offered."
            ),
        )

    if not observed.dhcp_coexistence_validated:
        return ExecutionReadinessDecision(
            readiness="NOT_SUPPORTED",
            mayRequestOperatorApproval=False,
            satisfied=satisfied,
            unmet=unmet,
            summary=(
                "The primitive is validated, but its coexistence with a "
                "DHCP-controlled primary address has not been measured. A "
                "recovery would run on a DHCP interface, so that gap is "
                "disqualifying on its own."
            ),
        )

    if unmet:
        return ExecutionReadinessDecision(
            readiness="BLOCKED",
            mayRequestOperatorApproval=False,
            satisfied=satisfied,
            unmet=unmet,
            summary=(
                f"{len(unmet)} prerequisite(s) are unproven. SwitchOps will not ask "
                "for approval on incomplete evidence."
            ),
        )

    return ExecutionReadinessDecision(
        readiness="READY",
        mayRequestOperatorApproval=True,
        satisfied=satisfied,
        unmet=[],
        summary=(
            "Every prerequisite is evidenced. SwitchOps may ask an operator to "
            "approve a bounded temporary management address; it may not act "
            "without that approval."
        ),
    )
