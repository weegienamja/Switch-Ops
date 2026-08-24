export type CapabilityState = "SUPPORTED" | "UNSUPPORTED" | "UNKNOWN";
export type EvidenceConfidence = "CONFIRMED" | "HIGH" | "UNKNOWN";
export type HopState = "PROVEN" | "INFERRED" | "EXPECTED" | "AMBIGUOUS" | "UNKNOWN";
export type FindingSeverity = "CRITICAL" | "WARNING" | "NOTICE" | "UNKNOWN";
export type ProbeState = "HEALTHY" | "DEGRADED" | "UNREACHABLE" | "INSUFFICIENT_EVIDENCE";

export interface LabEvidence {
  id: string;
  deviceId: string;
  kind: string;
  command: string;
  confidence: EvidenceConfidence;
  observedAt: string;
  current: boolean;
  detail: string;
}

export interface LabCapability {
  id: string;
  deviceId: string;
  name: string;
  state: CapabilityState;
  configured: boolean | null;
  observed: boolean | null;
  detail: string;
  evidenceIds: string[];
}

export interface LabInterface {
  id: string;
  deviceId: string;
  name: string;
  adminState: "UP" | "DOWN" | "UNKNOWN";
  operState: "UP" | "DOWN" | "UNKNOWN";
  mode: "ACCESS" | "TRUNK" | "ROUTED" | "DYNAMIC" | "UNKNOWN";
  accessVlan: number | null;
  nativeVlan: number | null;
  allowedVlans: number[];
  speedMbps: number | null;
  description: string | null;
  portChannel: string | null;
  poeWatts: number | null;
  learnedMacCount: number;
  errorCount: number;
  dropCount: number;
  inputBps: number | null;
  outputBps: number | null;
  utilizationPercent: number | null;
  evidenceIds: string[];
}

export interface LabDevice {
  id: string;
  label: string;
  role: "SWITCH" | "ROUTER" | "GATEWAY" | "ACCESS_POINT" | "ENDPOINT" | "UNKNOWN";
  provider: "cisco-ios" | "local-probe";
  model: string | null;
  software: string | null;
  primary: boolean;
  observed: boolean;
  collectionState: "CURRENT" | "PARTIAL" | "FAILED" | "NOT_COLLECTED";
  detail: string;
  evidenceIds: string[];
}

export interface LabEdge {
  id: string;
  fromNodeId: string;
  toNodeId: string;
  fromInterface: string | null;
  toInterface: string | null;
  kind: "PHYSICAL" | "PORT_CHANNEL_MEMBER" | "L2_MEMBERSHIP" | "L3_GATEWAY" | "ROUTING_ADJACENCY" | "EXPECTED";
  state: HopState;
  confidence: EvidenceConfidence;
  reciprocal: boolean;
  detail: string;
  evidenceIds: string[];
}

export interface LogicalNetwork {
  id: string;
  vlanId: number | null;
  name: string;
  vrf: string | null;
  gatewayNodes: string[];
  memberInterfaces: string[];
  trunkInterfaces: string[];
  endpointNodes: string[];
  isolationState: "PROVEN" | "POLICY_UNKNOWN" | "NOT_ISOLATED" | "UNKNOWN";
  detail: string;
  evidenceIds: string[];
}

export interface LabFinding {
  id: string;
  category: "RESILIENCY" | "LAYER2" | "SEGMENTATION" | "SECURITY" | "CAPACITY" | "PERFORMANCE" | "EVIDENCE";
  severity: FindingSeverity;
  confidence: EvidenceConfidence;
  title: string;
  detail: string;
  consequence: string;
  remediation: string | null;
  affectedIds: string[];
  evidenceIds: string[];
}

export interface FailureScenario {
  id: string;
  targetId: string;
  targetKind: "INTERFACE" | "UPLINK" | "SWITCH" | "GATEWAY" | "PORT_CHANNEL_MEMBER" | "ACCESS_POINT" | "POE" | "ADJACENCY";
  title: string;
  confidence: EvidenceConfidence;
  consequences: string[];
  affectedIds: string[];
  controlImpact: string;
  evidenceIds: string[];
}

export interface LabPathHop {
  nodeId: string;
  label: string;
  viaInterface: string | null;
  state: HopState;
  evidenceIds: string[];
}

export interface LabPath {
  id: string;
  fromNodeId: string;
  toNodeId: string;
  state: HopState;
  summary: string;
  hops: LabPathHop[];
  evidenceIds: string[];
}

export interface PerformanceObservation {
  id: string;
  targetLabel: string;
  targetToken: string;
  state: ProbeState;
  observedAt: string;
  transmitted: number;
  received: number;
  lossPercent: number | null;
  latencyAvgMs: number | null;
  jitterMs: number | null;
  routeChanged: boolean | null;
  detail: string;
}

export interface LabAssuranceState {
  generatedAt: string;
  collectionState: "NOT_COLLECTED" | "CURRENT" | "PARTIAL" | "FAILED";
  summary: {
    observedDevices: number;
    physicalEdges: number;
    logicalNetworks: number;
    criticalFindings: number;
    warningFindings: number;
    unknownFindings: number;
    evidenceGaps: number;
  };
  devices: LabDevice[];
  interfaces: LabInterface[];
  edges: LabEdge[];
  logicalNetworks: LogicalNetwork[];
  capabilities: LabCapability[];
  findings: LabFinding[];
  failures: FailureScenario[];
  paths: LabPath[];
  performance: PerformanceObservation[];
  evidence: LabEvidence[];
  limitations: string[];
}

export interface ConfiguredLabDevice {
  id: string;
  label: string;
  primary: boolean;
  deviceType: string;
  storage: "keyring" | "legacy" | "none";
  configured: boolean;
}

export interface LabDeviceList {
  keyringAvailable: boolean;
  devices: ConfiguredLabDevice[];
}

export interface LabDeviceCreateRequest {
  label: string;
  host: string;
  username: string;
  password: string;
  enableSecret: string;
  deviceType: "cisco_ios" | "cisco_xe";
}
