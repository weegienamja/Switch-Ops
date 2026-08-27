import type {
  AuditResponse,
  BackupResult,
  CpuStatus,
  ConfigurationHistoryEntry,
  ConfigurationHistoryResponse,
  CredentialSetupRequest,
  DashboardResponse,
  DeploymentPlan,
  EnvironmentStatus,
  GuideCatalogResponse,
  GuideRunResult,
  InterfaceErrorsResponse,
  InterfacePolicyResponse,
  InterfacePolicyState,
  LogsResponse,
  MacTableResponse,
  MemoryStatus,
  MockScenarioStatus,
  NetworkEventsResponse,
  PoeResponse,
  SetupStatus,
  SwitchSummary,
  TelemetryHistoryResponse,
  AccessPointPlanRequest,
  ConnectionTestResult,
  ExpectedRelationshipRequest,
  ExpectedTopologyResponse,
  RuntimeInfo,
  ConfigSaveResult,
  ConfigSaveState,
  ChangeSession,
  ChangeSessionList,
  LiveSnapshot,
  OperationKind,
  OperationResult,
  WriteLockStatus,
} from "./types";
import type {
  MerakiConnectionTestResult,
  MerakiNetwork,
  MerakiOrganization,
  MerakiRefreshResult,
  MerakiSelection,
  MerakiSetupStatus,
  UnifiedLabState,
} from "./unifiedTypes";
import type {
  ConfiguredLabDevice,
  LabAssuranceState,
  LabDeviceCreateRequest,
  LabDeviceList,
  PerformanceObservation,
} from "./labTypes";
import type {
  EWPSCandidatePath,
  EWPSConfig,
  EWPSExperimentSession,
  EWPSExportResult,
  EWPSLabProfile,
  EWPSLabScenario,
  EWPSLabScenarioAdvanceResponse,
  EWPSLabStatus,
  EWPSMeta,
  EWPSReplayResult,
  EWPSSimulatorResult,
  EWPSSimulatorScenario,
  EWPSSummary,
  EWPSTimeline,
} from "./ewpsTypes";

export interface InterfaceStatusResponse {
  interfaces: import("./types").InterfaceStatus[];
}

export type ManagementPathConclusion =
  | "MANAGEMENT_PATH_HEALTHY"
  | "HOST_NETWORK_CHANGED"
  | "HOST_ROUTE_MISSING"
  | "HOST_PATH_DEGRADED"
  | "SSH_SERVICE_UNAVAILABLE"
  | "DEVICE_OR_PATH_UNREACHABLE"
  | "AUTHENTICATION_FAILED"
  | "HOST_KEY_CHANGED"
  | "SSH_NEGOTIATION_FAILED"
  | "INDETERMINATE";

export type EvidenceFreshness = "current" | "aging" | "stale" | "historical";

export interface HostAddressObservation {
  address: string;
  prefixLength: number;
  prefixOrigin?: string | null;
  addressState?: string | null;
  skipAsSource?: boolean | null;
}

export interface ManagementPathObservation {
  observedAt: string;
  supported: boolean;
  collectionError?: string | null;
  adapterId?: string | null;
  adapterName?: string | null;
  interfaceIndex?: number | null;
  interfaceMetric?: number | null;
  adapterState?: string | null;
  sourceIp?: string | null;
  prefixLength?: number | null;
  connectedPrefix?: string | null;
  targetOnConnectedPrefix?: boolean | null;
  dhcpEnabled?: boolean | null;
  dhcpStaticCoexistence?: boolean | null;
  adapterAddresses: HostAddressObservation[];
  dhcpServer?: string | null;
  dhcpLeaseObtained?: string | null;
  dhcpLeaseExpires?: string | null;
  defaultGateway?: string | null;
  route: {
    destinationPrefix?: string | null;
    nextHop?: string | null;
    kind: "connected" | "scoped" | "default" | "none" | "unknown";
    routeMetric?: number | null;
    protocol?: string | null;
  };
  targetNeighborState?: string | null;
  gatewayNeighborState?: string | null;
  windowsConnectivity?: string | null;
  tcp22: "reachable" | "refused" | "timed_out" | "unreachable" | "unavailable";
  icmpReachable?: boolean | null;
}

export interface MerakiLanEvidence {
  vlanId?: string | null;
  subnet: string;
  applianceIp?: string | null;
  dhcpMode: "server" | "relay" | "disabled" | "unknown";
  dhcpRelayServerCount: number;
  dhcpLeaseTime?: string | null;
  reservedRangeCount: number;
  fixedAssignmentCount: number;
}

export interface MerakiPortEvidence {
  portId: string;
  enabled?: boolean | null;
  mode: "access" | "trunk" | "unknown";
  accessVlan?: string | null;
  nativeVlan?: string | null;
  allowedVlans: string[];
  catalystFacing?: boolean | null;
}

export interface MerakiManagementEvidence {
  source: "meraki-dashboard-current-configuration";
  state: "not-configured" | "healthy" | "partial" | "unavailable";
  checkedAt: string;
  observedAt?: string | null;
  freshness: EvidenceFreshness;
  complete: boolean;
  detail: string;
  failedOperations: string[];
  vlansEnabled?: boolean | null;
  lans: MerakiLanEvidence[];
  ports: MerakiPortEvidence[];
  catalystPortIdentified: boolean;
}

export interface RecoveryExecutionArchitecture {
  mode: "PLANNING_ONLY";
  executorImplemented: false;
  approvalAvailable: false;
  authority: {
    currentPolicy: "MANUAL_ONLY" | "OPERATOR_APPROVED" | "POLICY_AUTOMATIC";
    futurePolicyCeiling: "MANUAL_ONLY" | "OPERATOR_APPROVED" | "POLICY_AUTOMATIC";
    requiredLevel:
      | "LEVEL_0_READ_ONLY"
      | "LEVEL_1_SESSION_RECOVERY"
      | "LEVEL_2_EPHEMERAL_HOST_NETWORK"
      | "LEVEL_3_PERSISTENT_HOST_NETWORK"
      | "LEVEL_4_DEVICE_CHANGE_ASSURANCE";
    administratorRequired: boolean;
    explicitOperatorApprovalRequired: boolean;
    automaticExecutionEnabled: boolean;
    levels: Array<{
      level: string;
      supported: boolean;
      summary: string;
    }>;
  };
  primitive: {
    selectedPrimitive: "NONE";
    futureCandidate: "IP_HELPER_EPHEMERAL_UNICAST";
    candidateStatus: "ISOLATED_VALIDATION_REQUIRED";
    rationale: string[];
  };
  collisionSafety: {
    requiredAssurance: "AUTHORITATIVE_DEDICATED_RESERVATION";
    acceptedEvidence: string[];
    rejectedEvidence: string[];
    freshnessRequired: boolean;
  };
  ownership: {
    identityFields: string[];
    preexistingObjectMustBeAbsent: boolean;
    exactPostApplyFingerprintRequired: boolean;
    broadCleanupAllowed: boolean;
    ambiguityBehavior: "REQUIRE_OPERATOR_RECONCILIATION";
  };
  transaction: {
    journalRequiredBeforeApply: boolean;
    sequence: string[];
    capturedState: string[];
    preservationInvariants: string[];
    rollbackTriggers: string[];
    restartBehavior: string;
  };
  gate: {
    allowed: false;
    disposition: "BLOCKED" | "NOT_IMPLEMENTED";
    reasons: string[];
  };
}

export interface RecoveryPlan {
  planId: string;
  generatedAt: string;
  status: "NOT_NEEDED" | "BLOCKED" | "READY" | "NOT_SUPPORTED";
  kind: "NONE" | "TEMPORARY_SECONDARY_IPV4";
  headline: string;
  summary: string;
  blockers: Array<{ code: string; summary: string }>;
  missingEvidence: string[];
  warnings: string[];
  operation: {
    kind: "NONE" | "TEMPORARY_SECONDARY_IPV4";
    adapterId?: string | null;
    candidateAddress?: string | null;
    prefixLength?: number | null;
    gateway: null;
    expectedRoute?: string | null;
    persistence: "temporary-active-store";
  };
  candidateEvidence?: {
    address: string;
    prefixLength: number;
    assurance: "authoritative-reservation" | "unverified";
    source: string;
    observedAt: string;
  } | null;
  expectedEffect: string[];
  unchangedState: string[];
  verificationSteps: string[];
  rollbackSteps: string[];
  binding: {
    schemaVersion: number;
    targetId: string;
    adapterId?: string | null;
    primaryAddress?: string | null;
    prefixLength?: number | null;
    defaultGateway?: string | null;
    dhcpLeaseObtained?: string | null;
    dhcpStaticCoexistence?: boolean | null;
    routeFingerprint: string;
    diagnosis: string;
    evidenceObservedAt: string;
    stateFingerprint: string;
  };
  executionArchitecture: RecoveryExecutionArchitecture;
  executionEnabled: false;
}

export interface ManagementPathAssurance {
  targetLabel: string;
  current: ManagementPathObservation;
  lastKnownGood?: {
    observedAt: string;
    lastDeviceSuccessAt?: string | null;
    adapterId?: string | null;
    adapterName?: string | null;
    sourceIp?: string | null;
    prefixLength?: number | null;
    connectedPrefix?: string | null;
    managementPrefix?: string | null;
    defaultGateway?: string | null;
    catalystGateway?: string | null;
    dhcpServer?: string | null;
    catalystInterface?: string | null;
    sameAdapterAsCurrent?: boolean | null;
    provenance: string[];
    freshness: EvidenceFreshness;
  } | null;
  diagnosis: {
    conclusion: ManagementPathConclusion;
    confidence: "HIGH" | "MEDIUM" | "LOW" | "INDETERMINATE";
    headline: string;
    summary: string;
    evidence: string[];
    missingEvidence: string[];
  };
  merakiEvidence: MerakiManagementEvidence;
  recoveryPlan: RecoveryPlan;
  remediationAvailable: boolean;
}

const BACKEND_BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_BACKEND_URL) ||
  "http://127.0.0.1:8765";

export type ApiErrorCategory =
  | "BACKEND_UNREACHABLE"
  | "DEVICE_UNREACHABLE"
  | "DEVICE_AUTH_FAILED"
  | "DEVICE_HOST_KEY_CHANGED"
  | "DEVICE_SSH_NEGOTIATION_FAILED"
  | "DEVICE_SESSION_LOST"
  | "BACKEND_INTERNAL_ERROR";

const DEVICE_ERROR_CODES: Record<string, ApiErrorCategory> = {
  switch_unreachable: "DEVICE_UNREACHABLE",
  switch_connection_failed: "DEVICE_UNREACHABLE",
  switch_connection_error: "DEVICE_UNREACHABLE",
  switch_auth_failed: "DEVICE_AUTH_FAILED",
  authentication_failed: "DEVICE_AUTH_FAILED",
  host_key_changed: "DEVICE_HOST_KEY_CHANGED",
  ssh_negotiation_failed: "DEVICE_SSH_NEGOTIATION_FAILED",
  legacy_ssh_negotiation_failed: "DEVICE_SSH_NEGOTIATION_FAILED",
  switch_session_lost: "DEVICE_SESSION_LOST",
};

export class ApiError extends Error {
  readonly status: number | null;
  readonly code: string;
  readonly detail?: string;
  readonly category: ApiErrorCategory;

  constructor(options: {
    status: number | null;
    code: string;
    message: string;
    detail?: string;
    category: ApiErrorCategory;
  }) {
    super(options.message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.detail = options.detail;
    this.category = options.category;
  }

  get backendResponded(): boolean {
    return this.status !== null;
  }
}

function errorCategory(code: string): ApiErrorCategory {
  return DEVICE_ERROR_CODES[code] || "BACKEND_INTERNAL_ERROR";
}

export function asApiError(cause: unknown): ApiError {
  if (cause instanceof ApiError) return cause;
  return new ApiError({
    status: null,
    code: "client_response_error",
    message: "SwitchOps could not process the backend response.",
    category: "BACKEND_INTERNAL_ERROR",
  });
}

function backendUnavailableError(): ApiError {
  return new ApiError({
    status: null,
    code: "backend_unreachable",
    message: "SwitchOps could not reach the local backend sidecar.",
    detail: "No HTTP response was received from 127.0.0.1:8765.",
    category: "BACKEND_UNREACHABLE",
  });
}

function isErrorPayload(value: unknown): value is {
  code?: unknown;
  message?: unknown;
  detail?: unknown;
} {
  return typeof value === "object" && value !== null;
}

function url(path: string): string {
  return `${BACKEND_BASE}${path}`;
}

export function backendEventStreamUrl(): string {
  return url("/api/live/stream");
}

async function fetchJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url(path), {
      ...init,
      headers: {
        "content-type": "application/json",
        ...(init?.headers || {}),
      },
      cache: "no-store",
    });
  } catch {
    throw backendUnavailableError();
  }
  if (!res.ok) {
    let payload: unknown = null;
    try {
      payload = await res.json();
    } catch {}
    const body = isErrorPayload(payload) ? payload : null;
    const backendCode =
      body && typeof body.code === "string"
        ? body.code
        : res.status >= 500
          ? "backend_internal_error"
          : "http_error";
    const message =
      body && typeof body.message === "string"
        ? body.message
        : res.status >= 500
          ? "The SwitchOps backend could not complete the request."
          : "The SwitchOps backend rejected the request.";
    const detail =
      body && typeof body.code === "string" && typeof body.detail === "string"
        ? body.detail
        : undefined;
    throw new ApiError({
      status: res.status,
      code: backendCode,
      message,
      detail,
      category: errorCategory(backendCode),
    });
  }
  try {
    return await res.json() as T;
  } catch {
    throw new ApiError({
      status: res.status,
      code: "backend_invalid_response",
      message: "The SwitchOps backend returned an invalid response.",
      category: "BACKEND_INTERNAL_ERROR",
    });
  }
}

// Replace "/" in port name with "-" because the URL can't carry the slash.
export function portToPath(port: string): string {
  return port.replace(/\//g, "-");
}

export const api = {
  health: () =>
    fetchJson<{
      ok: boolean;
      service: string;
      mockMode: boolean;
      enableWriteActions: boolean;
    }>("/health"),
  setupStatus: () => fetchJson<SetupStatus>("/api/setup/status"),
  managementPath: () =>
    fetchJson<ManagementPathAssurance>("/api/management-path"),
  systemInfo: () => fetchJson<RuntimeInfo>("/api/system/info"),
  testConnection: () =>
    fetchJson<ConnectionTestResult>("/api/setup/test-connection", { method: "POST" }),
  saveCredentials: (req: CredentialSetupRequest) =>
    fetchJson<SetupStatus>("/api/setup/credentials", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  clearCredentials: () =>
    fetchJson<SetupStatus>("/api/setup/credentials", { method: "DELETE" }),
  merakiStatus: () =>
    fetchJson<MerakiSetupStatus>("/api/meraki/setup/status"),
  saveMerakiApiKey: (apiKey: string) =>
    fetchJson<MerakiSetupStatus>("/api/meraki/setup/credentials", {
      method: "POST",
      body: JSON.stringify({ apiKey }),
    }),
  clearMerakiApiKey: () =>
    fetchJson<MerakiSetupStatus>("/api/meraki/setup/credentials", { method: "DELETE" }),
  testMerakiConnection: () =>
    fetchJson<MerakiConnectionTestResult>("/api/meraki/setup/test", { method: "POST" }),
  merakiOrganizations: () =>
    fetchJson<MerakiOrganization[]>("/api/meraki/organizations"),
  merakiNetworks: (organizationId: string) =>
    fetchJson<MerakiNetwork[]>(
      `/api/meraki/networks?organizationId=${encodeURIComponent(organizationId)}`,
    ),
  saveMerakiSelection: (selection: MerakiSelection) =>
    fetchJson<MerakiSetupStatus>("/api/meraki/selection", {
      method: "PUT",
      body: JSON.stringify(selection),
    }),
  refreshMeraki: () =>
    fetchJson<MerakiRefreshResult>("/api/meraki/refresh", { method: "POST" }),
  unifiedLabState: () =>
    fetchJson<UnifiedLabState>("/api/unified-lab/state"),
  decideUnifiedIdentity: (
    linkId: string,
    decision: "confirm" | "reject" | "clear",
  ) =>
    fetchJson<UnifiedLabState>("/api/unified-lab/identity-decision", {
      method: "POST",
      body: JSON.stringify({ linkId, decision }),
    }),
  labAssuranceState: () =>
    fetchJson<LabAssuranceState>("/api/lab-assurance/state"),
  labAssuranceDevices: () =>
    fetchJson<LabDeviceList>("/api/lab-assurance/devices"),
  addLabAssuranceDevice: (request: LabDeviceCreateRequest) =>
    fetchJson<ConfiguredLabDevice>("/api/lab-assurance/devices", {
      method: "POST",
      body: JSON.stringify(request),
    }),
  removeLabAssuranceDevice: (deviceId: string) =>
    fetchJson<{ removed: boolean }>(
      `/api/lab-assurance/devices/${encodeURIComponent(deviceId)}`,
      { method: "DELETE" },
    ),
  refreshLabAssurance: () =>
    fetchJson<{ accepted: boolean; state: LabAssuranceState }>(
      "/api/lab-assurance/refresh",
      { method: "POST" },
    ),
  runLabProbe: (target: string, label: string, count = 4) =>
    fetchJson<PerformanceObservation>("/api/lab-assurance/performance/probe", {
      method: "POST",
      body: JSON.stringify({ target, label, count }),
    }),
  ewpsMeta: () => fetchJson<EWPSMeta>("/api/ewps/meta"),
  ewpsCandidates: () => fetchJson<EWPSCandidatePath[]>("/api/ewps/candidates"),
  ewpsExperiments: () => fetchJson<EWPSExperimentSession[]>("/api/ewps/experiments"),
  ewpsCurrent: () => fetchJson<EWPSExperimentSession | null>("/api/ewps/experiments/current"),
  ewpsCreate: (request: {
    name: string;
    workloadLabel: string;
    sourceMode: "REAL_INTERFACES" | "CONTROLLED_DUAL_PATH";
    candidatePathIds: string[];
    controlledScenario?: EWPSLabScenario | null;
    config: EWPSConfig;
  }) => fetchJson<EWPSExperimentSession>("/api/ewps/experiments", {
    method: "POST",
    body: JSON.stringify(request),
  }),
  ewpsStart: (experimentId: string) =>
    fetchJson<EWPSExperimentSession>(`/api/ewps/experiments/${encodeURIComponent(experimentId)}/start`, { method: "POST" }),
  ewpsPause: (experimentId: string) =>
    fetchJson<EWPSExperimentSession>(`/api/ewps/experiments/${encodeURIComponent(experimentId)}/pause`, { method: "POST" }),
  ewpsStop: (experimentId: string) =>
    fetchJson<{ session: EWPSExperimentSession; summary: EWPSSummary }>(
      `/api/ewps/experiments/${encodeURIComponent(experimentId)}/stop`,
      { method: "POST" },
    ),
  ewpsTimeline: (experimentId: string, limit = 300) =>
    fetchJson<EWPSTimeline>(
      `/api/ewps/experiments/${encodeURIComponent(experimentId)}/timeline?limit=${limit}`,
    ),
  ewpsSummary: (experimentId: string) =>
    fetchJson<EWPSSummary>(`/api/ewps/experiments/${encodeURIComponent(experimentId)}/summary`),
  ewpsReplay: (experimentId: string, config?: EWPSConfig) =>
    fetchJson<EWPSReplayResult>(`/api/ewps/experiments/${encodeURIComponent(experimentId)}/replay`, {
      method: "POST",
      body: JSON.stringify({ config: config || null }),
    }),
  ewpsSimulatorScenarios: () =>
    fetchJson<EWPSSimulatorScenario[]>("/api/ewps/simulator/scenarios"),
  ewpsRunSimulator: (scenarioId: string, config: EWPSConfig) =>
    fetchJson<EWPSSimulatorResult>("/api/ewps/simulator/run", {
      method: "POST",
      body: JSON.stringify({ scenarioId, config }),
    }),
  ewpsSaveExport: (experimentId: string, format: "jsonl" | "json" | "csv") =>
    fetchJson<EWPSExportResult>(
      `/api/ewps/experiments/${encodeURIComponent(experimentId)}/exports`,
      { method: "POST", body: JSON.stringify({ format }) },
    ),
  ewpsLabStatus: () => fetchJson<EWPSLabStatus>("/api/ewps/lab/status"),
  ewpsLabPrerequisites: () =>
    fetchJson<EWPSLabStatus>("/api/ewps/lab/prerequisites", { method: "POST" }),
  ewpsLabCreate: () =>
    fetchJson<EWPSLabStatus>("/api/ewps/lab/create", { method: "POST" }),
  ewpsLabVerify: () =>
    fetchJson<EWPSLabStatus>("/api/ewps/lab/verify", { method: "POST" }),
  ewpsLabTeardown: () =>
    fetchJson<EWPSLabStatus>("/api/ewps/lab/teardown", { method: "POST" }),
  ewpsLabProfile: (pathId: "lab-path-a" | "lab-path-b", profile: EWPSLabProfile) =>
    fetchJson<EWPSLabStatus>("/api/ewps/lab/profile", {
      method: "POST",
      body: JSON.stringify({ pathId, profile }),
    }),
  ewpsLabPrepareScenario: (scenarioId: EWPSLabScenario) =>
    fetchJson<EWPSLabStatus>("/api/ewps/lab/scenario/prepare", {
      method: "POST",
      body: JSON.stringify({ scenarioId }),
    }),
  ewpsLabAdvanceScenario: () =>
    fetchJson<EWPSLabScenarioAdvanceResponse>("/api/ewps/lab/scenario/advance", { method: "POST" }),
  summary: () => fetchJson<SwitchSummary>("/api/switch/summary"),
  dashboard: () => fetchJson<DashboardResponse>("/api/switch/dashboard"),
  interfaces: () =>
    fetchJson<InterfaceStatusResponse>("/api/switch/interfaces"),
  poe: () => fetchJson<PoeResponse>("/api/switch/poe"),
  errors: () => fetchJson<InterfaceErrorsResponse>("/api/switch/errors"),
  environment: () => fetchJson<EnvironmentStatus>("/api/switch/environment"),
  cpu: () => fetchJson<CpuStatus>("/api/switch/cpu"),
  memory: () => fetchJson<MemoryStatus>("/api/switch/memory"),
  macTable: () => fetchJson<MacTableResponse>("/api/switch/mac-table"),
  logs: () => fetchJson<LogsResponse>("/api/switch/logs"),
  audit: () => fetchJson<AuditResponse>("/api/switch/audit"),
  networkEvents: (query = "") =>
    fetchJson<NetworkEventsResponse>(`/api/network/events${query ? `?${query}` : ""}`),
  telemetryHistory: (deviceId: string, hours = 24) =>
    fetchJson<TelemetryHistoryResponse>(
      `/api/telemetry/history?deviceId=${encodeURIComponent(deviceId)}&hours=${hours}`,
    ),
  configurationHistory: (deviceId?: string) =>
    fetchJson<ConfigurationHistoryResponse>(
      `/api/configuration/history${deviceId ? `?deviceId=${encodeURIComponent(deviceId)}` : ""}`,
    ),
  markConfigurationKnownGood: (entryId: number) =>
    fetchJson<ConfigurationHistoryEntry>(
      `/api/configuration/history/${entryId}/known-good`,
      { method: "POST" },
    ),
  topologyIntent: (deviceId: string) =>
    fetchJson<ExpectedTopologyResponse>(
      `/api/topology/intent?deviceId=${encodeURIComponent(deviceId)}`,
    ),
  setTopologyIntent: (
    deviceId: string,
    port: string,
    request: ExpectedRelationshipRequest,
  ) =>
    fetchJson<ExpectedTopologyResponse>(
      `/api/topology/intent/${portToPath(port)}?deviceId=${encodeURIComponent(deviceId)}`,
      { method: "PUT", body: JSON.stringify(request) },
    ),
  clearTopologyIntent: (deviceId: string, port: string) =>
    fetchJson<ExpectedTopologyResponse>(
      `/api/topology/intent/${portToPath(port)}?deviceId=${encodeURIComponent(deviceId)}`,
      { method: "DELETE" },
    ),
  guideOperations: () => fetchJson<GuideCatalogResponse>("/api/guide/operations"),
  runGuideOperation: (operationId: string, interfaceName?: string) =>
    fetchJson<GuideRunResult>(
      `/api/guide/operations/${encodeURIComponent(operationId)}/run`,
      {
        method: "POST",
        body: JSON.stringify({ interface: interfaceName || null }),
      },
    ),
  planAccessPoint: (request: AccessPointPlanRequest) =>
    fetchJson<DeploymentPlan>("/api/plans/access-point", {
      method: "POST",
      body: JSON.stringify(request),
    }),
  mockScenario: () => fetchJson<MockScenarioStatus>("/api/mock/scenario"),
  setMockScenario: (scenario: "baseline" | "ap_attached") =>
    fetchJson<MockScenarioStatus>("/api/mock/scenario", {
      method: "POST",
      body: JSON.stringify({ scenario }),
    }),
  backup: () =>
    fetchJson<BackupResult>("/api/switch/backup-config", { method: "POST" }),
  liveState: () => fetchJson<LiveSnapshot>("/api/live/state"),
  controlLock: () => fetchJson<WriteLockStatus>("/api/control/lock"),
  unlockControl: () =>
    fetchJson<WriteLockStatus>("/api/control/unlock", { method: "POST" }),
  lockControl: () =>
    fetchJson<WriteLockStatus>("/api/control/lock", { method: "POST" }),
  interfacePolicy: () =>
    fetchJson<InterfacePolicyResponse>("/api/interface-policy"),
  setControlledWrites: (enabled: boolean) =>
    fetchJson<InterfacePolicyResponse>("/api/interface-policy/control", {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),
  setInterfacePolicy: (port: string, state: InterfacePolicyState) =>
    fetchJson<InterfacePolicyResponse>(
      `/api/interface-policy/interfaces/${portToPath(port)}`,
      { method: "PUT", body: JSON.stringify({ state }) },
    ),
  runOperation: (port: string, kind: OperationKind, value?: string) =>
    fetchJson<OperationResult>(`/api/interfaces/${portToPath(port)}/operations`, {
      method: "POST",
      body: JSON.stringify({ kind, value: value || null }),
    }),
  createChangeSession: (port: string, kind: OperationKind, value?: string) =>
    fetchJson<ChangeSession>("/api/change-sessions", {
      method: "POST",
      body: JSON.stringify({
        steps: [{ interface: port, kind, value: value || null }],
      }),
    }),
  preflightChangeSession: (sessionId: string) =>
    fetchJson<ChangeSession>(
      `/api/change-sessions/${encodeURIComponent(sessionId)}/preflight`,
      { method: "POST" },
    ),
  executeChangeSession: (sessionId: string) =>
    fetchJson<ChangeSession>(
      `/api/change-sessions/${encodeURIComponent(sessionId)}/execute`,
      { method: "POST" },
    ),
  changeSession: (sessionId: string) =>
    fetchJson<ChangeSession>(`/api/change-sessions/${encodeURIComponent(sessionId)}`),
  changeSessions: (limit = 50) =>
    fetchJson<ChangeSessionList>(`/api/change-sessions?limit=${limit}`),
  configState: () => fetchJson<ConfigSaveState>("/api/config/state"),
  refreshConfigState: () =>
    fetchJson<ConfigSaveState>("/api/config/state/refresh", { method: "POST" }),
  saveConfig: () => fetchJson<ConfigSaveResult>("/api/config/save", { method: "POST" }),
};
