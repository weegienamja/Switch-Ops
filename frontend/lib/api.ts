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

const BACKEND_BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_BACKEND_URL) ||
  "http://127.0.0.1:8765";

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
  const res = await fetch(url(path), {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.message || body?.detail || detail;
    } catch {}
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
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
    candidatePathIds: string[];
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
    fetchJson<EWPSLabStatus>("/api/ewps/lab/scenario/advance", { method: "POST" }),
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
