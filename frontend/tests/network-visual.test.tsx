import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import CatalystFrontPanel from "@/components/CatalystFrontPanel";
import NetworkMap from "@/components/NetworkMap";
import PortInspector from "@/components/PortInspector";
import { deviceArtFor } from "@/components/DeviceArt";
import { DEVICE_TYPE_LABELS, learnedBehindChip, learnedBehindNote } from "@/lib/evidence";
import type {
  NetworkDevice,
  NetworkLink,
  TelemetrySnapshotSummary,
  TopologyModel,
} from "@/lib/types";

function device(overrides: Partial<NetworkDevice> & { id: string }): NetworkDevice {
  return {
    type: "unknown",
    name: overrides.id,
    source: "observed",
    confidence: "medium",
    classificationStage: "unknown",
    online: true,
    visualCategory: "unknown",
    capabilities: [],
    evidence: [],
    evidenceLevel: "unknown",
    identitySource: "none",
    learnedMacCount: 0,
    role: "access",
    ...overrides,
  };
}

function link(overrides: Partial<NetworkLink> & { id: string; toDeviceId: string; fromInterface: string }): NetworkLink {
  return {
    fromDeviceId: "switch-synthetic",
    status: "up",
    speed: "a-1000",
    poe: false,
    confidence: "high",
    evidence: [],
    evidenceLevel: "observed-on-port",
    learnedMacCount: 1,
    ...overrides,
  };
}

const topology: TopologyModel = {
  generatedAt: "2026-08-22T04:00:00Z",
  rootDeviceId: "switch-synthetic",
  devices: [
    device({
      id: "switch-synthetic",
      type: "switch",
      vendor: "Cisco",
      model: "WS-C3560CG-8PC-S",
      name: "SWITCHOPS-TEST-SW1",
      confidence: "high",
      classificationStage: "model",
      visualCategory: "switch",
      evidence: ["authenticated telemetry"],
      evidenceLevel: "direct",
      identitySource: "switch-telemetry",
      role: "unknown",
    }),
    // The regression case: one uplink node carrying five learned addresses.
    device({
      id: "endpoint-gi01",
      type: "unknown",
      name: "Unidentified device",
      visualCategory: "unknown",
      connectedInterface: "Gi0/1",
      evidenceLevel: "observed-on-port",
      identitySource: "none",
      expectedName: "Uplink to Test Gateway",
      expectedType: "router",
      existenceConfidence: "high",
      identityConfidence: "unknown",
      freshness: "current",
      evidenceIds: ["ev-link-1", "ev-mac-1"],
      learnedMacCount: 5,
      role: "uplink",
    }),
    device({
      id: "endpoint-gi02",
      type: "unknown",
      name: "Unidentified device",
      visualCategory: "unknown",
      connectedInterface: "Gi0/2",
      evidenceLevel: "observed-on-port",
      identitySource: "none",
      expectedName: "Test Workstation",
      expectedType: "desktop",
      existenceConfidence: "high",
      identityConfidence: "unknown",
      freshness: "current",
      evidenceIds: ["ev-link-2"],
      learnedMacCount: 1,
    }),
  ],
  interfaces: Array.from({ length: 10 }, (_, index) => ({
    id: `switch-synthetic:Gi0/${index + 1}`,
    deviceId: "switch-synthetic",
    port: `Gi0/${index + 1}`,
    description: index === 0 ? "Uplink to Test Gateway" : index === 1 ? "Test Workstation" : index === 3 ? "TEST-AP-01 AP" : "Spare",
    adminState: index > 4 ? ("down" as const) : ("up" as const),
    operState: index < 2 ? ("up" as const) : ("down" as const),
    speed: index < 2 ? "a-1000" : "auto",
    duplex: index < 2 ? "a-full" : "auto",
    vlan: "1",
    poeCapable: index < 8,
    poeState: "off",
    poeWatts: 0,
    protected: index < 2,
    policyState: index < 2 ? ("PROTECTED" as const) : ("UNMANAGED" as const),
    role: index === 0 ? ("uplink" as const) : ("access" as const),
    learnedMacCount: index === 0 ? 5 : index === 1 ? 1 : 0,
  })),
  links: [
    link({ id: "link-gi01", toDeviceId: "endpoint-gi01", fromInterface: "Gi0/1", learnedMacCount: 5 }),
    link({ id: "link-gi02", toDeviceId: "endpoint-gi02", fromInterface: "Gi0/2" }),
  ],
  expectations: [
    {
      interface: "Gi0/4",
      name: "TEST-AP-01 AP",
      deviceType: "access-point",
      vendor: "Cisco Meraki",
      model: "TEST-AP",
      source: "interface-description",
      confidence: "low",
      evidenceIds: ["ev-description-4"],
    },
  ],
  evidence: [
    {
      id: "ev-link-1",
      evidenceType: "INTERFACE_LINK",
      evidenceClass: "observed",
      source: "interface-telemetry",
      deviceId: "switch-synthetic",
      interface: "Gi0/1",
      entityId: "endpoint-gi01",
      summary: "Gi0/1 reports an operational link.",
      observedAt: "2026-08-22T04:00:00Z",
      freshness: "current",
      strength: "high",
      establishes: { existence: true, identity: false, attachment: true, relationship: true, role: false },
      relationship: "attached-endpoint",
      provenance: "show interfaces status",
      revoked: false,
      conflict: false,
    },
    {
      id: "ev-mac-1",
      evidenceType: "MAC_LEARNED",
      evidenceClass: "observed",
      source: "mac-table",
      deviceId: "switch-synthetic",
      interface: "Gi0/1",
      summary: "Five addresses are learned through Gi0/1, not directly attached.",
      observedAt: "2026-08-22T04:00:00Z",
      freshness: "current",
      strength: "high",
      establishes: { existence: true, identity: false, attachment: false, relationship: true, role: false },
      relationship: "learned-behind",
      provenance: "show mac address-table",
      revoked: false,
      conflict: false,
    },
  ],
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

describe("physical front panel", () => {
  it("renders all ten physical ports with non-colour state labels", () => {
    const onSelect = vi.fn();
    render(
      <CatalystFrontPanel
        topology={topology}
        telemetry={telemetry}
        selectedPort="Gi0/4"
        onSelectPort={onSelect}
      />,
    );
    const ports = screen.getAllByRole("button", { name: /^Gi0\// });
    expect(ports).toHaveLength(10);
    expect(screen.getAllByText("WAIT").length).toBeGreaterThan(0);
    expect(screen.getAllByText("LINK").length).toBeGreaterThan(0);
    expect(screen.getAllByText("OFF").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /^Gi0\/2/ }));
    expect(onSelect).toHaveBeenCalledWith("Gi0/2");
  });

  it("describes port state in the accessible name rather than by colour alone", () => {
    render(
      <CatalystFrontPanel
        topology={topology}
        telemetry={telemetry}
        selectedPort="Gi0/1"
        onSelectPort={() => undefined}
      />,
    );
    expect(screen.getByRole("button", { name: /Gi0\/1, link established/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Gi0\/3, enabled, no link detected/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Gi0\/6, administratively disabled/ })).toBeTruthy();
    // Protection is stated in words, not just an icon.
    expect(
      screen.getByRole("button", { name: /Gi0\/1.*protected write policy/ }),
    ).toBeTruthy();
  });
});

describe("network map", () => {
  it("draws one node per interface and never duplicates an uplink per learned MAC", () => {
    render(
      <NetworkMap
        topology={topology}
        telemetry={telemetry}
        selectedPort="Gi0/4"
        onSelectPort={() => undefined}
      />,
    );
    // Two observed endpoints, not expected-only cards or one node per learned MAC.
    const nodes = document.querySelectorAll("[data-node]");
    expect(nodes).toHaveLength(2);
    expect((document.querySelector('[data-node="endpoint-gi01"]') as HTMLElement).textContent).toContain("Uplink to Test Gateway");
    expect(screen.queryByText("Uplink to Test Gateway 1")).toBeNull();
    expect(screen.queryByText("Uplink to Test Gateway 2")).toBeNull();
  });

  it("places an uplink upstream and endpoints below the switch", () => {
    render(
      <NetworkMap
        topology={topology}
        telemetry={telemetry}
        selectedPort="Gi0/4"
        onSelectPort={() => undefined}
      />,
    );
    const upstream = document.querySelector(".lab-canvas__tier--upstream") as HTMLElement;
    const edge = document.querySelector(".lab-canvas__tier--edge") as HTMLElement;
    expect(upstream.textContent).toContain("Uplink to Test Gateway");
    expect(edge.textContent).toContain("Test Workstation");
    expect(within(edge).queryByText("TEST-AP-01 AP")).toBeNull();
    expect(within(upstream).queryByText("Test Workstation")).toBeNull();
  });

  it("keeps expected-only descriptions as compact port intent rather than observed nodes", () => {
    render(
      <NetworkMap
        topology={topology}
        telemetry={telemetry}
        selectedPort="Gi0/4"
        onSelectPort={() => undefined}
      />,
    );
    expect(document.querySelector('[data-node="expected-ap"]')).toBeNull();
    const intent = document.querySelector('[data-expectation="Gi0/4"]') as HTMLElement;
    expect(within(intent).getByText(/No current observed attachment/)).toBeTruthy();
    expect(within(intent).getByText(/Expected only/)).toBeTruthy();
    expect(within(intent).queryByText(/offline/i)).toBeNull();
  });

  it("offers explicit observed, reconciled, and expected knowledge views", () => {
    render(
      <NetworkMap
        topology={topology}
        telemetry={telemetry}
        selectedPort="Gi0/4"
        onSelectPort={() => undefined}
      />,
    );
    expect(screen.getByRole("tab", { name: "Observed" }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByRole("tab", { name: "Expected" }));
    expect(screen.getByText("Expected network")).toBeTruthy();
    expect(document.querySelectorAll("[data-node]")).toHaveLength(0);
    expect(document.querySelector('[data-expectation="Gi0/4"]')).toBeTruthy();
  });

  it("selects the physical port when a topology node is clicked", () => {
    const onSelect = vi.fn();
    render(
      <NetworkMap
        topology={topology}
        telemetry={telemetry}
        selectedPort="Gi0/4"
        onSelectPort={onSelect}
      />,
    );
    fireEvent.click(document.querySelector('[data-node="endpoint-gi02"]') as HTMLElement);
    expect(onSelect).toHaveBeenCalledWith("Gi0/2");
  });

  it("marks the selected node so the front panel and map stay correlated", () => {
    render(
      <NetworkMap
        topology={topology}
        telemetry={telemetry}
        selectedPort="Gi0/1"
        onSelectPort={() => undefined}
      />,
    );
    const selected = document.querySelector('[data-node="endpoint-gi01"]') as HTMLElement;
    expect(selected.getAttribute("aria-pressed")).toBe("true");
    expect(selected.className).toContain("topo-node--selected");
    const other = document.querySelector('[data-node="endpoint-gi02"]') as HTMLElement;
    expect(other.getAttribute("aria-pressed")).toBe("false");
  });
});

describe("port inspector", () => {
  it("explains learned-behind evidence instead of claiming attached devices", () => {
    render(
      <PortInspector topology={topology} telemetry={telemetry} events={[]} selectedPort="Gi0/1" />,
    );
    expect(screen.getByText("Observed on port")).toBeTruthy();
    expect(screen.getByText(/5 addresses are reachable through this link/)).toBeTruthy();
    expect(
      screen.getByText(/Nothing identifies this device yet/),
    ).toBeTruthy();
    expect(screen.getByText("Evidence details · 2")).toBeTruthy();
    expect(screen.getByText(/show mac address-table/)).toBeTruthy();
  });

  it("gives a deterministic reason for each reported value", () => {
    render(
      <PortInspector topology={topology} telemetry={telemetry} events={[]} selectedPort="Gi0/2" />,
    );
    expect(screen.getByText("CONNECTED")).toBeTruthy();
    expect(screen.getByText("1 GBPS")).toBeTruthy();
    expect(screen.getByText("FULL DUPLEX")).toBeTruthy();
    expect(screen.getByText("VLAN 1")).toBeTruthy();
    expect(screen.getByText("PROTECTED")).toBeTruthy();
    expect(screen.getAllByText("Learn more").length).toBeGreaterThan(0);
  });

  it("explains a disabled port as configuration, not failure", () => {
    render(
      <PortInspector topology={topology} telemetry={telemetry} events={[]} selectedPort="Gi0/6" />,
    );
    expect(screen.getByText("DISABLED")).toBeTruthy();
    expect(
      screen.getByText(/administratively shut down and will not establish a link/),
    ).toBeTruthy();
    expect(screen.getByText(/Nothing is currently evidenced on this interface/)).toBeTruthy();
  });

  it("shows expected-only identity at the port without an endpoint card", () => {
    render(
      <PortInspector topology={topology} telemetry={telemetry} events={[]} selectedPort="Gi0/4" />,
    );
    expect(screen.getByText(/Nothing is currently evidenced on this interface.*Expected: TEST-AP-01 AP/)).toBeTruthy();
    expect(screen.queryByText("Observed on port")).toBeNull();
  });
});

describe("device art", () => {
  it("falls back to the unknown-device drawing for an unrecognised type", () => {
    expect(deviceArtFor("unknown")).toBe(deviceArtFor("unknown"));
    expect(DEVICE_TYPE_LABELS.unknown).toBe("Unknown device");
    expect(DEVICE_TYPE_LABELS["access-point"]).toBe("Access point");
  });
});

describe("learned-behind wording", () => {
  it("says nothing when there is nothing to say", () => {
    expect(learnedBehindNote(0)).toBeNull();
    expect(learnedBehindNote(1)).toBeNull();
    expect(learnedBehindChip(1)).toBeNull();
  });

  it("counts the addresses behind the link without claiming they are attached", () => {
    expect(learnedBehindChip(5)).toBe("+4 behind");
    expect(learnedBehindNote(5)).toContain("behind the device on this port");
  });
});
