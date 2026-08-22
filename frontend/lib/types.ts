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
