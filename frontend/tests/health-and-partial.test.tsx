import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import HealthPanel from "@/components/HealthPanel";
import TelemetryHistoryPanel from "@/components/TelemetryHistoryPanel";
import SummaryCards from "@/components/SummaryCards";
import type { SwitchSummary } from "@/lib/types";

const summary: SwitchSummary = {
  hostname: "Lab switch",
  model: "WS-C3560",
  managementIp: "192.0.2.10",
  gateway: "192.0.2.1",
  iosVersion: "12.2",
  temperatureState: "GREEN",
  temperatureC: 54,
  cpu5Sec: 8,
  poeAvailableW: 124,
  poeUsedW: 0,
  connectedPorts: ["Gi0/1"],
  shutdownPorts: [],
  errorPorts: [],
  summary: "Switch is healthy.",
  healthy: true,
  telemetryComplete: true,
  health: {
    state: "HEALTHY",
    evaluatedAt: "2026-08-22T04:00:00Z",
    basedOnHistory: false,
    reasons: [],
  },
};

describe("health and partial states", () => {
  it("explains a healthy first sample as a baseline", () => {
    render(<HealthPanel health={{
      state: "HEALTHY",
      evaluatedAt: "2026-08-22T04:00:00Z",
      basedOnHistory: false,
      reasons: [{
        code: "no_active_problems",
        severity: "HEALTHY",
        title: "No active problems detected",
        detail: "No adverse change was observed.",
      }],
    }} />);
    expect(screen.getByText("baseline sample")).toBeTruthy();
    expect(screen.getByText("No active problems detected")).toBeTruthy();
  });

  it("renders missing history without throwing", () => {
    render(<TelemetryHistoryPanel history={null} />);
    expect(screen.getByText("0 samples")).toBeTruthy();
    expect(screen.getByText(/This is the baseline/)).toBeTruthy();
  });

  it("shows a cumulative error as baseline until a positive delta exists", () => {
    const { rerender } = render(<SummaryCards
      summary={summary}
      poe={{ availableWatts: 124, usedWatts: 0, remainingWatts: 124, ports: [] }}
      telemetry={{
        observedAt: "2026-08-22T04:00:00Z",
        historyAvailable: false,
        retentionDays: 30,
        interfaceDeltas: [{
          port: "Gi0/2",
          currentTotalErrors: 1,
          counterState: "first",
          statusAfter: "connected",
          adminAfter: "up",
          speedAfter: "a-1000",
          duplexAfter: "a-full",
          vlanAfter: "1",
          poeAfter: "off",
        }],
      }}
    />);
    expect(screen.getByText("baseline")).toBeTruthy();

    rerender(<SummaryCards
      summary={summary}
      poe={{ availableWatts: 124, usedWatts: 0, remainingWatts: 124, ports: [] }}
      telemetry={{
        observedAt: "2026-08-22T04:05:00Z",
        previousObservedAt: "2026-08-22T04:00:00Z",
        historyAvailable: true,
        retentionDays: 30,
        interfaceDeltas: [{
          port: "Gi0/2",
          previousTotalErrors: 1,
          currentTotalErrors: 85,
          errorDelta: 84,
          counterState: "increased",
          statusBefore: "connected",
          statusAfter: "connected",
          adminBefore: "up",
          adminAfter: "up",
          speedBefore: "a-1000",
          speedAfter: "a-1000",
          duplexBefore: "a-full",
          duplexAfter: "a-full",
          vlanBefore: "1",
          vlanAfter: "1",
          poeBefore: "off",
          poeAfter: "off",
        }],
      }}
    />);
    expect(screen.getByText("+84")).toBeTruthy();
  });
});
