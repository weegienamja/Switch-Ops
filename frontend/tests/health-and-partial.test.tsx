import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import HealthPanel from "@/components/HealthPanel";
import ObservationHistoryPanel from "@/components/ObservationHistoryPanel";
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
  it("names the first observation instead of implying a sampling cadence", () => {
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
    expect(screen.getByText("first observation")).toBeTruthy();
    expect(screen.queryByText(/sample/i)).toBeNull();
    expect(screen.getByText("No active problems detected")).toBeTruthy();
  });

  it("renders missing history without implying monitoring", () => {
    render(<ObservationHistoryPanel history={null} />);
    expect(screen.getByText("0 observations · last 24h")).toBeTruthy();
    expect(screen.getByText(/No observation has been recorded/)).toBeTruthy();
    expect(screen.queryByText(/samples/i)).toBeNull();
  });

  it("names a single observation honestly and refuses to draw a trend", () => {
    render(<ObservationHistoryPanel history={{
      deviceId: "switch-lab",
      observations: [{
        timestamp: "2026-08-22T04:00:00Z",
        reachable: true,
        cpu5Sec: 6,
        memoryUsedPct: 28,
        temperatureC: 49,
        poeUsedW: 0,
      }],
    }} />);
    // Singular, and never "1 samples".
    expect(screen.getByText("1 observation · last 24h")).toBeTruthy();
    expect(screen.getByText(/This single observation is the baseline/)).toBeTruthy();
    expect(screen.getByText(/at least 3 before it will draw a trend/)).toBeTruthy();
    expect(document.querySelector(".trend__spark")).toBeNull();
    expect(document.querySelectorAll(".trend__dot").length).toBeGreaterThan(0);
  });

  it("states that SwitchOps is refresh-driven with no background polling", () => {
    render(<ObservationHistoryPanel history={null} />);
    expect(screen.getByText(/No background polling is\s+running/)).toBeTruthy();
    expect(screen.getByText("Recent observations")).toBeTruthy();
  });

  it("plots a time-aware sparkline once enough observations exist", () => {
    const observations = [0, 30, 90].map((minutes) => ({
      timestamp: new Date(Date.UTC(2026, 7, 22, 4, minutes)).toISOString(),
      reachable: true,
      cpu5Sec: 5 + minutes / 30,
      memoryUsedPct: 28,
      temperatureC: 49,
      poeUsedW: 0,
    }));
    render(<ObservationHistoryPanel history={{ deviceId: "switch-lab", observations }} />);
    expect(screen.getByText("3 observations · last 24h")).toBeTruthy();
    expect(document.querySelector(".trend__spark")).toBeTruthy();
    // Every observation stays visible as a discrete point.
    expect(document.querySelectorAll(".trend__spark-point").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText(/gaps between them are gaps in\s+observation/)).toBeTruthy();
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
