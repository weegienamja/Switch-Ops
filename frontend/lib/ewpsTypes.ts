export type TopologyEvidenceKey =
  | "reciprocal_independent_direct"
  | "one_sided_direct"
  | "strong_inference"
  | "weak_inference"
  | "contradictory"
  | "unknown";

export interface EWPSWeights {
  freshness: number;
  stability: number;
  density: number;
  topology: number;
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
  pMin: number;
  certaintyMode: "product" | "weighted_geometric";
  weights: EWPSWeights;
  hysteresis: EWPSHysteresisConfig;
  latencyWeight: number;
  jitterWeight: number;
  lossWeight: number;
  sampleIntervalSeconds: number;
  probeCount: number;
  rollingWindow: number;
}

export interface EWPSMeta {
  modelVersion: string;
  releaseId: string;
  mode: "SHADOW";
  changesNetworkState: false;
  confidenceSemantics: string;
  topologyMappingVersion: string;
  topologyMapping: Record<TopologyEvidenceKey, { score: number; description: string }>;
  defaultConfig: EWPSConfig;
  fixedProbeTargetToken: string;
  privacyBoundary: string;
}

export interface EWPSCandidatePath {
  pathId: string;
  displayLabel: string;
  adapterName: string;
  topologyEvidence: TopologyEvidenceKey;
  topologyDetail: string;
  reachable?: boolean | null;
  eligibleForLiveMeasurement: boolean;
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
  candidatePathIds: string[];
  createdAt: string;
  startedAt?: string | null;
  endedAt?: string | null;
  pausedAt?: string | null;
  totalMeasurements: number;
  decisionPoints: number;
}

export interface EWPSRawMetrics {
  latencyMs?: number | null;
  jitterMs?: number | null;
  lossPct?: number | null;
  sampleCount: number;
  reachable: boolean;
  interfacePacketsSent?: number | null;
  interfacePacketsReceived?: number | null;
  interfaceErrors?: number | null;
  interfaceDrops?: number | null;
}

export interface EWPSEvidenceInput {
  ageSeconds?: number | null;
  meanMs?: number | null;
  stddevMs?: number | null;
  effectiveSamples: number;
  topologyEvidence: TopologyEvidenceKey;
}

export interface EWPSCalculation {
  modelVersion: string;
  pathId: string;
  raw: EWPSRawMetrics;
  evidence: EWPSEvidenceInput;
  certainty: {
    freshness: number;
    stability: number;
    density: number;
    topology: number;
    composite: number;
    indexDescription: string;
  };
  rawCost?: number | null;
  ewpsCost?: number | null;
  eligible: boolean;
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
}

export interface EWPSTimeline {
  session: EWPSExperimentSession;
  decisions: EWPSDecisionPoint[];
}

export interface EWPSSummary {
  experimentId: string;
  durationSeconds: number;
  totalSamples: number;
  decisionPoints: number;
  measurementsPerPath: Record<string, number>;
  averageConfidencePerPath: Record<string, number | null>;
  minimumConfidencePerPath: Record<string, number | null>;
  preferredPercentPerPath: Record<string, number>;
  algorithmDisagreementRate: number;
  ewpsRecommendationChanges: number;
  hysteresisSuppressedChanges: number;
  ineligibleSamplesPerPath: Record<string, number>;
  ineligibleSecondsPerPath: Record<string, number>;
  staleEvidenceEvents: number;
  instabilityEvents: number;
  telemetryFailures: number;
  notableDecisionEvents: string[];
}

export interface EWPSSimulatorScenario {
  scenarioId: string;
  name: string;
  description: string;
}

export interface EWPSSimulatorResult {
  scenario: EWPSSimulatorScenario;
  config: EWPSConfig;
  decisions: EWPSDecisionPoint[];
  summary: Record<string, number | boolean>;
}

export interface EWPSReplayResult {
  sourceExperimentId: string;
  modelVersion: string;
  config: EWPSConfig;
  deterministicDigest: string;
  decisions: EWPSDecisionPoint[];
}
