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
  telemetryComplete: boolean;
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
  sectionErrors: Record<string, string>;
}
