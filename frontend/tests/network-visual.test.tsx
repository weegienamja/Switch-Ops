import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import CatalystFrontPanel from "@/components/CatalystFrontPanel";
import LogicalTopology from "@/components/LogicalTopology";
import { deviceAssetFor } from "@/components/DeviceVisual";
import type { TelemetrySnapshotSummary, TopologyModel } from "@/lib/types";

const topology: TopologyModel = {
  generatedAt: "2026-08-22T04:00:00Z",
  rootDeviceId: "switch-lab",
  devices: [
    {
      id: "switch-lab",
      type: "switch",
      vendor: "Cisco",
      model: "WS-C3560CG-8PC-S",
      name: "Lab switch",
      source: "observed",
      confidence: "high",
      classificationStage: "model",
      online: true,
      visualCategory: "switch",
      capabilities: [],
      evidence: ["authenticated telemetry"],
    },
    {
      id: "expected-ap",
      type: "access-point",
      vendor: "Cisco Meraki",
      model: "TEST-AP",
      name: "TEST-AP-01 AP",
      source: "expected",
      confidence: "medium",
      classificationStage: "model",
      online: false,
      connectedInterface: "Gi0/4",
      visualCategory: "access-point",
      capabilities: [],
      evidence: ["description only"],
    },
  ],
  interfaces: Array.from({ length: 10 }, (_, index) => ({
    id: `switch-lab:Gi0/${index + 1}`,
    deviceId: "switch-lab",
    port: `Gi0/${index + 1}`,
    description: index === 3 ? "TEST-AP-01 AP" : "Spare",
    adminState: index > 4 ? "down" as const : "up" as const,
    operState: index === 0 ? "up" as const : "down" as const,
    speed: index === 0 ? "a-1000" : "auto",
    duplex: index === 0 ? "a-full" : "auto",
    vlan: "1",
    poeCapable: index < 8,
    poeState: "off",
    poeWatts: 0,
    protected: index < 2,
  })),
  links: [{
    id: "link-ap",
    fromDeviceId: "switch-lab",
    fromInterface: "Gi0/4",
    toDeviceId: "expected-ap",
    status: "waiting",
    speed: "auto",
    poe: false,
    confidence: "low",
    evidence: ["description only"],
  }],
};

const telemetry: TelemetrySnapshotSummary = {
  observedAt: "2026-08-22T04:00:00Z",
  historyAvailable: false,
  retentionDays: 30,
  interfaceDeltas: topology.interfaces.map((item) => ({
    port: item.port,
    currentTotalErrors: item.port === "Gi0/2" ? 1 : 0,
    counterState: "first",
    statusAfter: item.adminState === "down" ? "disabled" : item.operState === "up" ? "connected" : "notconnect",
    adminAfter: item.adminState,
    speedAfter: item.speed,
    duplexAfter: item.duplex,
    vlanAfter: item.vlan,
    poeAfter: item.poeState,
  })),
};

describe("visual network", () => {
  it("renders all ten physical ports with non-colour state labels", () => {
    const onSelect = vi.fn();
    render(
      <CatalystFrontPanel
        topology={topology}
        telemetry={telemetry}
        events={[]}
        selectedPort="Gi0/4"
        onSelectPort={onSelect}
      />,
    );
    const ports = screen.getAllByRole("button", { name: /^Gi0\// });
    expect(ports).toHaveLength(10);
    expect(screen.getAllByText("WAIT").length).toBeGreaterThan(0);
    expect(screen.getByText("baseline")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Gi0\/2/ }));
    expect(onSelect).toHaveBeenCalledWith("Gi0/2");
  });

  it("renders an expected AP as waiting and correlates it to Gi0/4", () => {
    render(
      <LogicalTopology topology={topology} selectedPort="Gi0/4" onSelectPort={() => undefined} />,
    );
    expect(screen.getByText("TEST-AP-01 AP")).toBeTruthy();
    expect(screen.getByText("EXPECTED")).toBeTruthy();
    expect(screen.getByText(/Gi0\/4/)).toBeTruthy();
  });

  it("uses the explicit unknown-device fallback", () => {
    expect(deviceAssetFor("unknown")).toBe("/device-assets/unknown-device.svg");
  });
});
