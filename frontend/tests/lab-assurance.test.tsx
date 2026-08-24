import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import LabAssurancePanel from "@/components/LabAssurancePanel";
import { api } from "@/lib/api";
import type { LabAssuranceState } from "@/lib/labTypes";

const now = "2026-08-24T12:00:00Z";
const state: LabAssuranceState = {
  generatedAt: now,
  collectionState: "CURRENT",
  summary: { observedDevices: 2, physicalEdges: 1, logicalNetworks: 1, criticalFindings: 0, warningFindings: 1, unknownFindings: 1, evidenceGaps: 3 },
  devices: [
    { id: "a", label: "SYNTH-SW1", role: "SWITCH", provider: "cisco-ios", model: "C9200", software: "17.9", primary: true, observed: true, collectionState: "CURRENT", detail: "Collected.", evidenceIds: ["ev-a"] },
    { id: "b", label: "SYNTH-SW2", role: "SWITCH", provider: "cisco-ios", model: "C9200", software: "17.9", primary: false, observed: true, collectionState: "CURRENT", detail: "Collected.", evidenceIds: ["ev-b"] },
  ],
  interfaces: [],
  edges: [{ id: "edge-a-b", fromNodeId: "a", toNodeId: "b", fromInterface: "Gi1/0/1", toInterface: "Gi1/0/1", kind: "PHYSICAL", state: "PROVEN", confidence: "CONFIRMED", reciprocal: true, detail: "Reciprocal CDP agrees.", evidenceIds: ["ev-a", "ev-b"] }],
  logicalNetworks: [{ id: "vlan-10", vlanId: 10, name: "USERS", vrf: null, gatewayNodes: [], memberInterfaces: [], trunkInterfaces: [], endpointNodes: [], isolationState: "POLICY_UNKNOWN", detail: "Policy not proven.", evidenceIds: ["ev-vlan"] }],
  capabilities: [{ id: "cap-stp", deviceId: "a", name: "Spanning Tree", state: "SUPPORTED", configured: null, observed: true, detail: "Observed.", evidenceIds: ["ev-stp"] }],
  findings: [{ id: "finding-uplink", category: "RESILIENCY", severity: "WARNING", confidence: "HIGH", title: "One observed infrastructure path", detail: "Only one path is observed.", consequence: "A failure may isolate the switch.", remediation: "Confirm a second path.", affectedIds: ["a"], evidenceIds: ["ev-a"] }],
  failures: [{ id: "failure-edge", targetId: "edge-a-b", targetKind: "UPLINK", title: "Loss of Gi1/0/1", confidence: "CONFIRMED", consequences: ["One switch becomes unreachable."], affectedIds: ["b"], controlImpact: "Observation may be lost.", evidenceIds: ["ev-a"] }],
  paths: [{ id: "path-a-b", fromNodeId: "a", toNodeId: "b", state: "PROVEN", summary: "One proven hop.", hops: [{ nodeId: "a", label: "SYNTH-SW1", viaInterface: null, state: "PROVEN", evidenceIds: [] }, { nodeId: "b", label: "SYNTH-SW2", viaInterface: "Gi1/0/1", state: "PROVEN", evidenceIds: ["ev-a"] }], evidenceIds: ["ev-a"] }],
  performance: [],
  evidence: [{ id: "ev-a", deviceId: "a", kind: "OBSERVED", command: "show_cdp_neighbors_detail", confidence: "CONFIRMED", observedAt: now, current: true, detail: "Observed." }],
  limitations: ["Unknown paths remain unknown."],
};

afterEach(() => vi.restoreAllMocks());

describe("Lab Assurance", () => {
  it("presents one coherent tabbed feature without a health score", async () => {
    vi.spyOn(api, "labAssuranceState").mockResolvedValue(state);
    vi.spyOn(api, "labAssuranceDevices").mockResolvedValue({ keyringAvailable: true, devices: [] });
    render(<LabAssurancePanel />);

    expect(await screen.findByText("Know what survives before something fails")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Failure domains" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Capabilities" })).toBeTruthy();
    expect(document.body.textContent?.toLowerCase()).not.toContain("health score");

    fireEvent.click(screen.getByRole("button", { name: "Topology" }));
    expect(screen.getAllByText("SYNTH-SW1").length).toBeGreaterThan(0);
    expect(screen.getByText("Reciprocal CDP agrees.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Segmentation" }));
    expect(screen.getByText("POLICY UNKNOWN")).toBeTruthy();
    expect(screen.getByText(/Separate VLANs are separate broadcast domains/)).toBeTruthy();
  });
});
