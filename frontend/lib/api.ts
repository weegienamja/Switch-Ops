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
  LiveSnapshot,
  OperationKind,
  OperationResult,
  WriteLockStatus,
} from "./types";

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
  configState: () => fetchJson<ConfigSaveState>("/api/config/state"),
  refreshConfigState: () =>
    fetchJson<ConfigSaveState>("/api/config/state/refresh", { method: "POST" }),
  saveConfig: () => fetchJson<ConfigSaveResult>("/api/config/save", { method: "POST" }),
};
