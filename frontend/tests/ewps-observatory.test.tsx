import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import EWPSObservatory from "@/components/EWPSObservatory";
import { api } from "@/lib/api";
import type { EWPSConfig, EWPSDecisionPoint, EWPSExperimentSession, EWPSMeta } from "@/lib/ewpsTypes";


const config: EWPSConfig = {
  lambda: 0.035,
  k: 0.35,
  alpha: 1,
  pMin: 0.25,
  certaintyMode: "product",
  weights: { freshness: 1, stability: 1, density: 1, topology: 1 },
  hysteresis: { minimumImprovement: 0.08, minimumDwellSeconds: 30, minimumEvidenceSeconds: 15, recoveryHoldDownSeconds: 20 },
  latencyWeight: 1,
  jitterWeight: 0.5,
  lossWeight: 10,
  sampleIntervalSeconds: 5,
  probeCount: 3,
  rollingWindow: 12,
};

const meta: EWPSMeta = {
  modelVersion: "0.1.0",
  releaseId: "ewps-v0.1.0-alpha",
  mode: "SHADOW",
  changesNetworkState: false,
  confidenceSemantics: "dimensionless evidence-confidence index; not a calibrated probability",
  topologyMappingVersion: "switchops-evidence-v1/ewps-map-v1",
  topologyMapping: {
    reciprocal_independent_direct: { score: 1, description: "strongest" },
    one_sided_direct: { score: 0.85, description: "high" },
    strong_inference: { score: 0.6, description: "medium" },
    weak_inference: { score: 0.3, description: "low" },
    contradictory: { score: 0, description: "unusable" },
    unknown: { score: 0, description: "unknown" },
  },
  defaultConfig: config,
  fixedProbeTargetToken: "probe-token",
  privacyBoundary: "No payloads.",
};

const candidates = [
  { pathId: "path-a", displayLabel: "Path A", adapterName: "Ethernet", topologyEvidence: "reciprocal_independent_direct" as const, topologyDetail: "Reciprocal evidence.", eligibleForLiveMeasurement: true },
  { pathId: "path-b", displayLabel: "Path B", adapterName: "Wi-Fi", topologyEvidence: "one_sided_direct" as const, topologyDetail: "One-sided evidence.", eligibleForLiveMeasurement: true },
];

const session: EWPSExperimentSession = {
  experimentId: "ewps-test",
  name: "Streaming comparison",
  workloadLabel: "Netflix",
  status: "RUNNING",
  kind: "live",
  mode: "SHADOW",
  ewpsModelVersion: "0.1.0",
  releaseId: "ewps-v0.1.0-alpha",
  config,
  candidatePathIds: ["path-a", "path-b"],
  createdAt: "2026-08-24T12:00:00Z",
  startedAt: "2026-08-24T12:00:00Z",
  totalMeasurements: 2,
  decisionPoints: 1,
};

const decision: EWPSDecisionPoint = {
  timestamp: "2026-08-24T12:00:05Z",
  decisionIndex: 0,
  calculations: [
    {
      modelVersion: "0.1.0", pathId: "path-a",
      raw: { latencyMs: 16, jitterMs: 8, lossPct: 0, sampleCount: 3, reachable: true },
      evidence: { ageSeconds: 0, meanMs: 22, stddevMs: 10, effectiveSamples: 3, topologyEvidence: "reciprocal_independent_direct" },
      certainty: { freshness: 1, stability: 0.3, density: 0.65, topology: 1, composite: 0.19, indexDescription: "dimensionless evidence-confidence index" },
      rawCost: 20, ewpsCost: null, eligible: false, valid: true, reasons: ["below_minimum_evidence"],
    },
    {
      modelVersion: "0.1.0", pathId: "path-b",
      raw: { latencyMs: 25, jitterMs: 1, lossPct: 0, sampleCount: 3, reachable: true },
      evidence: { ageSeconds: 0, meanMs: 25, stddevMs: 1, effectiveSamples: 20, topologyEvidence: "one_sided_direct" },
      certainty: { freshness: 1, stability: 0.99, density: 0.99, topology: 0.85, composite: 0.83, indexDescription: "dimensionless evidence-confidence index" },
      rawCost: 25.5, ewpsCost: 30.72, eligible: true, valid: true, reasons: [],
    },
  ],
  algorithms: [
    { algorithm: "lowest_latency", pathId: "path-a", cost: 16, reason: "Lowest latency." },
    { algorithm: "lowest_loss", pathId: "path-a", cost: 0, reason: "Loss tied." },
    { algorithm: "performance_only", pathId: "path-a", cost: 20, reason: "Lowest performance cost." },
    { algorithm: "ewps", pathId: "path-b", cost: 30.72, reason: "Lowest EWPS cost." },
    { algorithm: "ewps_hysteresis", pathId: "path-b", cost: 30.72, reason: "Stable recommendation." },
  ],
  hysteresis: { preferredPathId: "path-b", challengerPathId: "path-b", recommendationChanged: true, suppressed: false, wouldSwitch: false, reason: "Initial recommendation.", switchBlockedBy: "shadow_mode" },
  events: ["path_became_ineligible:path-a", "ewps_recommendation_change"],
  explanation: "Path A remains nominally faster, but its evidence-confidence index fell. Path B now has the lower eligible evidence-weighted cost.",
};

function commonMocks(current: EWPSExperimentSession | null = null) {
  vi.spyOn(api, "ewpsMeta").mockResolvedValue(meta);
  vi.spyOn(api, "ewpsCandidates").mockResolvedValue(candidates);
  vi.spyOn(api, "ewpsCurrent").mockResolvedValue(current);
  vi.spyOn(api, "ewpsSimulatorScenarios").mockResolvedValue([{ scenarioId: "latency-flap-ewps-stable", name: "Latency flapping", description: "Flapping comparison." }]);
  vi.spyOn(api, "ewpsTimeline").mockResolvedValue({ session, decisions: [decision] });
}

afterEach(() => vi.restoreAllMocks());

describe("EWPS Observatory", () => {
  it("keeps shadow mode and the non-probability boundary unmistakable", async () => {
    commonMocks();
    const create = vi.spyOn(api, "ewpsCreate");
    render(<EWPSObservatory />);
    expect(await screen.findByText("EWPS Observatory")).toBeTruthy();
    expect(screen.getByText("SHADOW MODE — NO ROUTING CHANGES")).toBeTruthy();
    expect(screen.getByText(/not a statistically calibrated probability/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start experiment" })).toBeTruthy();
    expect(create).not.toHaveBeenCalled();
  });

  it("does not auto-start and sends exact model parameters only after operator action", async () => {
    commonMocks();
    const created = { ...session, status: "CREATED" as const, totalMeasurements: 0, decisionPoints: 0 };
    vi.spyOn(api, "ewpsCreate").mockResolvedValue(created);
    vi.spyOn(api, "ewpsStart").mockResolvedValue(session);
    render(<EWPSObservatory />);
    const start = await screen.findByRole("button", { name: "Start experiment" });
    expect(api.ewpsCreate).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("risk aversion"), { target: { value: "1.5" } });
    fireEvent.click(start);
    await waitFor(() => expect(api.ewpsCreate).toHaveBeenCalledTimes(1));
    expect(api.ewpsCreate).toHaveBeenCalledWith(expect.objectContaining({
      workloadLabel: "YouTube 4K",
      candidatePathIds: ["path-a", "path-b"],
      config: expect.objectContaining({ alpha: 1.5 }),
    }));
    await waitFor(() => expect(api.ewpsStart).toHaveBeenCalledWith("ewps-test"));
  });

  it("renders eligible, ineligible, disagreement, event, and explanation states without infinity", async () => {
    commonMocks(session);
    render(<EWPSObservatory />);
    expect(await screen.findByText("Streaming comparison")).toBeTruthy();
    expect(screen.getAllByText("INELIGIBLE").length).toBeGreaterThan(0);
    expect(screen.getAllByText("ELIGIBLE").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Path A/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Path B/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Path A remains nominally faster/)).toBeTruthy();
    expect(screen.getByText(/path became ineligible/i)).toBeTruthy();
    expect(document.body.textContent).not.toContain("Infinity");
    expect(document.querySelector(".ewps-chart-grid")).toBeTruthy();
    expect(document.querySelector(".ewps-path-grid")).toBeTruthy();
  });

  it("keeps simulator usable when no live adapter is available", async () => {
    commonMocks();
    vi.mocked(api.ewpsCandidates).mockResolvedValue([]);
    vi.spyOn(api, "ewpsRunSimulator").mockResolvedValue({
      scenario: { scenarioId: "latency-flap-ewps-stable", name: "Latency flapping", description: "Flapping comparison." },
      config,
      decisions: [decision],
      summary: { suppressedRecommendations: 2, shadowMode: true },
    });
    render(<EWPSObservatory />);
    expect(await screen.findByText(/No active non-loopback IPv4 adapter/)).toBeTruthy();
    const run = screen.getByRole("button", { name: "Run deterministic scenario" });
    fireEvent.click(run);
    expect(await screen.findByText(/1 decisions · 2 suppressions/)).toBeTruthy();
  });
});
