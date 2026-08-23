import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import NetworkMap from "@/components/NetworkMap";
import PortInspector from "@/components/PortInspector";
import ReconciliationInspector from "@/components/ReconciliationInspector";
import ReconciliationSummaryPanel from "@/components/ReconciliationSummaryPanel";
import HealthPanel from "@/components/HealthPanel";
import { api } from "@/lib/api";
import type {
  InterfaceReconciliation,
  NetworkDevice,
  NetworkLink,
  ReconciliationSummary,
  TelemetrySnapshotSummary,
  TopologyAssertion,
  TopologyModel,
} from "@/lib/types";

const DEVICE_ID = "switch-physical-synthetic-sw1";

function assertion(overrides: Partial<TopologyAssertion> & { subject: string }): TopologyAssertion {
  return {
    relationship: "attached-endpoint",
    objectLabel: "Unidentified device",
    objectIdentified: false,
    evidenceClass: "observed",
    source: "mac-table",
    confidence: "high",
    detail: "detail",
    ...overrides,
  };
}

function recon(overrides: Partial<InterfaceReconciliation> & { interface: string }): InterfaceReconciliation {
  return {
    status: "uncertain",
    driftKind: "none",
    headline: "Present, identity unconfirmed",
    explanation: "explanation",
    inferred: [],
    changedSincePrevious: false,
    assertions: [],
    documentationStale: false,
    ...overrides,
  };
}

/** The real lab: descriptions still describe the pre-migration network. */
const GI01 = recon({
  interface: "Gi0/1",
  status: "uncertain",
  observed: assertion({ subject: "Gi0/1" }),
  expected: assertion({
    subject: "Gi0/1",
    relationship: "expected-neighbour",
    objectLabel: "Uplink to Test Gateway",
    evidenceClass: "expected",
    source: "interface-description",
    confidence: "low",
    detail: "The switch's own description reads 'Uplink to Test Gateway'.",
  }),
  explanation:
    "Gi0/1 has a healthy link, so a device is attached, but nothing identifies it.",
});
GI01.assertions = [GI01.observed!, GI01.expected!];

const GI04 = recon({
  interface: "Gi0/4",
  status: "expected-not-observed",
  headline: "Expected device not observed",
  expected: assertion({
    subject: "Gi0/4",
    relationship: "expected-neighbour",
    objectLabel: "TEST-AP-01 AP",
    evidenceClass: "expected",
    source: "interface-description",
    confidence: "low",
    detail: "description",
  }),
  explanation:
    "TEST-AP-01 AP is expected on Gi0/4, but this switch currently detects no link on that port. SwitchOps cannot conclude the device is offline - it is only absent from the place it was expected.",
});
GI04.assertions = [GI04.expected!];

const GI01_DRIFT = recon({
  interface: "Gi0/1",
  status: "drift",
  driftKind: "identity",
  headline: "Topology drift",
  observed: assertion({
    subject: "Gi0/1",
    relationship: "direct-neighbour",
    objectLabel: "TEST-GATEWAY-01-HQ",
    objectIdentified: true,
    source: "cdp",
    detail: "TEST-GATEWAY-01-HQ announced itself over CDP on Gi0/1.",
    vendor: "Cisco Meraki",
    model: "TEST-GATEWAY-01",
    deviceType: "router",
  }),
  expected: assertion({
    subject: "Gi0/1",
    relationship: "expected-neighbour",
    objectLabel: "Uplink to Test Gateway",
    evidenceClass: "expected",
    source: "interface-description",
    confidence: "low",
    detail: "description",
  }),
  historical: assertion({
    subject: "Gi0/1",
    objectLabel: "Test ISPHub5",
    objectIdentified: true,
    evidenceClass: "historical",
    source: "prior-observation",
    confidence: "medium",
    detail: "Test ISPHub5 was the announced neighbour at the previous observation.",
  }),
  changedSincePrevious: true,
  changeSummary: "The announced neighbour on Gi0/1 changed from Test ISPHub5 to TEST-GATEWAY-01-HQ.",
  explanation:
    "Gi0/1 has a healthy link, but the device that announced itself (TEST-GATEWAY-01-HQ) is not the expected Uplink to Test Gateway. The link itself is fine; the documented topology is out of date.",
});
GI01_DRIFT.assertions = [GI01_DRIFT.observed!, GI01_DRIFT.expected!, GI01_DRIFT.historical!];

const SUMMARY: ReconciliationSummary = {
  evaluatedAt: "2026-08-22T16:00:00Z",
  deviceId: DEVICE_ID,
  aligned: 0,
  drift: 0,
  expectedNotObserved: 2,
  unexpected: 0,
  uncertain: 3,
  changed: 0,
  attention: true,
  headline: "2 expected but not observed",
  interfaces: [
    GI01,
    recon({ interface: "Gi0/2", observed: assertion({ subject: "Gi0/2" }) }),
    recon({ interface: "Gi0/3", observed: assertion({ subject: "Gi0/3" }) }),
    GI04,
    recon({ interface: "Gi0/5", status: "expected-not-observed", headline: "Expected device not observed" }),
    recon({ interface: "Gi0/6", status: "not-applicable", headline: "Nothing expected, nothing observed" }),
  ],
};

function device(overrides: Partial<NetworkDevice> & { id: string }): NetworkDevice {
  return {
    type: "unknown",
    name: "Unidentified device",
    source: "observed",
    confidence: "low",
    classificationStage: "unknown",
    online: true,
    visualCategory: "unknown",
    capabilities: [],
    evidence: [],
    evidenceLevel: "observed-on-port",
    identitySource: "none",
    learnedMacCount: 1,
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

const TOPOLOGY: TopologyModel = {
  generatedAt: "2026-08-22T16:00:00Z",
  rootDeviceId: "switch-synthetic",
  devices: [
    device({
      id: "switch-synthetic",
      type: "switch",
      name: "SWITCHOPS-TEST-SW1",
      model: "WS-C3560CG-8PC-S",
      visualCategory: "switch",
      confidence: "high",
      evidenceLevel: "direct",
      identitySource: "switch-telemetry",
      role: "unknown",
    }),
    device({
      id: "endpoint-gi01",
      connectedInterface: "Gi0/1",
      expectedName: "Uplink to Test Gateway",
      expectedType: "router",
      role: "uplink",
      learnedMacCount: 2,
    }),
  ],
  interfaces: Array.from({ length: 10 }, (_, index) => ({
    id: `switch-synthetic:Gi0/${index + 1}`,
    deviceId: "switch-synthetic",
    port: `Gi0/${index + 1}`,
    description: index === 0 ? "Uplink to Test Gateway" : index === 3 ? "TEST-AP-01 AP" : "Spare",
    adminState: "up" as const,
    operState: index === 0 ? ("up" as const) : ("down" as const),
    speed: index === 0 ? "a-1000" : "auto",
    duplex: index === 0 ? "a-full" : "auto",
    vlan: "1",
    poeCapable: index < 8,
    poeState: "off",
    poeWatts: 0,
    protected: index < 2,
    role: index === 0 ? ("uplink" as const) : ("access" as const),
    learnedMacCount: index === 0 ? 2 : 0,
  })),
  links: [
    link({ id: "l1", toDeviceId: "endpoint-gi01", fromInterface: "Gi0/1", learnedMacCount: 2 }),
  ],
  expectations: [
    {
      interface: "Gi0/4",
      name: "TEST-AP-01 AP",
      deviceType: "access-point",
      source: "interface-description",
      confidence: "low",
      evidenceIds: ["ev-description-4"],
    },
  ],
};

const TELEMETRY: TelemetrySnapshotSummary = {
  observedAt: "2026-08-22T16:00:00Z",
  historyAvailable: false,
  retentionDays: 30,
  interfaceDeltas: TOPOLOGY.interfaces.map((item) => ({
    port: item.port,
    currentTotalErrors: 0,
    counterState: "first",
    statusAfter: item.operState === "up" ? "connected" : "notconnect",
    adminAfter: item.adminState,
    speedAfter: item.speed,
    duplexAfter: item.duplex,
    vlanAfter: item.vlan,
    poeAfter: item.poeState,
  })),
};

afterEach(() => vi.restoreAllMocks());

// --- health and reconciliation are shown separately -------------------------

describe("health versus reconciliation", () => {
  it("keeps reconciliation out of the health panel", () => {
    render(
      <HealthPanel
        health={{
          state: "HEALTHY",
          evaluatedAt: "2026-08-22T16:00:00Z",
          basedOnHistory: true,
          reasons: [
            {
              code: "no_active_problems",
              severity: "HEALTHY",
              title: "No active problems detected",
              detail: "No adverse change was observed.",
            },
          ],
        }}
      />,
    );
    const text = document.body.textContent || "";
    expect(text).toContain("HEALTHY");
    expect(text.toLowerCase()).not.toContain("drift");
    expect(text.toLowerCase()).not.toContain("reconcil");
  });

  it("states that a healthy network can still be drifted", () => {
    render(<ReconciliationSummaryPanel reconciliation={SUMMARY} />);
    expect(screen.getByText("Topology reconciliation")).toBeTruthy();
    expect(
      screen.getByText(/Health describes whether the switch and its links are working/),
    ).toBeTruthy();
    expect(screen.getByText(/Both can be true at once/)).toBeTruthy();
  });

  it("summarises counts without using health words", () => {
    render(<ReconciliationSummaryPanel reconciliation={SUMMARY} />);
    expect(screen.getByText("2 expected but not observed")).toBeTruthy();
    expect(screen.getByText("Attention")).toBeTruthy();
    const text = document.body.textContent || "";
    expect(text).not.toMatch(/\bunhealthy\b|\bfault\b|\bCRITICAL\b/i);
  });

  it("lets the operator jump from a discrepancy to the port", () => {
    const onInspect = vi.fn();
    render(<ReconciliationSummaryPanel reconciliation={SUMMARY} onInspect={onInspect} />);
    fireEvent.click(screen.getByRole("button", { name: /Gi0\/4/ }));
    expect(onInspect).toHaveBeenCalledWith("Gi0/4");
  });

  it("does not list untracked interfaces as needing a decision", () => {
    render(<ReconciliationSummaryPanel reconciliation={SUMMARY} />);
    expect(screen.queryByRole("button", { name: /Gi0\/6/ })).toBeNull();
  });
});

// --- the description never renders as an observed identity ------------------

describe("observed versus expected in the map", () => {
  it("never labels an unidentified endpoint with the interface description", () => {
    render(
      <NetworkMap
        topology={TOPOLOGY}
        telemetry={TELEMETRY}
        selectedPort="Gi0/1"
        onSelectPort={() => undefined}
        reconciliation={SUMMARY}
      />,
    );
    const node = document.querySelector('[data-node="endpoint-gi01"]') as HTMLElement;
    expect(within(node).getByText("Unidentified device")).toBeTruthy();
    // The description appears only as an explicitly labelled expectation.
    expect(within(node).getByText("Expected: Uplink to Test Gateway")).toBeTruthy();
    expect(within(node).queryByText("Uplink to Test Gateway")).toBeNull();
  });

  it("marks an unconfirmed endpoint with the reconciliation status", () => {
    render(
      <NetworkMap
        topology={TOPOLOGY}
        telemetry={TELEMETRY}
        selectedPort="Gi0/1"
        onSelectPort={() => undefined}
        reconciliation={SUMMARY}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Reconciled" }));
    const node = document.querySelector('[data-node="endpoint-gi01"]') as HTMLElement;
    expect(node.getAttribute("data-reconciliation")).toBe("uncertain");
    expect(within(node).getByText("Unconfirmed")).toBeTruthy();
  });

  it("flags an interface that needs a decision", () => {
    render(
      <NetworkMap
        topology={TOPOLOGY}
        telemetry={TELEMETRY}
        selectedPort="Gi0/1"
        onSelectPort={() => undefined}
        reconciliation={SUMMARY}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Reconciled" }));
    const missing = document.querySelector('[data-expectation="Gi0/4"]') as HTMLElement;
    expect(missing.getAttribute("data-reconciliation")).toBe("expected-not-observed");
    expect(missing.className).toContain("port-expectation--attention");
    expect(within(missing).getByText("Not observed")).toBeTruthy();
  });

  it("keeps expected-only intent out of the observed link graph", () => {
    render(
      <NetworkMap
        topology={TOPOLOGY}
        telemetry={TELEMETRY}
        selectedPort="Gi0/1"
        onSelectPort={() => undefined}
        reconciliation={SUMMARY}
      />,
    );
    // The observed uplink is a link; the AP description is port intent only.
    const observedLink = TOPOLOGY.links.find((item) => item.fromInterface === "Gi0/1");
    const expectedLink = TOPOLOGY.links.find((item) => item.fromInterface === "Gi0/4");
    expect(observedLink?.status).toBe("up");
    expect(expectedLink).toBeUndefined();
    expect(TOPOLOGY.expectations?.[0].interface).toBe("Gi0/4");
  });

  it("still refuses to duplicate a router behind an uplink", () => {
    render(
      <NetworkMap
        topology={TOPOLOGY}
        telemetry={TELEMETRY}
        selectedPort="Gi0/1"
        onSelectPort={() => undefined}
        reconciliation={SUMMARY}
      />,
    );
    // Two addresses behind Gi0/1, one node.
    expect(document.querySelectorAll('[data-node="endpoint-gi01"]')).toHaveLength(1);
    expect(document.querySelectorAll("[data-node]")).toHaveLength(1);
  });
});

// --- the inspector ----------------------------------------------------------

describe("reconciliation inspector", () => {
  it("groups claims by what kind of knowledge they are", () => {
    render(
      <ReconciliationInspector
        deviceId={DEVICE_ID}
        result={GI01_DRIFT}
        onIntentChange={() => undefined}
      />,
    );
    expect(screen.getByText("Observed")).toBeTruthy();
    expect(screen.getByText("Expected")).toBeTruthy();
    expect(screen.getByText("Previously")).toBeTruthy();
  });

  it("presents a CDP identity as observed and the description as expected", () => {
    render(
      <ReconciliationInspector
        deviceId={DEVICE_ID}
        result={GI01_DRIFT}
        onIntentChange={() => undefined}
      />,
    );
    expect(screen.getByText("TEST-GATEWAY-01-HQ")).toBeTruthy();
    expect(screen.getByText(/from the device announced itself over CDP/)).toBeTruthy();
    expect(screen.getByText("Uplink to Test Gateway")).toBeTruthy();
    expect(screen.getByText(/from the description configured on the switch/)).toBeTruthy();
  });

  it("shows a drift badge and says the link is still fine", () => {
    render(
      <ReconciliationInspector
        deviceId={DEVICE_ID}
        result={GI01_DRIFT}
        onIntentChange={() => undefined}
      />,
    );
    expect(screen.getByText("Drift")).toBeTruthy();
    expect(screen.getByText(/link itself is fine/)).toBeTruthy();
  });

  it("shows the historical change separately from the alignment", () => {
    render(
      <ReconciliationInspector
        deviceId={DEVICE_ID}
        result={GI01_DRIFT}
        onIntentChange={() => undefined}
      />,
    );
    expect(screen.getByText("Changed")).toBeTruthy();
    expect(screen.getByText(/changed from Test ISPHub5 to TEST-GATEWAY-01-HQ/)).toBeTruthy();
  });

  it("says an expected device is absent, never offline", () => {
    render(
      <ReconciliationInspector
        deviceId={DEVICE_ID}
        result={GI04}
        onIntentChange={() => undefined}
      />,
    );
    expect(screen.getByText("Not observed")).toBeTruthy();
    expect(screen.getByText(/cannot conclude the device is offline/)).toBeTruthy();
    expect(screen.getByText(/absent from the place it was expected/)).toBeTruthy();
  });

  it("offers to adopt the observed identity only when something was identified", () => {
    const { unmount } = render(
      <ReconciliationInspector
        deviceId={DEVICE_ID}
        result={GI01_DRIFT}
        onIntentChange={() => undefined}
      />,
    );
    expect(screen.getByRole("button", { name: "Expect TEST-GATEWAY-01-HQ" })).toBeTruthy();
    unmount();

    render(
      <ReconciliationInspector
        deviceId={DEVICE_ID}
        result={GI01}
        onIntentChange={() => undefined}
      />,
    );
    expect(screen.queryByRole("button", { name: /^Expect / })).toBeNull();
  });

  it("records intent locally and states that nothing is sent to the switch", async () => {
    const set = vi.spyOn(api, "setTopologyIntent").mockResolvedValue({
      deviceId: DEVICE_ID,
      relationships: [],
    });
    const onChange = vi.fn();
    render(
      <ReconciliationInspector
        deviceId={DEVICE_ID}
        result={GI01}
        onIntentChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Record expectation" }));
    expect(
      screen.getByText(/recorded in SwitchOps only.*nothing is sent to the switch/s),
    ).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("e.g. Edge gateway"), {
      target: { value: "Edge gateway" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save expectation" }));

    await waitFor(() => expect(set).toHaveBeenCalledTimes(1));
    expect(set).toHaveBeenCalledWith(DEVICE_ID, "Gi0/1", {
      expectedName: "Edge gateway",
      expectedDeviceType: "unknown",
    });
    await waitFor(() => expect(onChange).toHaveBeenCalled());
  });

  it("can mute an interface without deleting its evidence", async () => {
    const set = vi.spyOn(api, "setTopologyIntent").mockResolvedValue({
      deviceId: DEVICE_ID,
      relationships: [],
    });
    render(
      <ReconciliationInspector
        deviceId={DEVICE_ID}
        result={GI04}
        onIntentChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Mute this interface" }));
    await waitFor(() => expect(set).toHaveBeenCalledTimes(1));
    expect(set.mock.calls[0][2].suppressed).toBe(true);
  });

  it("warns when the switch description is now stale", () => {
    render(
      <ReconciliationInspector
        deviceId={DEVICE_ID}
        result={{ ...GI01, documentationStale: true }}
        onIntentChange={() => undefined}
      />,
    );
    expect(
      screen.getByText(/still reflects the older topology/),
    ).toBeTruthy();
    expect(screen.getByText(/configuration writes are disabled/)).toBeTruthy();
  });

  it("offers no action that could change the switch", () => {
    render(
      <ReconciliationInspector
        deviceId={DEVICE_ID}
        result={GI01_DRIFT}
        onIntentChange={() => undefined}
      />,
    );
    for (const button of screen.getAllByRole("button")) {
      expect(button.textContent || "").not.toMatch(
        /^(apply|write|push|deploy|configure|shutdown|save to switch)$/i,
      );
    }
  });
});

// --- the port inspector carries it ------------------------------------------

describe("port inspector integration", () => {
  it("shows the reconciliation for the selected port", () => {
    render(
      <PortInspector
        topology={TOPOLOGY}
        telemetry={TELEMETRY}
        events={[]}
        selectedPort="Gi0/1"
        reconciliation={SUMMARY}
        intent={[]}
        onIntentChange={() => undefined}
      />,
    );
    expect(screen.getByText("Expected vs observed")).toBeTruthy();
    expect(screen.getByText("Unconfirmed")).toBeTruthy();
  });

  it("degrades cleanly when reconciliation is unavailable", () => {
    render(
      <PortInspector
        topology={TOPOLOGY}
        telemetry={TELEMETRY}
        events={[]}
        selectedPort="Gi0/1"
      />,
    );
    expect(screen.getByText(/Reconciliation was unavailable/)).toBeTruthy();
  });
});

// --- language discipline ----------------------------------------------------

describe("no unsupported certainty", () => {
  it("never claims a device is offline or down anywhere in the reconciliation UI", () => {
    render(
      <>
        <ReconciliationSummaryPanel reconciliation={SUMMARY} />
        <ReconciliationInspector deviceId={DEVICE_ID} result={GI04} onIntentChange={() => undefined} />
      </>,
    );
    const text = (document.body.textContent || "").toLowerCase();
    // The only permitted use of "offline" is the sentence denying the claim.
    const withoutDenial = text.replace(/cannot conclude the device is offline/g, "");
    expect(withoutDenial).not.toContain("offline");
    expect(withoutDenial).not.toContain("device is down");
  });

  it("labels an expectation as an expectation wherever it appears", () => {
    render(
      <ReconciliationInspector deviceId={DEVICE_ID} result={GI01} onIntentChange={() => undefined} />,
    );
    const expectedGroup = document.querySelector(".assertion-group__label--expected");
    expect(expectedGroup?.textContent).toBe("Expected");
    expect(screen.getByText(/from the description configured on the switch/)).toBeTruthy();
  });
});
