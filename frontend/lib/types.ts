// TypeScript types mirroring the FastAPI response models.

export interface SetupStatus {
  configured: boolean;
  hasPassword: boolean;
  hasEnableSecret: boolean;
  storage: "keyring" | "file" | "env" | "none";
  mockMode: boolean;
  enableWriteActions: boolean;
  switchHost?: string | null;
  switchUsername?: string | null;
  switchDeviceType?: string | null;
}

export interface RuntimeInfo {
  version: string;
  apiHost: string;
  apiPort: number;
  mockMode: boolean;
  enableWriteActions: boolean;
  legacySsh: boolean;
  apiDocsEnabled: boolean;
  hostKeyPinned: boolean;
  telemetryRetentionDays: number;
  telemetryCollection: "live-tiered";
  dataDir: string;
  backupDir: string;
  logDir: string;
  corsOrigins: string[];
  deviceDriver?: string | null;
}

export interface ConnectionCheck {
  id: string;
  label: string;
  status: "pass" | "fail" | "skipped";
  detail: string;
}

export interface ConnectionTestResult {
  ok: boolean;
  mode: "mock" | "real";
  summary: string;
  checks: ConnectionCheck[];
  failureCode?: string | null;
  testedAt: string;
  durationMs: number;
}

export interface CredentialSetupRequest {
  switchHost: string;
  switchUsername: string;
  switchPassword: string;
  switchEnableSecret?: string;
  switchDeviceType?: string;
}

export interface SwitchSummary {
  hostname: string;
  model: string;
  managementIp: string;
  gateway: string;
  iosVersion: string;
  serial?: string | null;
  pid?: string | null;
  hardwareRevision?: string | null;
  iosImage?: string | null;
  bootloader?: string | null;
  interfaceCounts?: string | null;
  uptime?: string | null;
  temperatureC?: number | null;
  temperatureState: "GREEN" | "YELLOW" | "RED" | "UNKNOWN";
  cpu5Sec?: number | null;
  poeAvailableW: number;
  poeUsedW: number;
  connectedPorts: string[];
  shutdownPorts: string[];
  errorPorts: string[];
  summary: string;
  healthy: boolean;
  health: HealthAssessment;
  telemetryComplete: boolean;
}

export type HealthState = "HEALTHY" | "NOTICE" | "ATTENTION" | "CRITICAL";

export interface HealthReason {
  code: string;
  severity: HealthState;
  title: string;
  detail: string;
  interface?: string | null;
}

export interface HealthAssessment {
  state: HealthState;
  reasons: HealthReason[];
  evaluatedAt: string;
  basedOnHistory: boolean;
}

export interface InterfaceDelta {
  port: string;
  previousTotalErrors?: number | null;
  currentTotalErrors: number;
  errorDelta?: number | null;
  counterState: "first" | "unchanged" | "increased" | "reset" | "wrapped";
  statusBefore?: string | null;
  statusAfter: string;
  adminBefore?: string | null;
  adminAfter: string;
  speedBefore?: string | null;
  speedAfter: string;
  duplexBefore?: string | null;
  duplexAfter: string;
  vlanBefore?: string | null;
  vlanAfter: string;
  poeBefore?: string | null;
  poeAfter: string;
}

export interface TelemetrySnapshotSummary {
  observedAt: string;
  previousObservedAt?: string | null;
  historyAvailable: boolean;
  interfaceDeltas: InterfaceDelta[];
  retentionDays: number;
}

export type LiveConnectionState =
  | "offline"
  | "connecting"
  | "live"
  | "stale"
  | "reconnecting";

export interface LiveConnection {
  state: LiveConnectionState;
  error?: string | null;
  errorCode?: string | null;
  queueDepth: number;
  lastSuccessAt?: string | null;
  metrics?: Record<string, unknown>;
}

export interface LiveFreshness {
  fast?: string | null;
  medium?: string | null;
  slow?: string | null;
  deep?: string | null;
}

/** Live cache uses dataclass field names rather than Pydantic aliases. */
export interface LiveInterfaceState {
  port: string;
  description: string;
  status: string;
  admin_state: "up" | "down" | "unknown";
  oper_state: "up" | "down" | "unknown";
  speed: string;
  duplex: string;
  vlan: string;
  poe_state: string;
  poe_watts: number;
  protected: boolean;
}

export interface LiveSnapshot {
  deviceId?: string | null;
  interfaces: LiveInterfaceState[];
  poe: { usedW: number; availableW: number };
  freshness: LiveFreshness;
  operationInProgress?: string | null;
  discovery?: { lldp?: LldpDiscoveryStatus };
  connection: LiveConnection;
  tiers?: {
    fastSeconds?: number | null;
    mediumSeconds?: number | null;
    slowSeconds?: number | null;
    paused: boolean;
    ticksRun: number;
    ticksSkipped: number;
  };
}

export type OperationKind =
  | "admin_up"
  | "admin_down"
  | "poe_auto"
  | "poe_never"
  | "set_description";

export interface OperationStage {
  name: "precheck" | "backup" | "execute" | "verify" | "audit" | "rollback";
  status: "pending" | "running" | "ok" | "failed" | "skipped";
  detail: string;
}

export interface OperationResult {
  operationId: string;
  kind: OperationKind;
  interface: string;
  status: "success" | "failed" | "blocked" | "rolled_back";
  detail: string;
  stages: OperationStage[];
  beforeState?: string | null;
  afterState?: string | null;
  commands: string[];
  durationMs: number;
  rolledBack?: boolean | null;
  backupPath?: string | null;
  requiresSave: boolean;
  at: string;
}

export interface OperationProgress {
  kind: OperationKind;
  interface: string;
  stages: OperationStage[];
  status: "running" | OperationResult["status"];
  result?: OperationResult;
}

export interface WriteLockStatus {
  capability: boolean;
  unlocked: boolean;
  unlockedAt?: string | null;
}

export interface ConfigSaveState {
  runningModified: boolean;
  lastChangeAt?: string | null;
  lastSavedAt?: string | null;
  pendingOperations: number;
  detail: string;
}

export interface ConfigSaveResult {
  success: boolean;
  detail: string;
  state: ConfigSaveState;
}

export interface DeviceObservationPoint {
  timestamp: string;
  reachable: boolean;
  cpu5Sec?: number | null;
  memoryUsedPct?: number | null;
  temperatureC?: number | null;
  poeUsedW?: number | null;
  poeAvailableW?: number | null;
}

export interface TelemetryHistoryResponse {
  deviceId: string;
  observations: DeviceObservationPoint[];
}

export interface NetworkEvent {
  id?: number | null;
  timestamp: string;
  deviceId: string;
  interface?: string | null;
  eventType: string;
  severity: HealthState;
  title: string;
  detail: string;
  metadata: Record<string, unknown>;
}

export interface NetworkEventsResponse {
  events: NetworkEvent[];
}

export type DeviceType =
  | "router"
  | "switch"
  | "access-point"
  | "desktop"
  | "laptop"
  | "server"
  | "phone"
  | "tv-media"
  | "printer"
  | "camera"
  | "unknown";

/**
 * How strongly an observation supports a claim about what sits on a link.
 * Mirrors backend/app/models.py — keep the two in step.
 */
export type EvidenceLevel =
  | "direct"
  | "observed-on-port"
  | "learned-behind"
  | "expected"
  | "unknown";

export type IdentitySource =
  | "cdp"
  | "lldp"
  | "local-host"
  | "interface-description"
  | "mac-oui"
  | "user-intent"
  | "meraki-api"
  | "historical"
  | "switch-telemetry"
  | "none";

export type InterfaceRole = "uplink" | "access" | "unknown";

/**
 * Topology reconciliation. Mirrors backend/app/models.py.
 *
 * The axis that matters is `evidenceClass`: it is what keeps a description
 * somebody typed into the switch from being rendered as observed truth.
 */
export type EvidenceClass =
  | "observed"
  | "expected"
  | "historical"
  | "inferred"
  | "unknown";

export type EvidenceSource =
  | "cdp"
  | "lldp"
  | "local-host"
  | "mac-table"
  | "arp"
  | "interface-telemetry"
  | "interface-description"
  | "user-intent"
  | "accepted-plan"
  | "prior-observation"
  | "mac-address-form"
  | "meraki-api"
  | "none";

export type RelationshipKind =
  | "direct-neighbour"
  | "attached-endpoint"
  | "learned-behind"
  | "gateway-path"
  | "expected-neighbour";

export interface TopologyAssertion {
  subject: string;
  relationship: RelationshipKind;
  objectLabel: string;
  /** False when only presence was proven. Never treat the label as a name. */
  objectIdentified: boolean;
  evidenceClass: EvidenceClass;
  source: EvidenceSource;
  confidence: "low" | "medium" | "high";
  detail: string;
  observedAt?: string | null;
  deviceType?: DeviceType | null;
  vendor?: string | null;
  model?: string | null;
}

export type ReconciliationStatus =
  | "aligned"
  | "drift"
  | "expected-not-observed"
  | "unexpected"
  | "uncertain"
  | "not-applicable";

export type DriftKind = "identity" | "location" | "none";

export interface InterfaceReconciliation {
  interface: string;
  status: ReconciliationStatus;
  driftKind: DriftKind;
  headline: string;
  explanation: string;
  observed?: TopologyAssertion | null;
  expected?: TopologyAssertion | null;
  historical?: TopologyAssertion | null;
  inferred: TopologyAssertion[];
  /** Orthogonal to status: a link can match intent and still have changed. */
  changedSincePrevious: boolean;
  changeSummary?: string | null;
  assertions: TopologyAssertion[];
  /** The switch's own description no longer matches the active intent. */
  documentationStale: boolean;
}

export interface ReconciliationSummary {
  evaluatedAt: string;
  deviceId: string;
  aligned: number;
  drift: number;
  expectedNotObserved: number;
  unexpected: number;
  uncertain: number;
  changed: number;
  /** Needs a human decision. Never means the network is unhealthy. */
  attention: boolean;
  headline: string;
  interfaces: InterfaceReconciliation[];
}

export interface ExpectedRelationship {
  deviceId: string;
  interface: string;
  expectedName: string;
  expectedDeviceType: DeviceType;
  expectedVendor?: string | null;
  expectedModel?: string | null;
  source: "user-intent" | "accepted-plan" | "interface-description";
  note?: string | null;
  suppressed: boolean;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface ExpectedRelationshipRequest {
  expectedName: string;
  expectedDeviceType?: DeviceType;
  expectedVendor?: string | null;
  expectedModel?: string | null;
  note?: string | null;
  suppressed?: boolean;
}

export interface ExpectedTopologyResponse {
  deviceId: string;
  relationships: ExpectedRelationship[];
}

export interface CdpNeighbor {
  remoteName: string;
  localInterface: string;
  remoteInterface?: string | null;
  platform?: string | null;
  capabilities: string[];
  ip?: string | null;
}

export interface LldpNeighbor {
  remoteName: string;
  localInterface: string;
  remoteInterface?: string | null;
  systemDescription?: string | null;
  capabilities: string[];
  ip?: string | null;
}

export interface LldpDiscoveryStatus {
  state: "enabled" | "disabled" | "unsupported" | "unknown";
  supported: boolean;
  enabled?: boolean | null;
  neighbors: LldpNeighbor[];
  detail: string;
}

export interface LocalEndpointStatus {
  state: "confirmed" | "ambiguous" | "not-observed" | "unavailable";
  interface?: string | null;
  label: string;
  ip?: string | null;
  detail: string;
}

export interface SnmpInspectionStatus {
  configured: boolean;
  versions: Array<"v1/v2c" | "v3">;
  readOnlyCommunities: number;
  readWriteCommunities: number;
  v3Users: number;
  trapHosts: number;
  detail: string;
}

export interface DiscoveryStatus {
  lldp: LldpDiscoveryStatus;
  localEndpoint: LocalEndpointStatus;
  snmp: SnmpInspectionStatus;
}

export interface DeviceCapability {
  name: string;
  available: boolean;
  source: string;
}

export interface NetworkDevice {
  id: string;
  type: DeviceType;
  vendor?: string | null;
  model?: string | null;
  name: string;
  mac?: string | null;
  ip?: string | null;
  source: "observed" | "inferred" | "expected";
  confidence: "low" | "medium" | "high";
  classificationStage: "unknown" | "category" | "vendor" | "model";
  online: boolean;
  connectedInterface?: string | null;
  visualCategory: DeviceType;
  capabilities: DeviceCapability[];
  lastSeen?: string | null;
  evidence: string[];
  evidenceLevel: EvidenceLevel;
  identitySource: IdentitySource;
  /** What intent says should be here, kept apart from the observed name. */
  expectedName?: string | null;
  expectedType?: DeviceType | null;
  /** Addresses reachable through this link. >1 means devices sit behind it. */
  learnedMacCount: number;
  role: InterfaceRole;
}

export interface NetworkInterface {
  id: string;
  deviceId: string;
  port: string;
  description: string;
  adminState: "up" | "down" | "unknown";
  operState: "up" | "down" | "unknown";
  speed: string;
  duplex: string;
  vlan: string;
  poeCapable: boolean;
  poeState: string;
  poeWatts: number;
  protected: boolean;
  role: InterfaceRole;
  learnedMacCount: number;
}

export interface NetworkLink {
  id: string;
  fromDeviceId: string;
  fromInterface: string;
  toDeviceId: string;
  toInterface?: string | null;
  status: "up" | "down" | "waiting" | "unknown";
  speed: string;
  poe: boolean;
  confidence: "low" | "medium" | "high";
  evidence: string[];
  evidenceLevel: EvidenceLevel;
  learnedMacCount: number;
}

export interface TopologyModel {
  generatedAt: string;
  rootDeviceId: string;
  devices: NetworkDevice[];
  interfaces: NetworkInterface[];
  links: NetworkLink[];
}

export interface InterfaceStatus {
  port: string;
  name: string;
  status: string;
  vlan: string;
  duplex: string;
  speed: string;
  type: string;
  protected: boolean;
  notes?: string | null;
}

export interface InterfaceErrorCounters {
  port: string;
  alignErr: number;
  fcsErr: number;
  xmitErr: number;
  rcvErr: number;
  underSize: number;
  singleCol: number;
  multiCol: number;
  lateCol: number;
  excessCol: number;
  total: number;
}

export interface InterfaceErrorsResponse {
  counters: InterfaceErrorCounters[];
  totalErrors: number;
  healthy: boolean;
}

export interface PoePort {
  interface: string;
  admin: string;
  oper: string;
  powerWatts: number;
  device: string;
  class: string;
  maxWatts: number;
}

export interface PoeResponse {
  availableWatts: number;
  usedWatts: number;
  remainingWatts: number;
  ports: PoePort[];
}

export interface EnvironmentStatus {
  temperatureC?: number | null;
  state: "GREEN" | "YELLOW" | "RED" | "UNKNOWN";
  yellowThresholdC?: number | null;
  redThresholdC?: number | null;
  powerStatus: string;
  raw?: string | null;
}

export interface CpuStatus {
  cpu5Sec?: number | null;
  cpu1Min?: number | null;
  cpu5Min?: number | null;
  raw?: string | null;
}

export interface MemoryStatus {
  processorTotal?: number | null;
  processorUsed?: number | null;
  processorFree?: number | null;
  ioTotal?: number | null;
  ioUsed?: number | null;
  ioFree?: number | null;
  raw?: string | null;
}

export interface MacTableEntry {
  vlan: string;
  mac: string;
  type: string;
  port: string;
}

export interface MacTableResponse {
  entries: MacTableEntry[];
}

export interface LogEntry {
  line: string;
  severity: "info" | "notice" | "warning" | "critical";
}

export interface LogsResponse {
  entries: LogEntry[];
  raw?: string | null;
}

export interface BackupResult {
  filename: string;
  path: string;
  sizeBytes: number;
  timestamp: string;
  redactedPreview: string;
}

export interface AuditEvent {
  id?: number | null;
  timestamp: string;
  actor: string;
  action: string;
  commands: string[];
  success: boolean;
  durationMs: number;
  outputPath?: string | null;
  errorType?: string | null;
  errorMessage?: string | null;
  beforeState?: string | null;
  afterState?: string | null;
}

export interface AuditResponse {
  events: AuditEvent[];
}

export interface DashboardResponse {
  summary: SwitchSummary;
  interfaces: { interfaces: InterfaceStatus[] };
  poe: PoeResponse;
  errors: InterfaceErrorsResponse;
  environment: EnvironmentStatus;
  cpu: CpuStatus;
  memory: MemoryStatus;
  macTable: MacTableResponse;
  logs: LogsResponse;
  audit: AuditResponse;
  telemetry: TelemetrySnapshotSummary;
  events: NetworkEventsResponse;
  topology: TopologyModel;
  reconciliation: ReconciliationSummary;
  discovery: DiscoveryStatus;
  configurationHistory: ConfigurationHistoryResponse;
  sectionErrors: Record<string, string>;
}

export interface ConfigurationHistoryEntry {
  id: number;
  timestamp: string;
  deviceId: string;
  fingerprint: string;
  filename: string;
  previousId?: number | null;
  knownGood: boolean;
  changeDetected: boolean;
  source: "initial_observation" | "external_or_unknown";
  redactedDiff: string[];
}

export interface ConfigurationHistoryResponse {
  entries: ConfigurationHistoryEntry[];
}

export interface AccessPointPlanRequest {
  interface: string;
  role: "wireless-access-point";
  enabled: boolean;
  vlan: number;
  poe: "auto" | "never";
  portfast: boolean;
}

export interface PlanCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface DeploymentPlan {
  planId: string;
  status: "VALID" | "INVALID";
  targetInterface: string;
  desiredState: Record<string, unknown>;
  checks: PlanCheck[];
  impact: string;
  proposedIos: string[];
  backupRequired: boolean;
  verificationCommands: string[];
  applyAvailable: false;
}

export interface GuideOperation {
  id: string;
  category: "GETTING STARTED" | "TROUBLESHOOTING" | "NETWORKING" | "SWITCH";
  title: string;
  question: string;
  whatItTellsYou: string;
  safety: "READ ONLY";
  commands: string[];
  requiresInterface: boolean;
}

export interface GuideCatalogResponse {
  operations: GuideOperation[];
}

export interface GuideRunResult {
  operation: GuideOperation;
  observedAt: string;
  result: Record<string, unknown>;
  explanation: string;
  warnings: string[];
}

export interface MockScenarioStatus {
  scenario: "baseline" | "ap_attached";
  mockMode: boolean;
}
