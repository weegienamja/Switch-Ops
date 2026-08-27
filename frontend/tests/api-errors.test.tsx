import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ErrorState from "@/components/ErrorState";
import { api, ApiError, type ManagementPathAssurance } from "@/lib/api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function capturedHealthError(): Promise<ApiError> {
  try {
    await api.health();
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error("Expected health request to fail");
}

function failedResponse(status: number, body: Record<string, unknown>): Response {
  return {
    ok: false,
    status,
    statusText: "Synthetic failure",
    json: async () => body,
  } as Response;
}

function managementPathFixture(): ManagementPathAssurance {
  return {
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
      route: { kind: "default", nextHop: "198.51.100.1" },
      windowsConnectivity: "Internet",
      tcp22: "timed_out",
    },
    lastKnownGood: {
      observedAt: "2026-08-20T10:00:00Z",
      lastDeviceSuccessAt: "2026-08-20T10:00:00Z",
      adapterId: "adapter-fixture",
      adapterName: "Ethernet",
      sourceIp: "192.0.2.95",
      prefixLength: 24,
      connectedPrefix: "192.0.2.0/24",
      catalystInterface: "Gi0/2",
      provenance: ["management-path-history"],
      freshness: "stale",
    },
    diagnosis: {
      conclusion: "HOST_NETWORK_CHANGED",
      confidence: "HIGH",
      headline: "Management path degraded",
      summary: "The host left the last-known Catalyst management prefix.",
      evidence: ["The historical and current observations identify the same host adapter."],
      missingEvidence: ["Current Catalyst health is unverified."],
    },
    merakiEvidence: {
      source: "meraki-dashboard-current-configuration",
      state: "healthy",
      checkedAt: "2026-08-25T10:00:10Z",
      observedAt: "2026-08-25T10:00:10Z",
      freshness: "current",
      complete: true,
      detail: "Meraki current LAN and appliance-port configuration was normalized.",
      failedOperations: [],
      vlansEnabled: true,
      lans: [{
        vlanId: "20",
        subnet: "198.51.100.0/24",
        applianceIp: "198.51.100.1",
        dhcpMode: "server",
        dhcpRelayServerCount: 0,
        reservedRangeCount: 0,
        fixedAssignmentCount: 0,
      }],
      ports: [{
        portId: "3",
        enabled: true,
        mode: "trunk",
        nativeVlan: "20",
        allowedVlans: ["20", "30"],
        catalystFacing: true,
      }],
      catalystPortIdentified: true,
    },
    recoveryPlan: {
      planId: "recovery-plan-synthetic",
      generatedAt: "2026-08-25T10:00:10Z",
      status: "BLOCKED",
      kind: "TEMPORARY_SECONDARY_IPV4",
      headline: "Temporary management address candidate",
      summary: "A temporary secondary IPv4 address may recreate an on-link path, but blockers remain.",
      blockers: [{
        code: "DHCP_STATIC_COEXISTENCE_DISABLED",
        summary: "The working DHCP configuration must not be disabled.",
      }],
      missingEvidence: ["No collision-safe address is established."],
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
}

describe("typed API errors", () => {
  it("classifies a fetch failure as backend unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("connection refused")));

    const error = await capturedHealthError();
    expect(error.status).toBeNull();
    expect(error.code).toBe("backend_unreachable");
    expect(error.category).toBe("BACKEND_UNREACHABLE");
    expect(error.backendResponded).toBe(false);

    render(<ErrorState error={error} />);
    expect(screen.getByText("Backend unavailable")).toBeTruthy();
    expect(screen.getByText(/Ensure the backend sidecar is running/)).toBeTruthy();
  });

  it("classifies an HTTP 500 as a running-backend internal error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(failedResponse(500, {
      code: "backend_internal_error",
      message: "The SwitchOps backend could not complete the request.",
    })));

    const error = await capturedHealthError();
    expect(error.status).toBe(500);
    expect(error.category).toBe("BACKEND_INTERNAL_ERROR");
    expect(error.backendResponded).toBe(true);

    render(<ErrorState error={error} />);
    expect(screen.getByText("SwitchOps backend error")).toBeTruthy();
    expect(screen.queryByText(/Ensure the backend sidecar is running/)).toBeNull();
  });

  it("does not call a malformed HTTP response a sidecar outage", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => { throw new SyntaxError("invalid json"); },
    } as unknown as Response));

    const error = await capturedHealthError();
    expect(error.status).toBe(200);
    expect(error.code).toBe("backend_invalid_response");
    expect(error.category).toBe("BACKEND_INTERNAL_ERROR");
    expect(error.backendResponded).toBe(true);

    render(<ErrorState error={error} />);
    expect(screen.queryByText(/Ensure the backend sidecar is running/)).toBeNull();
  });

  it("shows managed-device troubleshooting for switch_unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(failedResponse(502, {
      code: "switch_unreachable",
      message: "The configured Catalyst could not be reached.",
      detail: "No device-side cause was assumed.",
    })));

    const error = await capturedHealthError();
    expect(error.status).toBe(502);
    expect(error.code).toBe("switch_unreachable");
    expect(error.detail).toBe("No device-side cause was assumed.");
    expect(error.category).toBe("DEVICE_UNREACHABLE");

    render(
      <ErrorState
        error={error}
        lastSuccessfulObservation="2026-08-25T10:00:00Z"
        sessionState="reconnecting"
      />,
    );
    expect(screen.getByText("Catalyst unavailable")).toBeTruthy();
    expect(screen.getByText(/PC's network connection may have changed/)).toBeTruthy();
    expect(screen.getByText("Session state: reconnecting")).toBeTruthy();
    expect(screen.queryByText(/Ensure the backend sidecar is running/)).toBeNull();
  });

  it("renders evidence-backed management path diagnosis without offering a mutation", () => {
    const path = managementPathFixture();
    const error = new ApiError({
      status: 502,
      code: "switch_unreachable",
      message: "The configured Catalyst could not be reached.",
      category: "DEVICE_UNREACHABLE",
    });

    render(<ErrorState error={error} managementPath={path} />);
    expect(screen.getByText("Management path degraded")).toBeTruthy();
    expect(screen.getByText(/Ethernet: 192.0.2.95\/24 · Catalyst Gi0\/2/)).toBeTruthy();
    expect(screen.getByText(/Ethernet: 198.51.100.5\/24 · via 198.51.100.1/)).toBeTruthy();
    expect(screen.getByText(/Assessment · HIGH confidence/)).toBeTruthy();
    expect(screen.getByText(/Historical evidence · stale/)).toBeTruthy();
    expect(screen.getByText(/198.51.100.0\/24/)).toBeTruthy();
    expect(screen.getByText(/MX port 3/)).toBeTruthy();
    expect(screen.getByText("Review recovery plan")).toBeTruthy();
    expect(screen.getByText(/DHCP_STATIC_COEXISTENCE_DISABLED/)).toBeTruthy();
    expect(screen.getByText(/Planning only · no executor or approval control exists/i)).toBeTruthy();
    expect(screen.getByText(/EXECUTOR NOT IMPLEMENTED/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /approve temporary recovery/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /fix network/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /apply/i })).toBeNull();
  });

  it("keeps the Windows diagnosis visible when Meraki evidence is unavailable", () => {
    const path = managementPathFixture();
    path.merakiEvidence = {
      ...path.merakiEvidence,
      state: "not-configured",
      observedAt: null,
      freshness: "historical",
      complete: false,
      detail: "Meraki credentials or a network selection are unavailable.",
      lans: [],
      ports: [],
      catalystPortIdentified: false,
    };
    const error = new ApiError({
      status: 502,
      code: "switch_unreachable",
      message: "The configured Catalyst could not be reached.",
      category: "DEVICE_UNREACHABLE",
    });

    render(<ErrorState error={error} managementPath={path} />);
    expect(screen.getByText("Management path degraded")).toBeTruthy();
    expect(screen.getByText(/Meraki credentials or a network selection are unavailable/)).toBeTruthy();
    expect(screen.getByText(/No normalized MX LAN configuration is available/)).toBeTruthy();
  });

  it("never offers Apply even when a recovery plan is ready", () => {
    const path = managementPathFixture();
    path.recoveryPlan.status = "READY";
    path.recoveryPlan.blockers = [];
    path.recoveryPlan.operation.candidateAddress = "192.0.2.200";
    path.recoveryPlan.rollbackSteps = [
      "Remove only the exact journal-owned address object 192.0.2.200/24 on adapter adapter-fixture.",
    ];
    path.recoveryPlan.executionArchitecture.gate.disposition = "NOT_IMPLEMENTED";
    path.recoveryPlan.executionArchitecture.gate.reasons = ["EXECUTOR_NOT_IMPLEMENTED"];
    path.recoveryPlan.candidateEvidence = {
      address: "192.0.2.200",
      prefixLength: 24,
      assurance: "authoritative-reservation",
      source: "synthetic reservation",
      observedAt: "2026-08-25T10:00:10Z",
    };
    const error = new ApiError({
      status: 502,
      code: "switch_unreachable",
      message: "The configured Catalyst could not be reached.",
      category: "DEVICE_UNREACHABLE",
    });

    render(<ErrorState error={error} managementPath={path} />);
    expect(screen.getByText("READY")).toBeTruthy();
    expect(screen.getByText(/Temporary address: 192.0.2.200\/24/)).toBeTruthy();
    expect(screen.getByText(/Remove only the exact journal-owned address object 192.0.2.200\/24/)).toBeTruthy();
    expect(screen.getByText(/Current policy: MANUAL_ONLY/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /apply/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
  });
});
