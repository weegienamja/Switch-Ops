export type ProviderKind =
  | "catalyst-ios"
  | "meraki-dashboard"
  | "switchops-intent"
  | "switchops-history";
export type EvidenceStrength = "strong" | "supporting" | "weak";
export type EvidenceFreshness = "current" | "aging" | "stale" | "historical";
export type CrossProviderState =
  | "AGREED"
  | "PROVIDER_ONLY"
  | "STALE"
  | "AMBIGUOUS"
  | "CONFLICT"
  | "UNKNOWN";
export type IdentityLinkState =
  | "confirmed"
  | "candidate"
  | "rejected"
  | "conflicted"
  | "stale";

export interface ProviderScope {
  organizationId?: string | null;
  networkId?: string | null;
  deviceRef?: string | null;
}

export interface EvidenceProvenance {
  provider: ProviderKind;
  sourceKind: string;
  sourceObjectRef: string;
  scope: ProviderScope;
  observedAt: string;
  collectedAt: string;
  complete: boolean;
}

export interface ProviderIdentifier {
  kind: string;
  protectedValue: string;
  strength: EvidenceStrength;
  globallyAdministered?: boolean | null;
  provenanceRef: string;
}

export interface NormalizedClaim {
  id: string;
  provider: ProviderKind;
  subjectRef: string;
  field: string;
  value?: string | boolean | number | null;
  objectRef?: string | null;
  strength: EvidenceStrength;
  freshness: EvidenceFreshness;
  provenance: EvidenceProvenance;
  detail: string;
}

export interface ProviderEntity {
  id: string;
  provider: ProviderKind;
  providerRef: string;
  label: string;
  category: string;
  vendor?: string | null;
  model?: string | null;
  identifiers: ProviderIdentifier[];
  claimIds: string[];
  observedAt: string;
  freshness: EvidenceFreshness;
}

export interface IdentityReason {
  kind: "agreement" | "conflict" | "support" | "hint" | "operator";
  field: string;
  strength: EvidenceStrength;
  summary: string;
  provenanceRefs: string[];
}

export interface IdentityLink {
  id: string;
  leftEntityId: string;
  rightEntityId: string;
  state: IdentityLinkState;
  automatic: boolean;
  reasons: IdentityReason[];
  evaluatedAt: string;
  decidedAt?: string | null;
}

export interface IdentityConflict {
  id: string;
  leftEntityId: string;
  rightEntityId: string;
  field: string;
  summary: string;
  provenanceRefs: string[];
}

export type SourceHealthState =
  | "not-configured"
  | "healthy"
  | "partial"
  | "rate-limited"
  | "unavailable"
  | "stale";

export interface SourceHealth {
  provider: ProviderKind;
  state: SourceHealthState;
  detail: string;
  checkedAt: string;
  lastSuccessAt?: string | null;
  nextRetryAt?: string | null;
  complete: boolean;
  failedOperations: string[];
}

export interface AttributeResolution {
  field: string;
  state: CrossProviderState;
  value?: string | boolean | number | null;
  providerValues: Record<string, string | boolean | number | null>;
  claimIds: string[];
  explanation: string;
}

export interface UnifiedEntity {
  id: string;
  label: string;
  category: string;
  providerEntityIds: string[];
  providers: ProviderKind[];
  identityState: CrossProviderState;
  attributes: AttributeResolution[];
  evidenceIds: string[];
  freshness: EvidenceFreshness;
}

export interface UnifiedRelationship {
  id: string;
  subjectId: string;
  objectId: string;
  relationship: string;
  state: CrossProviderState;
  providerClaimIds: string[];
  explanation: string;
}

export interface UnifiedLabState {
  generatedAt: string;
  entities: UnifiedEntity[];
  relationships: UnifiedRelationship[];
  providerEntities: ProviderEntity[];
  claims: NormalizedClaim[];
  identityLinks: IdentityLink[];
  conflicts: IdentityConflict[];
  sourceHealth: SourceHealth[];
}

export interface MerakiSelection {
  organizationId: string;
  organizationName: string;
  networkId: string;
  networkName: string;
}

export interface MerakiSetupStatus {
  configured: boolean;
  keyringAvailable: boolean;
  storage: "keyring" | "none";
  selection?: MerakiSelection | null;
  sourceHealth: SourceHealth;
}

export interface MerakiConnectionTestResult {
  ok: boolean;
  summary: string;
  checkedAt: string;
  organizationsVisible: number;
  sourceHealth: SourceHealth;
}

export interface MerakiOrganization {
  id: string;
  name: string;
}

export interface MerakiNetwork {
  id: string;
  organizationId: string;
  name: string;
  productTypes: string[];
}

export interface MerakiRefreshResult {
  accepted: boolean;
  summary: string;
  sourceHealth: SourceHealth;
}
