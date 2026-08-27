import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/dynamic", () => ({
  default: () => function DeferredPanel() { return null; },
}));

vi.mock("@/lib/useLiveOperations", () => ({
  useLiveOperations: () => ({
    interfaces: [],
    freshness: { deep: "2026-08-25T10:00:00Z" },
    connection: { state: "live", queueDepth: 0, lastSuccessAt: "2026-08-25T10:00:00Z" },
    streamState: "open",
    operation: null,
    changeSession: null,
    lock: { capability: false, unlocked: false },
    config: { runningModified: false, pendingOperations: 0, detail: "unchanged" },
    lastEventAt: null,
    lldp: null,
    topology: null,
    unlock: vi.fn(),
    lockNow: vi.fn(),
    runOperation: vi.fn(),
    runChangeSession: vi.fn(),
    save: vi.fn(),
    refreshConfig: vi.fn(),
  }),
}));

import DashboardShell from "@/components/DashboardShell";
import {
  api,
  ApiError,
  type ManagementPathAssurance,
  type ManagementPathConclusion,
} from "@/lib/api";
import type { DashboardResponse, SetupStatus } from "@/lib/types";

const setup: SetupStatus = {
  configured: true,
  hasPassword: true,
  hasEnableSecret: false,
  storage: "keyring",
  mockMode: false,
  enableWriteActions: false,
  switchHost: "192.0.2.10",
  switchUsername: "operator",
  switchDeviceType: "cisco_ios",
};

const dashboard = {
  summary: {
    hostname: "stale-switch",
    model: "WS-C3560",
    pid: "WS-C3560CG-8PC-S",
    iosVersion: "12.2",
    health: { state: "HEALTHY", evaluatedAt: "2026-08-25T10:00:00Z", reasons: [] },
  },
  interfaces: { interfaces: [] },
  poe: { availableWatts: 124, usedWatts: 0, remainingWatts: 124, ports: [] },
  errors: { interfaces: [] },
  environment: { fans: [], powerSupplies: [], temperatureState: "GREEN" },
  cpu: { fiveSeconds: 5, oneMinute: 4, fiveMinutes: 3 },
  memory: { totalBytes: 1, usedBytes: 1, freeBytes: 0, usedPercent: 50 },
  macTable: { entries: [] },
  logs: { entries: [] },
  audit: { events: [] },
  telemetry: {
    observedAt: "2026-08-25T10:00:00Z",
    historyAvailable: true,
    retentionDays: 30,
    interfaceDeltas: [],
  },
  events: { events: [] },
  topology: { rootDeviceId: "device-safe", devices: [], interfaces: [], links: [] },
  reconciliation: { deviceId: "device-safe", state: "MATCH", interfaces: [] },
  discovery: { cdp: { state: "unknown" }, lldp: { state: "unknown" }, snmp: { state: "unknown" } },
  configurationHistory: { entries: [] },
  sectionErrors: {},
} as unknown as DashboardResponse;

const managementPath: ManagementPathAssurance = {
  targetLabel: "Configured Catalyst",
  current: {
    observedAt: "2026-08-25T10:00:10Z",
    supported: true,
    adapterId: "adapter-fixture",
    adapterName: "Ethernet",
    sourceIp: "198.51.100.5",
    prefixLength: 24,
    adapterAddresses: [],
    dhcpStaticCoexistence: false,
    connectedPrefix: "198.51.100.0/24",
    targetOnConnectedPrefix: false,
    dhcpEnabled: true,
    dhcpServer: "198.51.100.1",
    dhcpLeaseObtained: "2026-08-25T10:00:10Z",
    defaultGateway: "198.51.100.1",
    route: { destinationPrefix: "0.0.0.0/0", nextHop: "198.51.100.1", kind: "default" },
    windowsConnectivity: "Internet",
    tcp22: "timed_out",
    icmpReachable: false,
  },
  lastKnownGood: {
    observedAt: "2026-08-25T10:00:00Z",
    lastDeviceSuccessAt: "2026-08-25T10:00:00Z",
    adapterId: "adapter-fixture",
    adapterName: "Ethernet",
    sourceIp: "192.0.2.95",
    prefixLength: 24,
    connectedPrefix: "192.0.2.0/24",
    catalystInterface: "Gi0/2",
    sameAdapterAsCurrent: true,
    provenance: ["management-path-history"],
    freshness: "current",
  },
  diagnosis: {
    conclusion: "HOST_NETWORK_CHANGED",
    confidence: "HIGH",
    headline: "Management path degraded",
    summary: "The host left the last-known Catalyst management prefix.",
    evidence: ["The same host adapter received a different prefix."],
    missingEvidence: ["Current Catalyst health is unverified."],
  },
  merakiEvidence: {
    source: "meraki-dashboard-current-configuration",
    state: "not-configured",
    checkedAt: "2026-08-25T10:00:10Z",
    observedAt: null,
    freshness: "historical",
    complete: false,
    detail: "Current Meraki management evidence is unavailable.",
    failedOperations: [],
    lans: [],
    ports: [],
    catalystPortIdentified: false,
  },
  recoveryPlan: {
    planId: "recovery-plan-dashboard-fixture",
    generatedAt: "2026-08-25T10:00:10Z",
    status: "BLOCKED",
    kind: "TEMPORARY_SECONDARY_IPV4",
    headline: "Temporary management address candidate",
    summary: "A temporary local management address may restore direct reachability, but blockers remain.",
    blockers: [{
      code: "DHCP_STATIC_COEXISTENCE_DISABLED",
      summary: "The working DHCP configuration must not be disabled.",
    }],
    missingEvidence: ["A collision-safe address is unavailable."],
    warnings: ["Current Catalyst state remains unverified."],
    operation: {
      kind: "TEMPORARY_SECONDARY_IPV4",
      adapterId: "adapter-fixture",
      candidateAddress: null,
      prefixLength: 24,
      gateway: null,
      expectedRoute: "192.0.2.0/24",
      persistence: "temporary-active-store",
    },
    expectedEffect: ["Create an on-link route for the historical management prefix."],
    unchangedState: ["Keep the DHCP primary address and current default gateway."],
    verificationSteps: ["Confirm the default route is unchanged."],
    rollbackSteps: ["Remove only the temporary address identified by this plan."],
    binding: {
      schemaVersion: 1,
      targetId: "target-synthetic",
      adapterId: "adapter-fixture",
      primaryAddress: "198.51.100.5",
      prefixLength: 24,
      defaultGateway: "198.51.100.1",
      dhcpStaticCoexistence: false,
      routeFingerprint: "route-synthetic",
      diagnosis: "HOST_NETWORK_CHANGED",
      evidenceObservedAt: "2026-08-25T10:00:10Z",
      stateFingerprint: "0123456789abcdef0123456789abcdef",
    },
    executionArchitecture: {
      mode: "PLANNING_ONLY",
      executorImplemented: false,
      approvalAvailable: false,
      authority: {
        currentPolicy: "MANUAL_ONLY",
        futurePolicyCeiling: "OPERATOR_APPROVED",
        requiredLevel: "LEVEL_2_EPHEMERAL_HOST_NETWORK",
        administratorRequired: true,
        explicitOperatorApprovalRequired: true,
        automaticExecutionEnabled: false,
        levels: [],
      },
      primitive: {
        selectedPrimitive: "NONE",
        futureCandidate: "IP_HELPER_EPHEMERAL_UNICAST",
        candidateStatus: "ISOLATED_VALIDATION_REQUIRED",
        rationale: ["DHCP preservation requires isolated validation."],
      },
      collisionSafety: {
        requiredAssurance: "AUTHORITATIVE_DEDICATED_RESERVATION",
        acceptedEvidence: ["Dedicated reservation"],
        rejectedEvidence: ["Failed ping"],
        freshnessRequired: true,
      },
      ownership: {
        identityFields: ["operation ID", "exact object fingerprint"],
        preexistingObjectMustBeAbsent: true,
        exactPostApplyFingerprintRequired: true,
        broadCleanupAllowed: false,
        ambiguityBehavior: "REQUIRE_OPERATOR_RECONCILIATION",
      },
      transaction: {
        journalRequiredBeforeApply: true,
        sequence: ["PRE_FLIGHT", "COMMIT_OR_ROLLBACK"],
        capturedState: ["DHCP", "gateway", "DNS"],
        preservationInvariants: ["The default route and Internet path remain unchanged."],
        rollbackTriggers: ["Internet connectivity changes."],
        restartBehavior: "Incomplete recovery requires operator reconciliation.",
      },
      gate: {
        allowed: false,
        disposition: "BLOCKED",
        reasons: ["PLAN_NOT_READY", "EXECUTOR_NOT_IMPLEMENTED"],
      },
    },
    executionEnabled: false,
  },
  remediationAvailable: false,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Deep Refresh connectivity", () => {
  it("preserves the previous observation as stale when the Catalyst becomes unreachable", async () => {
    vi.spyOn(api, "setupStatus").mockResolvedValue(setup);
    vi.spyOn(api, "guideOperations").mockResolvedValue({ operations: [] });
    vi.spyOn(api, "telemetryHistory").mockResolvedValue({
      deviceId: "device-safe",
      observations: [],
    });
    vi.spyOn(api, "topologyIntent").mockResolvedValue({
      deviceId: "device-safe",
      relationships: [],
    });
    vi.spyOn(api, "unifiedLabState").mockRejectedValue(new Error("not configured"));
    vi.spyOn(api, "managementPath").mockResolvedValue(managementPath);
    vi.spyOn(api, "liveState").mockResolvedValue({
      interfaces: [],
      poe: { usedW: 0, availableW: 0 },
      freshness: { deep: "2026-08-25T10:00:00Z" },
      operationInProgress: null,
      connection: {
        state: "reconnecting",
        queueDepth: 0,
        lastSuccessAt: "2026-08-25T10:00:00Z",
        errorCode: "switch_unreachable",
      },
    });
    const dashboardRequest = vi.spyOn(api, "dashboard")
      .mockResolvedValueOnce(dashboard)
      .mockRejectedValueOnce(new ApiError({
        status: 502,
        code: "switch_unreachable",
        message: "The configured Catalyst could not be reached.",
        detail: "No device-side cause was assumed.",
        category: "DEVICE_UNREACHABLE",
      }));

    render(<DashboardShell />);
    expect(await screen.findByText(/stale-switch/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Deep refresh" }));

    expect(await screen.findByText("MANAGEMENT PATH DEGRADED")).toBeTruthy();
    expect(screen.getByText(/left the last-known Catalyst management prefix/)).toBeTruthy();
    // The typed conclusion must stay visible for evidence review, not just
    // drive the plain-language headline.
    expect(screen.getByText(/Diagnosis: HOST_NETWORK_CHANGED/)).toBeTruthy();
    expect(screen.getByText(/Confidence: HIGH/)).toBeTruthy();
    expect(screen.getByText(/Last observation:/)).toBeTruthy();
    expect(screen.getByText("Session state: reconnecting")).toBeTruthy();
    expect(screen.getByText("Recovery assessment")).toBeTruthy();
    expect(screen.getByText("Review recovery plan")).toBeTruthy();
    expect(screen.getByText(/Planning only · no executor or approval control exists/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /approve temporary recovery/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /fix network/i })).toBeNull();
    expect(screen.getByText(/stale-switch/)).toBeTruthy();
    expect(screen.queryByText(/Ensure the backend sidecar is running/)).toBeNull();
    expect(screen.queryByRole("button", { name: /apply/i })).toBeNull();
    await waitFor(() => expect(dashboardRequest).toHaveBeenCalledTimes(2));
  });

  it.each<[ManagementPathConclusion, string]>([
    ["HOST_ROUTE_MISSING", "No Windows route to the Catalyst"],
    ["HOST_PATH_DEGRADED", "Management path degraded"],
  ])(
    "reports %s as a host-path problem rather than claiming the device is offline",
    async (conclusion, headline) => {
      // A host-side route or path fault says nothing about whether the
      // Catalyst is running. Claiming DEVICE OFFLINE here would assert a
      // device state the evidence does not support.
      vi.spyOn(api, "setupStatus").mockResolvedValue(setup);
      vi.spyOn(api, "guideOperations").mockResolvedValue({ operations: [] });
      vi.spyOn(api, "telemetryHistory").mockResolvedValue({
        deviceId: "device-safe",
        observations: [],
      });
      vi.spyOn(api, "topologyIntent").mockResolvedValue({
        deviceId: "device-safe",
        relationships: [],
      });
      vi.spyOn(api, "unifiedLabState").mockRejectedValue(new Error("not configured"));
      vi.spyOn(api, "managementPath").mockResolvedValue({
        ...managementPath,
        diagnosis: { ...managementPath.diagnosis, conclusion, headline },
      });
      vi.spyOn(api, "liveState").mockResolvedValue({
        interfaces: [],
        poe: { usedW: 0, availableW: 0 },
        freshness: { deep: "2026-08-25T10:00:00Z" },
        operationInProgress: null,
        connection: {
          state: "reconnecting",
          queueDepth: 0,
          lastSuccessAt: "2026-08-25T10:00:00Z",
          errorCode: "switch_unreachable",
        },
      });
      vi.spyOn(api, "dashboard")
        .mockResolvedValueOnce(dashboard)
        .mockRejectedValueOnce(new ApiError({
          status: 502,
          code: "switch_unreachable",
          message: "The configured Catalyst could not be reached.",
          detail: "No device-side cause was assumed.",
          category: "DEVICE_UNREACHABLE",
        }));

      render(<DashboardShell />);
      expect(await screen.findByText(/stale-switch/)).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "Deep refresh" }));

      expect(await screen.findByText(headline.toUpperCase())).toBeTruthy();
      expect(screen.getByText(new RegExp(`Diagnosis: ${conclusion}`))).toBeTruthy();
      expect(screen.queryByText("DEVICE OFFLINE / RECONNECTING")).toBeNull();
    },
  );
});
