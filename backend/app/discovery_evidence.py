"""Central discovery-evidence, freshness, confidence, and OUI rules.

The topology builder supplies facts to this module.  Presentation code never
decides whether a fact proves existence, identity, or attachment.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Iterable, Optional

from .models import (
    Confidence,
    DiscoveryEvidence,
    EvidenceClaimSupport,
    EvidenceConflict,
    EvidenceSource,
    EvidenceType,
    FreshnessState,
    RelationshipKind,
)


# Current windows deliberately exceed a normal collection interval. One
# skipped or failed poll therefore moves nothing out of CURRENT.
_CURRENT_SECONDS: dict[EvidenceType, int | None] = {
    "INTERFACE_LINK": 20,
    "INTERFACE_DESCRIPTION": None,
    "CDP_NEIGHBOR": 180,
    "LLDP_NEIGHBOR": 180,
    "MAC_LEARNED": 180,
    "ARP_ENTRY": 180,
    "LOCAL_HOST_MAC": 180,
    "OUI_VENDOR": 180,
    "USER_INTENT": None,
    "ACCEPTED_PLAN": None,
    "PRIOR_OBSERVATION": 0,
}


def normalize_mac(value: str) -> str:
    compact = re.sub(r"[-:.]", "", value.strip().lower())
    return compact if re.fullmatch(r"[0-9a-f]{12}", compact) else ""


@dataclass(frozen=True)
class OuiHint:
    vendor: Optional[str]
    status: str
    detail: str


def oui_vendor_hint(value: str) -> OuiHint:
    """Resolve a globally assigned EUI-48 against the bundled local registry."""
    normalized = normalize_mac(value)
    if len(normalized) != 12 or not re.fullmatch(r"[0-9a-f]{12}", normalized):
        return OuiHint(None, "invalid", "The address is not a valid 48-bit MAC address.")
    first = int(normalized[:2], 16)
    if normalized == "f" * 12:
        return OuiHint(None, "broadcast", "Broadcast addresses do not have a vendor identity.")
    if first & 0x01:
        return OuiHint(None, "multicast", "Multicast addresses do not identify one device vendor.")
    if first & 0x02:
        return OuiHint(
            None,
            "locally-administered",
            "The address is locally administered, so its prefix is not a reliable vendor hint.",
        )
    if normalized == "0" * 12:
        return OuiHint(None, "invalid", "The all-zero address is not a vendor identity.")
    try:
        from netaddr import EUI
        from netaddr.core import NotRegisteredError

        try:
            vendor = str(EUI(normalized).oui.registration().org).strip()
        except NotRegisteredError:
            vendor = ""
    except (ImportError, ValueError, TypeError):
        # Packaged builds include netaddr and its IEEE data. This defensive
        # fallback keeps discovery honest if packaging is incomplete.
        vendor = ""
    if not vendor:
        return OuiHint(None, "unknown", "No vendor is registered locally for this prefix.")
    return OuiHint(
        vendor,
        "registered",
        "The bundled IEEE registry maps the address prefix to this organisation; it does not identify a model or role.",
    )


def evidence_expiry(evidence_type: EvidenceType, observed_at: datetime) -> datetime | None:
    seconds = _CURRENT_SECONDS[evidence_type]
    return observed_at + timedelta(seconds=seconds) if seconds is not None and seconds > 0 else None


def freshness_for(
    *,
    evidence_type: EvidenceType,
    observed_at: datetime,
    reference_at: datetime | None = None,
    revoked: bool = False,
    connection_state: str = "live",
) -> FreshnessState:
    """Categorise age without converting an outage into negative evidence.

    A successful contradictory observation sets ``revoked`` and immediately
    makes a fact historical. During reconnect/offline periods an old fact can
    become STALE, but never HISTORICAL merely because polling was impossible.
    """
    if revoked or evidence_type == "PRIOR_OBSERVATION":
        return "historical"
    current_seconds = _CURRENT_SECONDS[evidence_type]
    if current_seconds is None:
        return "current"
    reference_at = reference_at or datetime.now(timezone.utc)
    age = max(0.0, (reference_at - observed_at).total_seconds())
    if age <= current_seconds:
        return "current"
    if age <= current_seconds * 4:
        return "aging"
    # Offline/reconnecting state must not imply disappearance. The evidence is
    # visibly stale and remains available until a successful poll supersedes it.
    return "stale"


def stable_entity_id(namespace: str, kind: str, identity: str) -> str:
    normalized = re.sub(r"\s+", " ", identity.strip().lower())
    digest = hashlib.sha256(f"{namespace}|{kind}|{normalized}".encode("utf-8")).hexdigest()[:16]
    return f"entity-{digest}"


def _evidence_id(
    evidence_type: EvidenceType,
    device_id: str,
    interface: str | None,
    entity_id: str | None,
    observed_value: str | None,
) -> str:
    key = "|".join((evidence_type, device_id, interface or "", entity_id or "", observed_value or ""))
    return f"ev-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]}"


def evidence_record(
    *,
    evidence_type: EvidenceType,
    evidence_class: str,
    source: EvidenceSource,
    device_id: str,
    interface: str | None,
    entity_id: str | None,
    observed_value: str | None,
    summary: str,
    observed_at: datetime,
    strength: Confidence,
    establishes: EvidenceClaimSupport,
    provenance: str,
    relationship: RelationshipKind | None = None,
) -> DiscoveryEvidence:
    return DiscoveryEvidence(
        id=_evidence_id(evidence_type, device_id, interface, entity_id, observed_value),
        evidenceType=evidence_type,
        evidenceClass=evidence_class,
        source=source,
        deviceId=device_id,
        interface=interface,
        entityId=entity_id,
        observedValue=observed_value,
        summary=summary,
        observedAt=observed_at,
        expiresAt=evidence_expiry(evidence_type, observed_at),
        strength=strength,
        establishes=establishes,
        relationship=relationship,
        provenance=provenance,
    )


def _lower(confidence: Confidence) -> Confidence:
    return {
        "confirmed": "high",
        "high": "medium",
        "medium": "low",
        "low": "unknown",
        "unknown": "unknown",
    }[confidence]


def existence_confidence(records: Iterable[DiscoveryEvidence]) -> Confidence:
    current = [record for record in records if record.freshness == "current" and not record.revoked]
    types = {record.evidence_type for record in current}
    if "LOCAL_HOST_MAC" in types or "CDP_NEIGHBOR" in types or "LLDP_NEIGHBOR" in types:
        return "confirmed"
    if "INTERFACE_LINK" in types and "MAC_LEARNED" in types:
        return "high"
    if "INTERFACE_LINK" in types:
        return "medium"
    if any(record.establishes.existence for record in current):
        return "low"
    return "unknown"


def identity_confidence(
    records: Iterable[DiscoveryEvidence], conflicts: Iterable[EvidenceConflict] = ()
) -> Confidence:
    current = [record for record in records if record.freshness == "current" and not record.revoked]
    types = {record.evidence_type for record in current}
    if "LOCAL_HOST_MAC" in types:
        result: Confidence = "confirmed"
    elif "CDP_NEIGHBOR" in types or "LLDP_NEIGHBOR" in types:
        result = "high"
    elif "OUI_VENDOR" in types and "ARP_ENTRY" in types and "MAC_LEARNED" in types:
        result = "medium"
    elif "OUI_VENDOR" in types:
        result = "low"
    else:
        result = "unknown"
    if any(True for _ in conflicts):
        result = _lower(result)
    return result


def vendor_conflict(
    *,
    observed_vendor: str | None,
    oui_vendor: str | None,
    evidence_ids: list[str],
) -> EvidenceConflict | None:
    """Return a conflict only for clearly disjoint vendor names."""
    if not observed_vendor or not oui_vendor:
        return None
    observed_tokens = set(re.findall(r"[a-z0-9]+", observed_vendor.lower()))
    oui_tokens = set(re.findall(r"[a-z0-9]+", oui_vendor.lower()))
    aliases = {"cisco", "meraki"}
    if observed_tokens & oui_tokens or (observed_tokens & aliases and oui_tokens & aliases):
        return None
    return EvidenceConflict(
        field="vendor",
        summary=(
            f"The neighbour reports {observed_vendor}, while the learned address prefix "
            f"maps to {oui_vendor}. SwitchOps keeps both facts and lowers identity confidence."
        ),
        evidenceIds=evidence_ids,
    )
