export type TopologyEvidenceKey =
  | "reciprocal_independent_direct"
  | "one_sided_direct"
  | "strong_inference"
  | "weak_inference"
  | "contradictory"
  | "unknown";

export type EWPSCandidateLifecycle =
  | "VIABLE"
  | "PROBING"
  | "PERSISTENTLY_UNAVAILABLE"
  | "RECOVERING"
  | "DISABLED";

export type EWPSSourceMode =
  | "REAL_INTERFACES"
  | "CONTROLLED_DUAL_PATH"
  | "SIMULATOR"
  | "LEGACY_UNBOUND";

export interface EWPSWeights {
  freshness: number;
  stability: number;
  density: number;
}

export interface EWPSHysteresisConfig {
  minimumImprovement: number;
  minimumDwellSeconds: number;
  minimumEvidenceSeconds: number;
  recoveryHoldDownSeconds: number;
}

export interface EWPSConfig {
  lambda: number;
  k: number;
  alpha: number;
  beta: number;
  pPerfMin: number;
  weights: EWPSWeights;
  hysteresis: EWPSHysteresisConfig;
  latencyWeight: number;
  jitterWeight: number;
  lossWeight: number;
  sampleIntervalSeconds: number;
  probeCount: number;
  rollingWindow: number;
  lossWindowProbes: number;
  unavailableFailureThreshold: number;
  unavailableReprobeCycles: number;
}

export interface EWPSMeta {
  modelVersion: "0.2.0";
  releaseId: "ewps-v0.2.0-alpha" | "ewps-v0.2.1-alpha" | "ewps-v0.2.2-alpha" | "ewps-v0.2.3-alpha" | "ewps-v0.2.4-alpha";
  mode: "SHADOW";
  modeLabel: string;
  changesNetworkState: false;
  confidenceSemantics: string;
  topologyMappingVersion: string;
  topologyMapping: Record<TopologyEvidenceKey, { score: number; description: string }>;
  defaultConfig: EWPSConfig;
  sampleIntervalSemantics: string;
  fixedProbeTargetToken: string;
  privacyBoundary: string;
  compatibility: {
    v01Replay: boolean;
    v01SemanticsPreserved: boolean;
    historicalRowsReinterpreted: boolean;
    cadenceInstrumentationAdditive: boolean;
    scenarioPhaseProvenanceAdditive?: boolean;
  };
}

export interface EWPSCandidatePath {
  pathId: string;
  displayLabel: string;
  adapterName: string;
  sourceKind: "real_interface" | "controlled_lab";
  lifecycle: EWPSCandidateLifecycle;
  topologyEvidence: TopologyEvidenceKey;
  topologyDetail: string;
  diversityClaim: string;
  reachable?: boolean | null;
  eligibleForLiveMeasurement: boolean;
}

export interface EWPSCandidateSnapshot {
  pathId: string;
  displayLabel: string;
  adapterName: string;
  sourceKind: "real_interface" | "controlled_lab";
  topologyEvidence: TopologyEvidenceKey;
  topologyDetail: string;
  diversityClaim: string;
}

export interface EWPSExperimentSession {
  experimentId: string;
  name: string;
  workloadLabel: string;
  status: "CREATED" | "RUNNING" | "PAUSED" | "COMPLETED";
  kind: "live" | "simulator";
  mode: "SHADOW";
  ewpsModelVersion: string;
  releaseId: string;
  config: EWPSConfig;
  sourceMode: EWPSSourceMode;
  candidatePathIds: string[];
  candidateSnapshot: EWPSCandidateSnapshot[];
  labInstanceId?: string | null;
  labTopologyVersion?: string | null;
  initialVerificationStatus: "VERIFIED" | "NOT_APPLICABLE" | "LEGACY_UNKNOWN";
  controlledImpairmentScenario?: EWPSLabScenario | null;
  initialScenarioPhase?: EWPSScenarioPhase | null;
  createdAt: string;
  startedAt?: string | null;
  endedAt?: string | null;
  pausedAt?: string | null;
  totalMeasurements: number;
  decisionPoints: number;
}

export interface EWPSRawMetrics {
  latencyMs?: number | null;
  rollingLatencyMs?: number | null;
  jitterMs?: number | null;
  rollingJitterMs?: number | null;
  lossPct?: number | null;
  rollingLossPct?: number | null;
  sampleCount: number;
  lossSampleCount: number;
  probeOutcomes: boolean[];
  reachable?: boolean | null;
  routingMetricsUsable: boolean;
  telemetryState: "validated" | "transient_failure" | "candidate_unavailable" | "reprobe_deferred" | "evidence_stale" | "controlled_lab_lost";
  candidateLifecycle: EWPSCandidateLifecycle;
  transientFailure: boolean;
  candidateUnavailableEvent: boolean;
  recoveryEvent: boolean;
}

export interface EWPSEvidenceInput {
  ageSeconds?: number | null;
  meanMs?: number | null;
  stddevMs?: number | null;
  effectiveSamples: number;
  topologyEvidence: TopologyEvidenceKey;
  collectionStartedAt?: string | null;
  observationValidatedAt?: string | null;
  collectionDurationMs?: number | null;
}

export interface EWPSCalculation {
  modelVersion: string;
  pathId: string;
  raw: EWPSRawMetrics;
  evidence: EWPSEvidenceInput;
  confidence: {
    freshness: number;
    stability: number;
    density: number;
    performance: number;
    topology: number;
    topologyPenalty: number;
    indexDescription: string;
  };
  rawCost?: number | null;
  ewpsCost?: number | null;
  eligible: boolean;
  eligibilityState: string;
  valid: boolean;
  reasons: string[];
}

export interface EWPSAlgorithmChoice {
  algorithm: "lowest_latency" | "lowest_loss" | "performance_only" | "ewps" | "ewps_hysteresis";
  pathId?: string | null;
  cost?: number | null;
  reason: string;
}

export interface EWPSDecisionPoint {
  timestamp: string;
  decisionIndex: number;
  calculations: EWPSCalculation[];
  algorithms: EWPSAlgorithmChoice[];
  hysteresis: {
    preferredPathId?: string | null;
    challengerPathId?: string | null;
    recommendationChanged: boolean;
    suppressed: boolean;
    wouldSwitch: boolean;
    reason: string;
    switchBlockedBy: string;
  };
  events: string[];
  explanation: string;
  cadence?: {
    configuredIntervalSeconds: number;
    cycleStartedAt: string;
    cycleCompletedAt: string;
    collectionDurationMs: number;
    actualStartToStartSeconds?: number | null;
    cadenceOverrunCount: number;
  } | null;
  scenarioPhase?: EWPSScenarioPhase | null;
}

export interface EWPSNormalizedNetemConfig {
  kind: "netem";
  delayMs?: number | null;
  jitterMs?: number | null;
  delayCorrelationPct?: number | null;
  distribution?: string | null;
  lossPct?: number | null;
  lossCorrelationPct?: number | null;
}

export interface EWPSPhasePathProfile {
  requestedProfileId: EWPSLabProfile;
  appliedProfileId?: EWPSLabProfile | null;
  requestedConfiguration: EWPSNormalizedNetemConfig;
  appliedConfiguration?: EWPSNormalizedNetemConfig | null;
  verification: "PASSED" | "FAILED" | "NOT_APPLICABLE";
  verificationDetail: string;
}

export interface EWPSScenarioPhase {
  scenarioId: EWPSLabScenario;
  phaseIndex: number;
  phaseId: string;
  labInstanceId: string;
  pathProfiles: Record<string, EWPSPhasePathProfile>;
}

export interface EWPSExperimentEvent {
  eventId: string;
  eventType: "SCENARIO_PHASE_CHANGED" | "SCENARIO_PHASE_APPLY_FAILED";
  timestamp: string;
  completedAt: string;
  experimentId: string;
  scenarioId: EWPSLabScenario;
  previousPhaseIndex: number;
  previousPhaseId: string;
  newPhaseIndex: number;
  newPhaseId: string;
  applicationSucceeded: boolean;
  labInstanceId: string;
  affectedPathIds: string[];
  pathProfiles: Record<string, EWPSPhasePathProfile>;
  verification: "PASSED" | "FAILED" | "NOT_APPLICABLE";
  detail: string;
}

export interface EWPSTimeline {
  session: EWPSExperimentSession;
  decisions: EWPSDecisionPoint[];
  events: EWPSExperimentEvent[];
}

export interface EWPSDistribution {
  minimum?: number | null;
  mean?: number | null;
  median?: number | null;
  maximum?: number | null;
}

export interface EWPSSummary {
  experimentId: string;
  durationSeconds: number;
  totalSamples: number;
  decisionPoints: number;
  configuredIntervalSeconds: number;
  observedStartToStartSeconds: EWPSDistribution;
  observedCollectionDurationMs: EWPSDistribution;
  cadenceOverrunCount: number;
  measurementsPerPath: Record<string, number>;
  usablePathCountOverTime: Array<{ timestamp: string; count: number }>;
  unavailableCandidateCount: number;
  candidateUnavailableEvents: number;
  transientFailuresOnViablePaths: number;
  recoveryEvents: number;
  performanceConfidencePerPath: Record<string, { minimum: number | null; average: number | null; maximum: number | null }>;
  topologyConfidencePerPath: Record<string, { minimum: number | null; average: number | null; maximum: number | null }>;
  ewpsCostDistributionPerPath: Record<string, EWPSDistribution>;
  rawCostDistributionPerPath: Record<string, EWPSDistribution>;
  algorithmDisagreementPercentage: number;
  pairwiseDisagreementMatrix: Record<string, Record<string, number>>;
  preferenceDurationSecondsPerAlgorithmPath: Record<string, Record<string, number>>;
  recommendationSwitchesPerAlgorithm: Record<string, number>;
  hysteresisSuppressedSwitches: number;
  belowEvidenceThresholdSecondsPerPath: Record<string, number>;
  rollingLossEvents: number;
  staleEvidenceEvents: number;
  ewpsVsLowestLatencyDifferencePercentage: number;
  disagreementEvidenceComponents: Record<string, number>;
  mostCommonDisagreementComponent?: string | null;
  notableDecisionEvents: string[];
  phaseSummaries: EWPSPhaseSummary[];
}

export interface EWPSPhaseSummary {
  scenarioId: EWPSLabScenario;
  phaseIndex: number;
  phaseId: string;
  startedAt: string;
  endedAt: string;
  durationSeconds: number;
  decisionPoints: number;
  measurementsPerPath: Record<string, number>;
  performanceConfidencePerPath: Record<string, EWPSDistribution>;
  rawCostDistributionPerPath: Record<string, EWPSDistribution>;
  ewpsCostDistributionPerPath: Record<string, EWPSDistribution>;
  algorithmPreferenceCounts: Record<string, Record<string, number>>;
  algorithmDisagreementCount: number;
  hysteresisSuppressions: number;
  pathEligibilitySeconds: Record<string, number>;
  telemetryFailures: number;
  staleEvents: number;
}

export interface EWPSSimulatorScenario {
  scenarioId: string;
  name: string;
  description: string;
  expectedResearchPattern: string;
}

export interface EWPSSimulatorResult {
  scenario: EWPSSimulatorScenario;
  config: EWPSConfig;
  decisions: EWPSDecisionPoint[];
  summary: Record<string, unknown>;
  v1Comparison?: Record<string, unknown> | null;
}

export interface EWPSReplayResult {
  sourceExperimentId: string;
  modelVersion: string;
  sourceMode: EWPSSourceMode;
  candidateSnapshot: EWPSCandidateSnapshot[];
  labInstanceId?: string | null;
  labTopologyVersion?: string | null;
  controlledImpairmentScenario?: EWPSLabScenario | null;
  config: EWPSConfig;
  deterministicDigest: string;
  decisions: EWPSDecisionPoint[];
  events: EWPSExperimentEvent[];
}

export type EWPSLabProfile =
  | "fast-stable"
  | "slow-stable"
  | "fast-noisy"
  | "moderate-jitter"
  | "intermittent-loss"
  | "sustained-loss"
  | "telemetry-stale"
  | "temporary-failure"
  | "recovery"
  | "crossing-latency";

export type EWPSLabScenario =
  | "conventional-agreement"
  | "faster-epistemically-weak"
  | "raw-metric-flapping"
  | "evidence-outage"
  | "recovery";

export interface EWPSLabStatus {
  available: boolean;
  ready: boolean;
  state: "LAB_NOT_CREATED" | "LAB_UNVERIFIED" | "LAB_READY" | "LAB_LOST";
  prerequisitesPassed: boolean;
  labInstanceId?: string | null;
  topologyVersion: string;
  explicitStartRequired: boolean;
  architecture: string;
  diversityClaim: string;
  message: string;
  scenarioId?: EWPSLabScenario | null;
  scenarioPhase: number;
  scenarioPhaseId?: string | null;
  scenarioPhaseCount: number;
  paths: Array<{
    pathId: "lab-path-a" | "lab-path-b";
    displayLabel: string;
    profile: EWPSLabProfile;
    independentlyValidated: boolean;
    lastLatencyMs?: number | null;
    lastValidatedAt?: string | null;
  }>;
}

export interface EWPSLabScenarioAdvanceResponse {
  status: EWPSLabStatus;
  event?: EWPSExperimentEvent | null;
}

export interface EWPSExportResult {
  savedPath: string;
  filename: string;
  format: "jsonl" | "json" | "csv";
  folderOpenAvailable: boolean;
}
