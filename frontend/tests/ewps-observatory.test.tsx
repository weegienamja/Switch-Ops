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
  sampleIntervalSemantics: "non-overlapping measurement-cycle start-to-start cadence",
  fixedProbeTargetToken: "fixed-token",
  privacyBoundary: "no payloads",
  compatibility: { v01Replay: true, v01SemanticsPreserved: true, historicalRowsReinterpreted: false, cadenceInstrumentationAdditive: true },
};

const candidates: EWPSCandidatePath[] = [
  { pathId: "lab-path-a", displayLabel: "Controlled Path A", adapterName: "Fast Stable", sourceKind: "controlled_lab", lifecycle: "VIABLE", topologyEvidence: "reciprocal_independent_direct", topologyDetail: "Contained chain A.", diversityClaim: "No physical diversity claimed.", eligibleForLiveMeasurement: true },
  { pathId: "lab-path-b", displayLabel: "Controlled Path B", adapterName: "Slow Stable", sourceKind: "controlled_lab", lifecycle: "VIABLE", topologyEvidence: "reciprocal_independent_direct", topologyDetail: "Contained chain B.", diversityClaim: "No physical diversity claimed.", eligibleForLiveMeasurement: true },
];

const lab: EWPSLabStatus = {
  available: true,
  ready: true,
  state: "LAB_READY",
  prerequisitesPassed: true,
  labInstanceId: "11111111-1111-4111-8111-111111111111",
  topologyVersion: "switchops-ewps-contained-dual-path-v1",
  explicitStartRequired: true,
  architecture: "contained WSL2 network namespaces",
  diversityClaim: "Controlled logical test paths; no physical diversity.",
  message: "Both controlled logical paths independently returned validated telemetry.",
  scenarioId: "faster-epistemically-weak",
  scenarioPhase: 0,
  scenarioPhaseId: "baseline",
  scenarioPhaseCount: 3,
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
  sourceMode: "CONTROLLED_DUAL_PATH",
  candidatePathIds: ["lab-path-a", "lab-path-b"],
  candidateSnapshot: candidates.map(({ pathId, displayLabel, adapterName, sourceKind, topologyEvidence, topologyDetail, diversityClaim }) => ({ pathId, displayLabel, adapterName, sourceKind, topologyEvidence, topologyDetail, diversityClaim })),
  labInstanceId: lab.labInstanceId,
  labTopologyVersion: lab.topologyVersion,
  initialVerificationStatus: "VERIFIED",
  controlledImpairmentScenario: "faster-epistemically-weak",
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
  cadence: {
    configuredIntervalSeconds: 5,
    cycleStartedAt: "2026-08-25T00:09:50Z",
    cycleCompletedAt: "2026-08-25T00:09:54Z",
    collectionDurationMs: 4000,
    actualStartToStartSeconds: 5.01,
    cadenceOverrunCount: 0,
  },
};

const summary: EWPSSummary = {
  experimentId: "ewps-test",
  durationSeconds: 600,
  totalSamples: 40,
  decisionPoints: 20,
  configuredIntervalSeconds: 5,
  observedStartToStartSeconds: { minimum: 4.99, mean: 5.01, median: 5, maximum: 5.04 },
  observedCollectionDurationMs: { minimum: 3990, mean: 4010, median: 4000, maximum: 4060 },
  cadenceOverrunCount: 0,
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
  phaseSummaries: [],
};


function mockBase(current: EWPSExperimentSession | null = session) {
  vi.spyOn(api, "ewpsMeta").mockResolvedValue(meta);
  vi.spyOn(api, "ewpsCandidates").mockResolvedValue(candidates);
  vi.spyOn(api, "ewpsCurrent").mockResolvedValue(current);
  vi.spyOn(api, "ewpsSimulatorScenarios").mockResolvedValue([{ scenarioId: "faster-epistemically-weak", name: "Faster but weak", description: "comparison", expectedResearchPattern: "possible disagreement" }]);
  vi.spyOn(api, "ewpsLabStatus").mockResolvedValue(lab);
  vi.spyOn(api, "ewpsTimeline").mockResolvedValue({ session, decisions: [point], events: [] });
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
    expect(screen.getByText("OBSERVED CADENCE")).toBeTruthy();
    expect(screen.getAllByText("5.01 s").length).toBeGreaterThan(0);
  });

  it("offers a generic background streaming workload without rewriting session history", async () => {
    mockBase(null);
    render(<EWPSObservatory />);
    expect(await screen.findByRole("option", { name: "Background streaming" })).toBeTruthy();
    expect(session.workloadLabel).toBe("Amazon Prime Video");
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
    vi.mocked(api.ewpsLabStatus).mockResolvedValue({ ...lab, ready: false, state: "LAB_NOT_CREATED", prerequisitesPassed: false, labInstanceId: null, paths: [], message: "Explicit creation required." });
    render(<EWPSObservatory />);
    expect(await screen.findByText(/Create, verify, and prepare/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create contained lab" })).toBeTruthy();
  });

  it("E2E regression: a controlled run cannot inherit real-interface candidates from the pre-lab screen", async () => {
    const real: EWPSCandidatePath = { pathId: "path-real-a", displayLabel: "Real Path A", adapterName: "Ethernet", sourceKind: "real_interface", lifecycle: "VIABLE", topologyEvidence: "weak_inference", topologyDetail: "Local attachment.", diversityClaim: "No physical diversity claimed.", eligibleForLiveMeasurement: true };
    const notCreated: EWPSLabStatus = { ...lab, ready: false, state: "LAB_NOT_CREATED", prerequisitesPassed: false, labInstanceId: null, scenarioId: null, paths: [], message: "LAB NOT CREATED." };
    mockBase(null);
    vi.mocked(api.ewpsCandidates).mockResolvedValueOnce([real]).mockResolvedValue([real, ...candidates]);
    vi.mocked(api.ewpsLabStatus).mockResolvedValue(notCreated);
    vi.spyOn(api, "ewpsLabCreate").mockResolvedValue(lab);
    const created = { ...session, status: "CREATED" as const, startedAt: null, endedAt: null };
    const running = { ...created, status: "RUNNING" as const, startedAt: "2026-08-25T00:00:00Z" };
    const create = vi.spyOn(api, "ewpsCreate").mockResolvedValue(created);
    vi.spyOn(api, "ewpsStart").mockResolvedValue(running);
    render(<EWPSObservatory />);
    expect(await screen.findByText(/Create, verify, and prepare/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Start experiment" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Create contained lab" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "Start experiment" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Start experiment" }));
    await waitFor(() => expect(create).toHaveBeenCalledOnce());
    expect(create).toHaveBeenCalledWith(expect.objectContaining({
      sourceMode: "CONTROLLED_DUAL_PATH",
      candidatePathIds: ["lab-path-a", "lab-path-b"],
      controlledScenario: "faster-epistemically-weak",
    }));
    expect(create.mock.calls[0][0].candidatePathIds).not.toContain("path-real-a");
  });

  it("does not present a controlled running session as merely not yet verified", async () => {
    mockBase({ ...session, status: "RUNNING", endedAt: null });
    vi.mocked(api.ewpsLabStatus).mockResolvedValue({
      ...lab,
      ready: false,
      state: "LAB_LOST",
      paths: lab.paths.map((path) => ({ ...path, independentlyValidated: false })),
      message: "CONTROLLED LAB LOST.",
    });
    render(<EWPSObservatory />);
    expect(await screen.findByText("CONTROLLED LAB LOST.")).toBeTruthy();
    expect(screen.queryByText("NOT YET VERIFIED")).toBeNull();
    expect(screen.getAllByText("CONTROLLED LAB LOST")).toHaveLength(2);
  });

  it("E2E phase provenance: advances explicitly and renders the immutable transition marker", async () => {
    const running = { ...session, status: "RUNNING" as const, endedAt: null };
    const pathProfiles = {
      "lab-path-a": { requestedProfileId: "fast-noisy" as const, appliedProfileId: "fast-noisy" as const, requestedConfiguration: { kind: "netem" as const, delayMs: 8, jitterMs: 12, delayCorrelationPct: 25, distribution: "normal" }, appliedConfiguration: { kind: "netem" as const, delayMs: 8, jitterMs: 12, delayCorrelationPct: 25 }, verification: "PASSED" as const, verificationDetail: "verified" },
      "lab-path-b": { requestedProfileId: "slow-stable" as const, appliedProfileId: "slow-stable" as const, requestedConfiguration: { kind: "netem" as const, delayMs: 28, jitterMs: .5, delayCorrelationPct: 10 }, appliedConfiguration: { kind: "netem" as const, delayMs: 28, jitterMs: .5, delayCorrelationPct: 10 }, verification: "PASSED" as const, verificationDetail: "verified" },
    };
    const event = { eventId: "ewps-event-one", eventType: "SCENARIO_PHASE_CHANGED" as const, timestamp: "2026-08-25T03:07:13Z", completedAt: "2026-08-25T03:07:13.020Z", experimentId: session.experimentId, scenarioId: "faster-epistemically-weak" as const, previousPhaseIndex: 0, previousPhaseId: "baseline", newPhaseIndex: 1, newPhaseId: "fast-noisy", applicationSucceeded: true, labInstanceId: "11111111-1111-4111-8111-111111111111", affectedPathIds: ["lab-path-a"], pathProfiles, verification: "PASSED" as const, detail: "verified" };
    const noisyPoint = { ...point, decisionIndex: 2, timestamp: "2026-08-25T03:07:18Z", scenarioPhase: { scenarioId: "faster-epistemically-weak" as const, phaseIndex: 1, phaseId: "fast-noisy", labInstanceId: event.labInstanceId, pathProfiles } };
    mockBase(running);
    vi.mocked(api.ewpsTimeline).mockResolvedValue({ session: running, decisions: [point, noisyPoint], events: [event] });
    vi.spyOn(api, "ewpsLabAdvanceScenario").mockResolvedValue({ status: { ...lab, scenarioPhase: 1, scenarioPhaseId: "fast-noisy" }, event });
    render(<EWPSObservatory />);
    const advance = await screen.findByRole("button", { name: "Advance to Phase 2" });
    expect((advance as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(advance);
    expect(await screen.findByText("Scenario phase changed")).toBeTruthy();
    expect(screen.getByText("Baseline → Fast Noisy")).toBeTruthy();
    expect(screen.getAllByText("Fast Noisy").length).toBeGreaterThan(0);
  });
});
