import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import EWPSObservatory from "@/components/EWPSObservatory";
import { api } from "@/lib/api";
import * as desktop from "@/lib/ewpsDesktop";
import type {
  EWPSCandidatePath,
  EWPSConfig,
  EWPSDecisionPoint,
  EWPSExperimentSession,
  EWPSLabStatus,
  EWPSMeta,
  EWPSSummary,
} from "@/lib/ewpsTypes";


const config: EWPSConfig = {
  lambda: 0.035,
  k: 0.08,
  alpha: 1,
  beta: 0.25,
  pPerfMin: 0.5,
  weights: { freshness: 0.4, stability: 0.35, density: 0.25 },
  hysteresis: { minimumImprovement: 0.08, minimumDwellSeconds: 30, minimumEvidenceSeconds: 15, recoveryHoldDownSeconds: 20 },
  latencyWeight: 1,
  jitterWeight: 0.5,
  lossWeight: 10,
  sampleIntervalSeconds: 5,
  probeCount: 5,
  rollingWindow: 12,
  lossWindowProbes: 50,
  unavailableFailureThreshold: 3,
  unavailableReprobeCycles: 6,
};

const meta: EWPSMeta = {
  modelVersion: "0.2.0",
  releaseId: "ewps-v0.2.0-alpha",
  mode: "SHADOW",
  modeLabel: "SHADOW MODE — RECOMMENDATIONS ONLY",
  changesNetworkState: false,
  confidenceSemantics: "separate heuristic indices; not a calibrated probability",
  topologyMappingVersion: "switchops-evidence-v1/ewps-map-v1",
  topologyMapping: {
    reciprocal_independent_direct: { score: 1, description: "proven" },
    one_sided_direct: { score: 0.85, description: "direct" },
    strong_inference: { score: 0.6, description: "strong" },
    weak_inference: { score: 0.3, description: "weak" },
    contradictory: { score: 0, description: "conflict" },
    unknown: { score: 0, description: "unknown" },
  },
  defaultConfig: config,
  fixedProbeTargetToken: "fixed-token",
  privacyBoundary: "no payloads",
  compatibility: { v01Replay: true, v01SemanticsPreserved: true, historicalRowsReinterpreted: false },
};

const candidates: EWPSCandidatePath[] = [
  { pathId: "lab-path-a", displayLabel: "Path A", adapterName: "Controlled logical Path A", sourceKind: "controlled_lab", lifecycle: "VIABLE", topologyEvidence: "reciprocal_independent_direct", topologyDetail: "Contained chain A.", diversityClaim: "No physical diversity claimed.", eligibleForLiveMeasurement: true },
  { pathId: "lab-path-b", displayLabel: "Path B", adapterName: "Controlled logical Path B", sourceKind: "controlled_lab", lifecycle: "VIABLE", topologyEvidence: "reciprocal_independent_direct", topologyDetail: "Contained chain B.", diversityClaim: "No physical diversity claimed.", eligibleForLiveMeasurement: true },
];

const lab: EWPSLabStatus = {
  available: true,
  ready: true,
  explicitStartRequired: true,
  architecture: "contained WSL2 network namespaces",
  diversityClaim: "Controlled logical test paths; no physical diversity.",
  message: "Both controlled logical paths independently returned validated telemetry.",
  scenarioId: "faster-epistemically-weak",
  scenarioPhase: 0,
  paths: [
    { pathId: "lab-path-a", displayLabel: "Path A", profile: "fast-stable", independentlyValidated: true, lastLatencyMs: 15 },
    { pathId: "lab-path-b", displayLabel: "Path B", profile: "slow-stable", independentlyValidated: true, lastLatencyMs: 25 },
  ],
};

const session: EWPSExperimentSession = {
  experimentId: "ewps-test",
  name: "Experiment 002",
  workloadLabel: "Amazon Prime Video",
  status: "COMPLETED",
  kind: "live",
  mode: "SHADOW",
  ewpsModelVersion: "0.2.0",
  releaseId: "ewps-v0.2.0-alpha",
  config,
  candidatePathIds: ["lab-path-a", "lab-path-b"],
  createdAt: "2026-08-25T00:00:00Z",
  startedAt: "2026-08-25T00:00:00Z",
  endedAt: "2026-08-25T00:10:00Z",
  totalMeasurements: 40,
  decisionPoints: 20,
};

function calculation(pathId: string, latency: number, confidence: number, eligible = true) {
  return {
    modelVersion: "0.2.0",
    pathId,
    raw: {
      latencyMs: latency,
      rollingLatencyMs: latency + 1,
      jitterMs: 2,
      rollingJitterMs: 3,
      lossPct: 20,
      rollingLossPct: 2,
      sampleCount: 5,
      lossSampleCount: 50,
      probeOutcomes: [true, true, true, true, false],
      reachable: true,
      routingMetricsUsable: true,
      telemetryState: "validated" as const,
      candidateLifecycle: "VIABLE" as const,
      transientFailure: false,
      candidateUnavailableEvent: false,
      recoveryEvent: false,
    },
    evidence: { ageSeconds: 0, meanMs: latency + 1, stddevMs: 3, effectiveSamples: 40, topologyEvidence: "reciprocal_independent_direct" as const },
    confidence: { freshness: 1, stability: 0.9, density: 0.95, performance: confidence, topology: 1, topologyPenalty: 1, indexDescription: "index" },
    rawCost: latency + 22,
    ewpsCost: (latency + 22) / confidence,
    eligible,
    eligibilityState: eligible ? "ELIGIBLE" : "PERFORMANCE_EVIDENCE_INSUFFICIENT",
    valid: true,
    reasons: [],
  };
}

const point: EWPSDecisionPoint = {
  timestamp: "2026-08-25T00:09:55Z",
  decisionIndex: 19,
  calculations: [calculation("lab-path-a", 15, 0.55), calculation("lab-path-b", 25, 0.95)],
  algorithms: [
    { algorithm: "lowest_latency", pathId: "lab-path-a", cost: 15, reason: "faster" },
    { algorithm: "lowest_loss", pathId: "lab-path-b", cost: 0, reason: "rolling" },
    { algorithm: "performance_only", pathId: "lab-path-a", cost: 37, reason: "raw" },
    { algorithm: "ewps", pathId: "lab-path-b", cost: 49, reason: "evidence" },
    { algorithm: "ewps_hysteresis", pathId: "lab-path-b", cost: 49, reason: "stable" },
  ],
  hysteresis: { preferredPathId: "lab-path-b", challengerPathId: "lab-path-b", recommendationChanged: true, suppressed: false, wouldSwitch: true, reason: "changed", switchBlockedBy: "shadow_mode" },
  events: ["algorithm_preference_crossing:lowest_latency", "rolling_loss_event:lab-path-a"],
  explanation: "Path A is faster, but stability limits its performance confidence. Path B has lower EWPS cost.",
};

const summary: EWPSSummary = {
  experimentId: "ewps-test",
  durationSeconds: 600,
  totalSamples: 40,
  decisionPoints: 20,
  measurementsPerPath: { "lab-path-a": 20, "lab-path-b": 20 },
  usablePathCountOverTime: [{ timestamp: point.timestamp, count: 2 }],
  unavailableCandidateCount: 0,
  candidateUnavailableEvents: 0,
  transientFailuresOnViablePaths: 1,
  recoveryEvents: 1,
  performanceConfidencePerPath: { "lab-path-a": { minimum: 0.4, average: 0.7, maximum: 0.95 }, "lab-path-b": { minimum: 0.8, average: 0.9, maximum: 0.97 } },
  topologyConfidencePerPath: { "lab-path-a": { minimum: 1, average: 1, maximum: 1 }, "lab-path-b": { minimum: 1, average: 1, maximum: 1 } },
  ewpsCostDistributionPerPath: {}, rawCostDistributionPerPath: {},
  algorithmDisagreementPercentage: 45,
  pairwiseDisagreementMatrix: {}, preferenceDurationSecondsPerAlgorithmPath: {}, recommendationSwitchesPerAlgorithm: {},
  hysteresisSuppressedSwitches: 3,
  belowEvidenceThresholdSecondsPerPath: { "lab-path-a": 30, "lab-path-b": 0 },
  rollingLossEvents: 1, staleEvidenceEvents: 1,
  ewpsVsLowestLatencyDifferencePercentage: 40,
  disagreementEvidenceComponents: { stability: 8 }, mostCommonDisagreementComponent: "stability",
  notableDecisionEvents: [],
};


function mockBase(current: EWPSExperimentSession | null = session) {
  vi.spyOn(api, "ewpsMeta").mockResolvedValue(meta);
  vi.spyOn(api, "ewpsCandidates").mockResolvedValue(candidates);
  vi.spyOn(api, "ewpsCurrent").mockResolvedValue(current);
  vi.spyOn(api, "ewpsSimulatorScenarios").mockResolvedValue([{ scenarioId: "faster-epistemically-weak", name: "Faster but weak", description: "comparison", expectedResearchPattern: "possible disagreement" }]);
  vi.spyOn(api, "ewpsLabStatus").mockResolvedValue(lab);
  vi.spyOn(api, "ewpsTimeline").mockResolvedValue({ session, decisions: [point] });
  vi.spyOn(api, "ewpsSummary").mockResolvedValue(summary);
}


describe("EWPS v0.2 Observatory", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(desktop, "isTauriDesktop").mockReturnValue(false);
  });
  afterEach(() => cleanup());

  it("renders the shadow boundary, controlled path proof, dual metrics, and algorithm disagreement", async () => {
    mockBase();
    render(<EWPSObservatory />);
    expect(await screen.findByText("SHADOW MODE — RECOMMENDATIONS ONLY")).toBeTruthy();
    expect(screen.getByText(/Both controlled logical paths independently/)).toBeTruthy();
    expect(screen.getAllByText(/INDEPENDENT TELEMETRY VERIFIED/)).toHaveLength(2);
    expect(await screen.findAllByText("RAW LATENCY")).toHaveLength(2);
    expect(screen.getAllByText("ROLLING LATENCY")).toHaveLength(2);
    expect(screen.getByText("performance only")).toBeTruthy();
    expect(screen.getByText(/Path A is faster/)).toBeTruthy();
  });

  it("shows a successful export popup with the exact selectable path and browser fallback", async () => {
    mockBase();
    vi.spyOn(api, "ewpsSaveExport").mockResolvedValue({ savedPath: "C:\\Users\\tester\\AppData\\Local\\SwitchOps\\data\\ewps-exports\\ewps-test.json", filename: "ewps-test.json", format: "json", folderOpenAvailable: true });
    render(<EWPSObservatory />);
    fireEvent.click(await screen.findByRole("button", { name: "Export JSON" }));
    expect(await screen.findByRole("status", { name: "Export saved" })).toBeTruthy();
    expect(screen.getByLabelText("Saved export path").textContent).toContain("ewps-test.json");
    const open = screen.getByRole("button", { name: "Open export folder" }) as HTMLButtonElement;
    expect(open.disabled).toBe(true);
    expect(screen.getByText(/browser\/source mode/)).toBeTruthy();
  });

  it("uses the narrow allowlisted Tauri command for Open export folder", async () => {
    vi.restoreAllMocks();
    vi.spyOn(desktop, "isTauriDesktop").mockReturnValue(true);
    const open = vi.spyOn(desktop, "openEWPSExportFolder").mockResolvedValue("C:\\fixed\\ewps-exports");
    mockBase();
    vi.spyOn(api, "ewpsSaveExport").mockResolvedValue({ savedPath: "C:\\fixed\\ewps-exports\\ewps-test.csv", filename: "ewps-test.csv", format: "csv", folderOpenAvailable: true });
    render(<EWPSObservatory />);
    fireEvent.click(await screen.findByRole("button", { name: "Export CSV" }));
    fireEvent.click(await screen.findByRole("button", { name: "Open export folder" }));
    await waitFor(() => expect(open).toHaveBeenCalledOnce());
  });

  it("never silently claims success when export fails", async () => {
    mockBase();
    vi.spyOn(api, "ewpsSaveExport").mockRejectedValue(new Error("disk unavailable"));
    render(<EWPSObservatory />);
    fireEvent.click(await screen.findByRole("button", { name: "Export JSONL" }));
    const alert = await screen.findByRole("alert", { name: "Export failed" });
    expect(alert.textContent).toContain("disk unavailable");
    expect(screen.queryByText("EXPORT SAVED")).toBeNull();
  });

  it("dismisses the export notice with Escape and remains usable at a narrow viewport", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    mockBase();
    vi.spyOn(api, "ewpsSaveExport").mockResolvedValue({ savedPath: "C:\\fixed\\ewps-test.jsonl", filename: "ewps-test.jsonl", format: "jsonl", folderOpenAvailable: true });
    render(<EWPSObservatory />);
    fireEvent.click(await screen.findByRole("button", { name: "Export JSONL" }));
    expect(await screen.findByRole("status", { name: "Export saved" })).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("status", { name: "Export saved" })).toBeNull());
    expect(document.querySelector(".ewps-path-grid")).toBeTruthy();
  });

  it("renders the empty candidate state and contained-lab creation action", async () => {
    mockBase(null);
    vi.mocked(api.ewpsCandidates).mockResolvedValue([]);
    vi.mocked(api.ewpsLabStatus).mockResolvedValue({ ...lab, ready: false, paths: [], message: "Explicit creation required." });
    render(<EWPSObservatory />);
    expect(await screen.findByText(/No active real interface/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create contained lab" })).toBeTruthy();
  });
});
