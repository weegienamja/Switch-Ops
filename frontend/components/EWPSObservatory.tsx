"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";
import { isTauriDesktop, openEWPSExportFolder } from "@/lib/ewpsDesktop";
import type {
  EWPSCalculation,
  EWPSCandidatePath,
  EWPSConfig,
  EWPSDecisionPoint,
  EWPSExperimentSession,
  EWPSExportResult,
  EWPSLabScenario,
  EWPSLabStatus,
  EWPSMeta,
  EWPSReplayResult,
  EWPSSimulatorResult,
  EWPSSimulatorScenario,
  EWPSSourceMode,
  EWPSSummary,
  EWPSTimeline,
} from "@/lib/ewpsTypes";


const WORKLOADS = ["Amazon Prime Video", "YouTube 4K", "Netflix", "Large download", "Normal browsing", "Idle baseline"];
const LAB_SCENARIOS: Array<{ id: EWPSLabScenario; label: string }> = [
  { id: "conventional-agreement", label: "Scenario 1 · Conventional agreement" },
  { id: "faster-epistemically-weak", label: "Scenario 2 · Faster but epistemically weak" },
  { id: "raw-metric-flapping", label: "Scenario 3 · Raw-metric flapping" },
  { id: "evidence-outage", label: "Scenario 4 · Evidence outage" },
  { id: "recovery", label: "Scenario 5 · Recovery" },
];
const COLORS = ["#53d7bf", "#ffb454", "#8da9ff", "#eb77c6"];


function number(value: number | null | undefined, digits = 1): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}


function duration(seconds: number): string {
  const value = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}


function pathLabel(pathId: string | null | undefined, candidates: EWPSCandidatePath[]): string {
  if (!pathId) return "NO ELIGIBLE PATH";
  return candidates.find((item) => item.pathId === pathId)?.displayLabel || pathId.toUpperCase();
}


function latestCalculation(timeline: EWPSTimeline | null, pathId: string): EWPSCalculation | null {
  const last = timeline?.decisions.at(-1);
  return last?.calculations.find((item) => item.pathId === pathId) || null;
}


function LineChart({
  title,
  points,
  paths,
  value,
  ceiling,
}: {
  title: string;
  points: EWPSDecisionPoint[];
  paths: string[];
  value: (item: EWPSCalculation) => number | null | undefined;
  ceiling?: number;
}) {
  const width = 760;
  const height = 150;
  const series = paths.map((pathId) => points.flatMap((point, index) => {
    const found = point.calculations.find((item) => item.pathId === pathId);
    const candidate = found ? value(found) : null;
    return candidate == null || !Number.isFinite(candidate) ? [] : [{ index, value: candidate }];
  }));
  const maximum = ceiling || Math.max(1, ...series.flatMap((item) => item.map((point) => point.value)));
  const span = Math.max(1, points.length - 1);
  return (
    <section className="ewps-chart card" aria-label={title}>
      <div className="ewps-chart__head"><div><span>LIVE TIMELINE</span><h3>{title}</h3></div><div className="ewps-chart__legend">{paths.map((pathId, index) => <span key={pathId}><i style={{ background: COLORS[index % COLORS.length] }} />{pathId.endsWith("a") ? "PATH A" : pathId.endsWith("b") ? "PATH B" : `PATH ${index + 1}`}</span>)}</div></div>
      {series.some((item) => item.length > 1) ? (
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title} over recorded decision points`}>
          <line x1="0" y1={height - 7} x2={width} y2={height - 7} className="ewps-chart__axis" />
          <line x1="0" y1="7" x2={width} y2="7" className="ewps-chart__grid" />
          <line x1="0" y1={height / 2} x2={width} y2={height / 2} className="ewps-chart__grid" />
          {series.map((items, index) => {
            const pointsValue = items.map((item) => `${item.index / span * width},${height - Math.min(maximum, Math.max(0, item.value)) / maximum * (height - 14) - 7}`).join(" ");
            return <polyline key={paths[index]} points={pointsValue} fill="none" stroke={COLORS[index % COLORS.length]} strokeWidth="3" vectorEffect="non-scaling-stroke" />;
          })}
        </svg>
      ) : <p className="ewps-empty">Waiting for enough observations to draw this timeline.</p>}
    </section>
  );
}


function CandidateCard({ candidate, calculation, preferred }: { candidate: EWPSCandidatePath; calculation: EWPSCalculation | null; preferred: boolean }) {
  const confidence = calculation?.confidence;
  return (
    <article className={`ewps-path-card ${preferred ? "is-preferred" : ""} ${calculation && !calculation.eligible ? "is-ineligible" : ""}`}>
      <header>
        <div><span className="ewps-path-card__ordinal">{candidate.displayLabel}</span><strong>{candidate.adapterName}</strong><small>{candidate.sourceKind === "controlled_lab" ? "LOGICAL LAB PATH" : "REAL INTERFACE"}</small></div>
        <span className={`ewps-state ${calculation?.eligible ? "is-good" : "is-muted"}`}>{calculation?.eligibilityState?.replaceAll("_", " ") || candidate.lifecycle}</span>
      </header>
      <div className="ewps-performance-strip">
        <div><span>RAW LATENCY</span><strong>{number(calculation?.raw.latencyMs)}<small> ms</small></strong></div>
        <div><span>ROLLING LATENCY</span><strong>{number(calculation?.raw.rollingLatencyMs)}<small> ms</small></strong></div>
        <div><span>JITTER / ROLLING</span><strong>{number(calculation?.raw.jitterMs)} / {number(calculation?.raw.rollingJitterMs)}<small> ms</small></strong></div>
        <div><span>LOSS INSTANT / COST</span><strong>{number(calculation?.raw.lossPct)} / {number(calculation?.raw.rollingLossPct)}<small>%</small></strong></div>
      </div>
      <div className="ewps-certainty-grid">
        <div><span>FRESH</span><strong>{number(confidence?.freshness, 3)}</strong></div>
        <div><span>STABLE</span><strong>{number(confidence?.stability, 3)}</strong></div>
        <div><span>DENSITY</span><strong>{number(confidence?.density, 3)}</strong></div>
        <div><span>P PERF</span><strong>{number(confidence?.performance, 3)}</strong></div>
        <div><span>TOPOLOGY</span><strong>{number(confidence?.topology, 3)}</strong></div>
        <div><span>TOPOLOGY ×</span><strong>{number(confidence?.topologyPenalty, 3)}</strong></div>
      </div>
      <div className="ewps-confidence"><span>PERFORMANCE EVIDENCE CONFIDENCE</span><div className="ewps-confidence__bar"><i style={{ width: `${(confidence?.performance || 0) * 100}%` }} /></div></div>
      <dl className="ewps-costs"><div><dt>Raw rolling cost</dt><dd>{number(calculation?.rawCost, 2)}</dd></div><div><dt>EWPS cost</dt><dd>{number(calculation?.ewpsCost, 2)}</dd></div><div><dt>Loss evidence</dt><dd>{calculation?.raw.lossSampleCount || 0} probes</dd></div></dl>
      <p className={`ewps-topology-note evidence--${candidate.topologyEvidence}`}>{candidate.topologyEvidence.replaceAll("_", " ")} · {candidate.topologyDetail}</p>
      <small className="ewps-diversity-note">{candidate.diversityClaim}</small>
      {preferred ? <div className="ewps-preferred-flag">CURRENT EWPS + HYSTERESIS RECOMMENDATION</div> : null}
    </article>
  );
}


function LabPanel({ lab, controlledSessionActive, busy, onAction }: { lab: EWPSLabStatus | null; controlledSessionActive: boolean; busy: string | null; onAction: (action: "check" | "create" | "verify" | "prepare" | "advance" | "teardown", scenario?: EWPSLabScenario) => Promise<void> }) {
  const [scenario, setScenario] = useState<EWPSLabScenario>("faster-epistemically-weak");
  return (
    <section className="card ewps-lab" aria-labelledby="ewps-lab-title">
      <div className="ewps-section-head"><div><span>CONTROLLED DUAL-PATH TESTBED</span><h3 id="ewps-lab-title">Contained WSL2 logical paths</h3></div><span className={`ewps-state ${lab?.ready ? "is-good" : "is-muted"}`}>{lab?.state?.replaceAll("_", " ") || "CHECKING LAB"}</span></div>
      <p>{lab?.message || "Checking contained-lab status…"}</p>
      <div className="ewps-lab__architecture"><strong>{lab?.architecture || "Two separate namespace/gateway chains"}</strong><span>{lab?.diversityClaim || "No physical or ISP diversity claim."}</span></div>
      <div className="ewps-lab__paths">
        {(lab?.paths || []).map((path) => <div key={path.pathId}><span>Controlled {path.displayLabel}</span><strong>{path.profile.replaceAll("-", " ")}</strong><small>{path.independentlyValidated ? `INDEPENDENT TELEMETRY VERIFIED · ${number(path.lastLatencyMs)} ms` : lab?.state === "LAB_LOST" ? "CONTROLLED LAB LOST" : controlledSessionActive ? "RECORDED BINDING INVALID" : "NOT YET VERIFIED"}</small></div>)}
      </div>
      <div className="ewps-lab__controls">
        <button className="btn" disabled={Boolean(busy)} onClick={() => void onAction("check")}>Check prerequisites</button>
        {!lab?.ready ? <button className="btn btn--primary" disabled={Boolean(busy) || lab?.available === false} onClick={() => void onAction("create")}>{busy === "lab-create" ? "Creating…" : "Create contained lab"}</button> : null}
        {lab?.ready ? <><button className="btn" disabled={Boolean(busy)} onClick={() => void onAction("verify")}>Verify A + B</button><select aria-label="Controlled lab scenario" value={scenario} onChange={(event) => setScenario(event.target.value as EWPSLabScenario)}>{LAB_SCENARIOS.map((item) => <option value={item.id} key={item.id}>{item.label}</option>)}</select><button className="btn btn--primary" disabled={Boolean(busy)} onClick={() => void onAction("prepare", scenario)}>Prepare scenario</button><button className="btn" disabled={Boolean(busy) || !lab.scenarioId} onClick={() => void onAction("advance")}>Advance impairment phase</button><button className="btn btn--ghost" disabled={Boolean(busy)} onClick={() => void onAction("teardown")}>Teardown lab</button></> : null}
      </div>
      {lab?.scenarioId ? <p className="ewps-lab__phase">PREPARED · {lab.scenarioId.replaceAll("-", " ")} · PHASE {lab.scenarioPhase + 1} · no experiment or traffic steering was started</p> : null}
    </section>
  );
}


function ExperimentForm({ meta, candidates, lab, busy, onStart }: { meta: EWPSMeta; candidates: EWPSCandidatePath[]; lab: EWPSLabStatus | null; busy: boolean; onStart: (request: { name: string; workloadLabel: string; sourceMode: "REAL_INTERFACES" | "CONTROLLED_DUAL_PATH"; candidatePathIds: string[]; controlledScenario?: EWPSLabScenario | null; config: EWPSConfig }) => Promise<void> }) {
  const [name, setName] = useState("Experiment 002 · Controlled dual path");
  const [workload, setWorkload] = useState(WORKLOADS[0]);
  const [sourceMode, setSourceMode] = useState<Exclude<EWPSSourceMode, "SIMULATOR" | "LEGACY_UNBOUND">>("CONTROLLED_DUAL_PATH");
  const [selected, setSelected] = useState<string[]>([]);
  const [config, setConfig] = useState<EWPSConfig>(meta.defaultConfig);
  useEffect(() => {
    setSelected((current) => {
      const sourceKind = sourceMode === "CONTROLLED_DUAL_PATH" ? "controlled_lab" : "real_interface";
      const sourceCandidates = candidates.filter((item) => item.sourceKind === sourceKind);
      const available = new Set(sourceCandidates.map((item) => item.pathId));
      const retained = current.filter((item) => available.has(item));
      if (sourceMode === "CONTROLLED_DUAL_PATH") {
        const controlled = sourceCandidates.map((item) => item.pathId);
        return lab?.ready && controlled.length === 2 ? controlled : [];
      }
      return retained.length ? retained : sourceCandidates.filter((item) => item.eligibleForLiveMeasurement).map((item) => item.pathId);
    });
  }, [candidates, lab?.ready, sourceMode]);
  const displayedCandidates = candidates.filter((item) => item.sourceKind === (sourceMode === "CONTROLLED_DUAL_PATH" ? "controlled_lab" : "real_interface"));
  const controlledReady = sourceMode !== "CONTROLLED_DUAL_PATH" || Boolean(
    lab?.ready
    && lab.scenarioId
    && lab.paths.length === 2
    && lab.paths.every((path) => path.independentlyValidated)
    && selected.join(",") === "lab-path-a,lab-path-b"
  );
  function parameter<K extends keyof EWPSConfig>(key: K, value: EWPSConfig[K]) { setConfig((current) => ({ ...current, [key]: value })); }
  function weight(key: keyof EWPSConfig["weights"], value: number) {
    const bounded = Math.min(1, Math.max(0, value));
    setConfig((current) => {
      const peers = (Object.keys(current.weights) as Array<keyof EWPSConfig["weights"]>).filter((item) => item !== key);
      const peerTotal = peers.reduce((total, item) => total + current.weights[item], 0);
      const remaining = 1 - bounded;
      const next = { ...current.weights, [key]: bounded };
      peers.forEach((item) => { next[item] = peerTotal > 0 ? current.weights[item] / peerTotal * remaining : remaining / peers.length; });
      return { ...current, weights: next };
    });
  }
  return (
    <section className="card ewps-form">
      <div className="ewps-section-head"><div><span>NEW SHADOW EXPERIMENT</span><h3>Define the evidence boundary</h3></div><span className="ewps-shadow-chip">RECOMMENDATIONS ONLY</span></div>
      <div className="ewps-form__grid"><label><span>Experiment name</span><input value={name} onChange={(event) => setName(event.target.value)} maxLength={100} /></label><label><span>Workload label</span><select value={workload} onChange={(event) => setWorkload(event.target.value)}>{WORKLOADS.map((item) => <option key={item}>{item}</option>)}</select></label><label><span>Authoritative source mode</span><select aria-label="Authoritative source mode" value={sourceMode} onChange={(event) => setSourceMode(event.target.value as "REAL_INTERFACES" | "CONTROLLED_DUAL_PATH")}><option value="CONTROLLED_DUAL_PATH">Controlled dual path</option><option value="REAL_INTERFACES">Real interfaces</option></select></label></div>
      <fieldset className="ewps-candidate-picker"><legend>Candidate paths / evidence sources</legend>{displayedCandidates.length ? displayedCandidates.map((candidate) => <label key={candidate.pathId}><input type="checkbox" disabled={sourceMode === "CONTROLLED_DUAL_PATH"} checked={selected.includes(candidate.pathId)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, candidate.pathId] : current.filter((item) => item !== candidate.pathId))} /><span><strong>{candidate.displayLabel} · {candidate.adapterName}</strong><small>{candidate.sourceKind.replaceAll("_", " ")} · {candidate.lifecycle} · {candidate.topologyEvidence.replaceAll("_", " ")}</small></span></label>) : <p className="ewps-empty">{sourceMode === "CONTROLLED_DUAL_PATH" ? "Create, verify, and prepare the contained lab before configuring this experiment." : "No active real interface is available; use the controlled lab or deterministic simulator."}</p>}</fieldset>
      {sourceMode === "CONTROLLED_DUAL_PATH" ? <p className="ewps-research-warning">{controlledReady ? `Backend-verified binding · ${lab?.labInstanceId} · ${lab?.scenarioId}` : "Start is blocked until both controlled paths and a prepared scenario are backend verified."}</p> : null}
      {selected.length === 1 ? <p className="ewps-research-warning">One path can validate telemetry, but cannot produce a comparative result.</p> : null}
      <div className="ewps-parameter-grid"><label><span>λ freshness decay</span><input aria-label="lambda freshness decay" type="number" step="0.005" min="0" value={config.lambda} onChange={(event) => parameter("lambda", Number(event.target.value))} /></label><label><span>k density rate</span><input aria-label="density rate" type="number" step="0.01" min="0.01" value={config.k} onChange={(event) => parameter("k", Number(event.target.value))} /></label><label><span>α performance risk</span><input aria-label="performance risk aversion" type="number" step="0.1" min="0" value={config.alpha} onChange={(event) => parameter("alpha", Number(event.target.value))} /></label><label><span>β topology bound</span><input aria-label="topology penalty beta" type="number" step="0.05" min="0" value={config.beta} onChange={(event) => parameter("beta", Number(event.target.value))} /></label><label><span>P perf min</span><input aria-label="minimum performance evidence threshold" type="number" step="0.05" min="0" max="1" value={config.pPerfMin} onChange={(event) => parameter("pPerfMin", Number(event.target.value))} /></label><label><span>Sample interval</span><input aria-label="sample interval" type="number" step="1" min="2" value={config.sampleIntervalSeconds} onChange={(event) => parameter("sampleIntervalSeconds", Number(event.target.value))} /></label></div>
      <details className="ewps-advanced"><summary>Advanced normalized weights and hysteresis</summary><div className="ewps-parameter-grid">{(["freshness", "stability", "density"] as const).map((key) => <label key={key}><span>{key} weight</span><input aria-label={`${key} weight`} type="number" step="0.05" min="0" max="1" value={config.weights[key]} onChange={(event) => weight(key, Number(event.target.value))} /></label>)}<label><span>Min improvement</span><input aria-label="minimum improvement" type="number" step="0.01" min="0" max="1" value={config.hysteresis.minimumImprovement} onChange={(event) => setConfig((current) => ({ ...current, hysteresis: { ...current.hysteresis, minimumImprovement: Number(event.target.value) } }))} /></label><label><span>Min dwell seconds</span><input aria-label="minimum dwell seconds" type="number" step="1" min="0" value={config.hysteresis.minimumDwellSeconds} onChange={(event) => setConfig((current) => ({ ...current, hysteresis: { ...current.hysteresis, minimumDwellSeconds: Number(event.target.value) } }))} /></label><label><span>Evidence duration</span><input aria-label="minimum evidence duration" type="number" step="1" min="0" value={config.hysteresis.minimumEvidenceSeconds} onChange={(event) => setConfig((current) => ({ ...current, hysteresis: { ...current.hysteresis, minimumEvidenceSeconds: Number(event.target.value) } }))} /></label><label><span>Recovery hold-down</span><input aria-label="recovery hold down" type="number" step="1" min="0" value={config.hysteresis.recoveryHoldDownSeconds} onChange={(event) => setConfig((current) => ({ ...current, hysteresis: { ...current.hysteresis, recoveryHoldDownSeconds: Number(event.target.value) } }))} /></label></div></details>
      <div className="ewps-form__footer"><p>P<sub>perf</sub> combines fresh, stable, dense measurement evidence. Topology remains separate and bounded by β.</p><button className="btn btn--primary" disabled={busy || !selected.length || !name.trim() || !controlledReady} onClick={() => void onStart({ name: name.trim(), workloadLabel: workload, sourceMode, candidatePathIds: selected, controlledScenario: sourceMode === "CONTROLLED_DUAL_PATH" ? lab?.scenarioId : null, config })}>{busy ? "Preparing…" : "Start experiment"}</button></div>
    </section>
  );
}


function SummaryPanel({ summary, candidates }: { summary: EWPSSummary; candidates: EWPSCandidatePath[] }) {
  return (
    <section className="card ewps-summary">
      <div className="ewps-section-head"><div><span>COMPLETED EXPERIMENT</span><h3>Availability, evidence, and decisions</h3></div><strong>{duration(summary.durationSeconds)}</strong></div>
      <div className="ewps-summary__metrics"><div><span>Decision points</span><strong>{summary.decisionPoints}</strong></div><div><span>Algorithm disagreement</span><strong>{number(summary.algorithmDisagreementPercentage, 1)}%</strong></div><div><span>EWPS ≠ latency</span><strong>{number(summary.ewpsVsLowestLatencyDifferencePercentage, 1)}%</strong></div><div><span>Unavailable paths</span><strong>{summary.unavailableCandidateCount}</strong></div><div><span>Transient viable failures</span><strong>{summary.transientFailuresOnViablePaths}</strong></div><div><span>Recoveries</span><strong>{summary.recoveryEvents}</strong></div><div><span>Hysteresis suppressions</span><strong>{summary.hysteresisSuppressedSwitches}</strong></div><div><span>Primary disagreement evidence</span><strong>{summary.mostCommonDisagreementComponent || "—"}</strong></div></div>
      <div className="ewps-summary__paths">{Object.entries(summary.measurementsPerPath).map(([pathId, count]) => <div key={pathId}><strong>{pathLabel(pathId, candidates)}</strong><span>{count} records · P perf avg {number(summary.performanceConfidencePerPath[pathId]?.average, 3)} · topology avg {number(summary.topologyConfidencePerPath[pathId]?.average, 3)} · below threshold {duration(summary.belowEvidenceThresholdSecondsPerPath[pathId] || 0)}</span></div>)}</div>
      <p className="ewps-summary__qualification">A disagreement is an experimental observation, not evidence that EWPS was objectively better.</p>
    </section>
  );
}


function ExportNotice({ notice, desktop, onDismiss, onOpen }: { notice: ({ kind: "success"; result: EWPSExportResult } | { kind: "error"; message: string }) | null; desktop: boolean; onDismiss: () => void; onOpen: () => Promise<void> }) {
  useEffect(() => {
    if (!notice) return;
    const handler = (event: KeyboardEvent) => { if (event.key === "Escape") onDismiss(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [notice, onDismiss]);
  if (!notice) return null;
  const success = notice.kind === "success";
  return (
    <aside className={`ewps-export-toast ${success ? "is-success" : "is-error"}`} role={success ? "status" : "alert"} aria-live={success ? "polite" : "assertive"} aria-label={success ? "Export saved" : "Export failed"}>
      <div><span>{success ? "EXPORT SAVED" : "EXPORT FAILED"}</span><strong>{success ? notice.result.filename : notice.message}</strong></div>
      {success ? <code tabIndex={0} aria-label="Saved export path">{notice.result.savedPath}</code> : null}
      <div className="ewps-export-toast__actions">{success ? <button className="btn btn--ghost" disabled={!desktop} title={desktop ? "Open the fixed EWPS export folder" : "Available in the Windows desktop application only"} onClick={() => void onOpen()}>Open export folder</button> : null}<button className="btn" autoFocus onClick={onDismiss}>Dismiss</button></div>
      {success && !desktop ? <small>Folder opening is unavailable in browser/source mode; the selectable path above is still authoritative.</small> : null}
    </aside>
  );
}


export function EWPSObservatory() {
  const [meta, setMeta] = useState<EWPSMeta | null>(null);
  const [candidates, setCandidates] = useState<EWPSCandidatePath[]>([]);
  const [session, setSession] = useState<EWPSExperimentSession | null>(null);
  const [timeline, setTimeline] = useState<EWPSTimeline | null>(null);
  const [summary, setSummary] = useState<EWPSSummary | null>(null);
  const [replay, setReplay] = useState<EWPSReplayResult | null>(null);
  const [scenarios, setScenarios] = useState<EWPSSimulatorScenario[]>([]);
  const [scenarioId, setScenarioId] = useState("faster-epistemically-weak");
  const [simulation, setSimulation] = useState<EWPSSimulatorResult | null>(null);
  const [lab, setLab] = useState<EWPSLabStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creatingNew, setCreatingNew] = useState(false);
  const [notice, setNotice] = useState<({ kind: "success"; result: EWPSExportResult } | { kind: "error"; message: string }) | null>(null);
  const creatingRef = useRef(false);
  const desktop = useMemo(() => isTauriDesktop(), []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.ewpsMeta(), api.ewpsCandidates(), api.ewpsCurrent(), api.ewpsSimulatorScenarios(), api.ewpsLabStatus()])
      .then(([nextMeta, nextCandidates, nextCurrent, nextScenarios, nextLab]) => {
        if (cancelled) return;
        setMeta(nextMeta); setCandidates(nextCandidates); setSession(nextCurrent); setScenarios(nextScenarios); setLab(nextLab);
        if (nextScenarios.length) setScenarioId(nextScenarios[0].scenarioId);
      })
      .catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function refreshLabBinding() {
      try {
        const [nextLab, nextCandidates] = await Promise.all([api.ewpsLabStatus(), api.ewpsCandidates()]);
        if (!cancelled) { setLab(nextLab); setCandidates(nextCandidates); }
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      }
    }
    const timer = window.setInterval(() => void refreshLabBinding(), 2500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  const activeExperimentId = session?.experimentId;

  useEffect(() => {
    if (!activeExperimentId || creatingNew) return;
    let cancelled = false;
    async function poll() {
      try {
        const [nextCurrent, nextCandidates] = await Promise.all([api.ewpsCurrent(), api.ewpsCandidates()]);
        if (cancelled) return;
        setCandidates(nextCandidates);
        if (nextCurrent && nextCurrent.experimentId === activeExperimentId) {
          setSession(nextCurrent);
          const nextTimeline = await api.ewpsTimeline(nextCurrent.experimentId);
          if (!cancelled) setTimeline(nextTimeline);
          if (nextCurrent.status === "COMPLETED") setSummary(await api.ewpsSummary(nextCurrent.experimentId));
        }
      } catch (cause) { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 2500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [activeExperimentId, creatingNew]);

  const last = timeline?.decisions.at(-1) || null;
  const elapsed = session?.startedAt ? ((session.endedAt ? new Date(session.endedAt) : new Date()).getTime() - new Date(session.startedAt).getTime()) / 1000 : 0;
  const visiblePaths = useMemo(() => {
    if (!session) return candidates;
    if (session.candidateSnapshot?.length) {
      return session.candidateSnapshot.map((item) => ({ ...item, lifecycle: "PROBING" as const, eligibleForLiveMeasurement: true }));
    }
    return session.candidatePathIds.map((pathId) => candidates.find((item) => item.pathId === pathId)).filter((item): item is EWPSCandidatePath => Boolean(item));
  }, [candidates, session]);

  async function startExperiment(request?: { name: string; workloadLabel: string; sourceMode: "REAL_INTERFACES" | "CONTROLLED_DUAL_PATH"; candidatePathIds: string[]; controlledScenario?: EWPSLabScenario | null; config: EWPSConfig }) {
    setBusy("start"); setError(null);
    try {
      let target = session;
      if (request) target = await api.ewpsCreate(request);
      if (!target) throw new Error("Create an experiment before starting it.");
      const running = await api.ewpsStart(target.experimentId);
      creatingRef.current = false; setCreatingNew(false); setSession(running); setSummary(null); setTimeline(await api.ewpsTimeline(running.experimentId));
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(null); }
  }

  async function pauseExperiment() { if (!session) return; setBusy("pause"); try { setSession(await api.ewpsPause(session.experimentId)); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(null); } }
  async function stopExperiment() { if (!session) return; setBusy("stop"); try { const result = await api.ewpsStop(session.experimentId); setSession(result.session); setSummary(result.summary); setTimeline(await api.ewpsTimeline(session.experimentId)); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(null); } }
  async function runReplay() { if (!session) return; setBusy("replay"); try { setReplay(await api.ewpsReplay(session.experimentId)); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(null); } }
  async function runSimulation() { if (!meta) return; setBusy("simulator"); try { setSimulation(await api.ewpsRunSimulator(scenarioId, meta.defaultConfig)); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(null); } }
  async function exportExperiment(format: "jsonl" | "json" | "csv") { if (!session) return; setBusy(`export-${format}`); setNotice(null); try { setNotice({ kind: "success", result: await api.ewpsSaveExport(session.experimentId, format) }); } catch (cause) { setNotice({ kind: "error", message: cause instanceof Error ? cause.message : String(cause) }); } finally { setBusy(null); } }
  async function openExportFolder() { try { await openEWPSExportFolder(); } catch (cause) { setNotice({ kind: "error", message: cause instanceof Error ? cause.message : String(cause) }); } }
  async function labAction(action: "check" | "create" | "verify" | "prepare" | "advance" | "teardown", selectedScenario?: EWPSLabScenario) { setBusy(`lab-${action}`); setError(null); try { const next = action === "check" ? await api.ewpsLabPrerequisites() : action === "create" ? await api.ewpsLabCreate() : action === "verify" ? await api.ewpsLabVerify() : action === "prepare" ? await api.ewpsLabPrepareScenario(selectedScenario || "faster-epistemically-weak") : action === "advance" ? await api.ewpsLabAdvanceScenario() : await api.ewpsLabTeardown(); setLab(next); setCandidates(await api.ewpsCandidates()); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(null); } }

  if (loading) return <section className="card ewps-loading">Loading the EWPS v0.2 research boundary…</section>;
  if (!meta) return <section className="card ewps-loading">The EWPS v0.2 model metadata is unavailable.</section>;
  const showForm = !session || creatingNew;
  const chartPoints = timeline?.decisions || [];

  return (
    <div className="ewps-observatory">
      <section className="ewps-mode-banner" role="status"><div><span className="ewps-mode-banner__pulse" /><strong>SHADOW MODE — RECOMMENDATIONS ONLY</strong></div><p>EWPS records comparative decisions. It cannot steer Windows or application traffic.</p><span>{meta.releaseId}</span></section>
      <header className="ewps-hero card"><div><span className="eyebrow">CONTROLLED DUAL-PATH NETWORKING RESEARCH</span><h2>EWPS Observatory</h2><p>Measurement confidence and structural confidence now have separate, explicit authority.</p></div><dl><div><dt>MODEL</dt><dd>{meta.modelVersion}</dd></div><div><dt>MODE</dt><dd>SHADOW</dd></div><div><dt>DECISIONS</dt><dd>{session?.decisionPoints || 0}</dd></div></dl></header>
      {error ? <div className="warning-banner warning-banner--inline ewps-error" role="alert">{error}</div> : null}
      <LabPanel lab={lab} controlledSessionActive={Boolean(session && session.status !== "COMPLETED" && session.sourceMode === "CONTROLLED_DUAL_PATH")} busy={busy} onAction={labAction} />
      {showForm ? <ExperimentForm meta={meta} candidates={candidates} lab={lab} busy={busy === "start"} onStart={startExperiment} /> : null}
      {session && !creatingNew ? <>
        <section className="ewps-session-bar card"><div><span>CURRENT EXPERIMENT</span><strong>{session.name}</strong><small>{session.workloadLabel}</small></div><div><span>ELAPSED</span><strong>{duration(elapsed)}</strong><small>{session.status}</small></div><div><span>MODEL / MEASUREMENTS</span><strong>{session.ewpsModelVersion}</strong><small>{session.totalMeasurements} path records</small></div><div className="ewps-session-actions">{session.status === "CREATED" || session.status === "PAUSED" ? <button className="btn btn--primary" disabled={Boolean(busy)} onClick={() => void startExperiment()}>{session.status === "PAUSED" ? "Resume" : "Start experiment"}</button> : null}{session.status === "RUNNING" ? <button className="btn" disabled={Boolean(busy)} onClick={() => void pauseExperiment()}>Pause</button> : null}{session.status !== "COMPLETED" ? <button className="btn btn--ghost" disabled={Boolean(busy)} onClick={() => void stopExperiment()}>Stop</button> : null}{session.status === "COMPLETED" ? <button className="btn btn--primary" onClick={() => { creatingRef.current = true; setCreatingNew(true); setReplay(null); }}>New experiment</button> : null}</div></section>
        <section className="ewps-path-grid">{visiblePaths.map((candidate) => <CandidateCard key={candidate.pathId} candidate={candidate} calculation={latestCalculation(timeline, candidate.pathId)} preferred={last?.hysteresis.preferredPathId === candidate.pathId} />)}</section>
        <section className="card ewps-topology"><div className="ewps-section-head"><div><span>CONTROLLED / OBSERVED PATH VIEW</span><h3>No invented diversity</h3></div><small>Logical separation is not physical or ISP independence.</small></div><div className="ewps-topology__canvas"><div className="ewps-topology__origin"><span>PROBE SOURCE</span><strong>Bounded observations</strong></div>{visiblePaths.map((candidate) => <div className={`ewps-topology__path evidence--${candidate.topologyEvidence}`} key={candidate.pathId}><i /><div><span>{candidate.displayLabel}</span><strong>{candidate.sourceKind.replaceAll("_", " ")}</strong></div><i /><div className="ewps-topology__unknown"><span>{candidate.sourceKind === "controlled_lab" ? "LAB" : "?"}</span><small>{candidate.sourceKind === "controlled_lab" ? "CONTAINED GATEWAY" : "UNOBSERVED HOPS"}</small></div></div>)}<div className="ewps-topology__target"><span>FIXED PROBE</span><strong>{meta.fixedProbeTargetToken}</strong></div></div></section>
        <div className="ewps-chart-grid"><LineChart title="Evidence-weighted cost" points={chartPoints} paths={session.candidatePathIds} value={(item) => item.ewpsCost} /><LineChart title="Performance evidence confidence" points={chartPoints} paths={session.candidatePathIds} value={(item) => item.confidence.performance} ceiling={1} /></div>
        <div className="ewps-analysis-grid"><section className="card ewps-algorithms"><div className="ewps-section-head"><div><span>ALGORITHM COMPARISON</span><h3>Same telemetry, different decisions</h3></div></div>{last ? last.algorithms.map((choice) => <div key={choice.algorithm} className={choice.pathId && choice.pathId !== last.hysteresis.preferredPathId ? "is-disagreement" : ""} title={choice.reason}><span>{choice.algorithm.replaceAll("_", " ")}</span><strong>{pathLabel(choice.pathId, candidates)}</strong></div>) : <p className="ewps-empty">Recommendations appear after the first bounded observation.</p>}</section><section className="card ewps-explanation"><div className="ewps-section-head"><div><span>DETERMINISTIC DECISION EXPLANATION</span><h3>Why this recommendation?</h3></div></div><p>{last?.explanation || "Waiting for the first decision point."}</p>{last?.hysteresis.suppressed ? <div className="ewps-suppressed">SUPPRESSED · {last.hysteresis.switchBlockedBy.replaceAll("_", " ")}</div> : null}</section></div>
        <section className="card ewps-events"><div className="ewps-section-head"><div><span>MATERIAL EVENTS</span><h3>Impairment, evidence, availability, and decisions</h3></div><small>{chartPoints.flatMap((item) => item.events).length} markers</small></div><ol>{chartPoints.slice(-20).reverse().flatMap((point) => point.events.map((event) => <li key={`${point.decisionIndex}-${event}`}><time>{new Date(point.timestamp).toLocaleTimeString()}</time><span>{event.replaceAll("_", " ").replaceAll(":", " · ")}</span></li>))}</ol>{!chartPoints.some((item) => item.events.length) ? <p className="ewps-empty">No impairment, staleness, failure, recovery, crossing, or suppression event is recorded yet.</p> : null}</section>
        {summary ? <SummaryPanel summary={summary} candidates={candidates} /> : null}
        {session.status === "COMPLETED" ? <section className="card ewps-research-actions"><div><span>REPRODUCIBILITY</span><h3>Replay and privacy-safe export</h3><p>The original session is immutable; stored model version selects v0.1 or v0.2 replay semantics.</p></div><div><button className="btn" disabled={busy === "replay"} onClick={() => void runReplay()}>Replay original</button>{(["jsonl", "json", "csv"] as const).map((format) => <button className="btn btn--ghost" disabled={busy === `export-${format}`} key={format} onClick={() => void exportExperiment(format)}>{busy === `export-${format}` ? "Saving…" : `Export ${format.toUpperCase()}`}</button>)}</div>{replay ? <p className="ewps-replay-digest"><span>DETERMINISTIC DIGEST</span><code>{replay.deterministicDigest}</code> · {replay.decisions.length} reproduced decisions</p> : null}</section> : null}
      </> : null}
      <section className="card ewps-simulator"><div><span className="eyebrow">VERSIONED CALIBRATION LAB</span><h3>EWPS v0.2 simulator</h3><p>Exercises agreement, disagreement, outage, recovery, Experiment 001 patterns, and adversarial settings through the production engine.</p></div><label><span>Scenario</span><select value={scenarioId} onChange={(event) => setScenarioId(event.target.value)}>{scenarios.map((item) => <option value={item.scenarioId} key={item.scenarioId}>{item.name}</option>)}</select></label><button className="btn btn--primary" disabled={busy === "simulator"} onClick={() => void runSimulation()}>{busy === "simulator" ? "Running…" : "Run deterministic scenario"}</button>{simulation ? <div className="ewps-simulator__result"><strong>{simulation.scenario.name}</strong><span>{simulation.scenario.description}</span><code>{simulation.decisions.length} decisions · {String(simulation.summary.disagreementPoints)} disagreement points · shadow mode</code>{simulation.v1Comparison ? <small>Includes explicit v0.1 vs v0.2 calibration output.</small> : null}</div> : null}</section>
      <p className="ewps-confidence-disclaimer">P<sub>perf</sub> and topology confidence are dimensionless heuristic evidence indices, not calibrated probabilities. Controlled logical paths do not establish physical diversity.</p>
      <ExportNotice notice={notice} desktop={desktop} onDismiss={() => setNotice(null)} onOpen={openExportFolder} />
    </div>
  );
}

export default EWPSObservatory;
