"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, downloadEwpsExport } from "@/lib/api";
import type {
  EWPSCalculation,
  EWPSCandidatePath,
  EWPSConfig,
  EWPSDecisionPoint,
  EWPSExperimentSession,
  EWPSMeta,
  EWPSReplayResult,
  EWPSSimulatorResult,
  EWPSSimulatorScenario,
  EWPSSummary,
  EWPSTimeline,
} from "@/lib/ewpsTypes";


const WORKLOADS = [
  "YouTube 4K",
  "Netflix",
  "Amazon Prime Video",
  "Large download",
  "Normal browsing",
  "Idle baseline",
];

const COLOURS = ["#22d3ee", "#a78bfa", "#f59e0b", "#22c55e", "#fb7185", "#60a5fa"];

function number(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

function duration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
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
  value: (calculation: EWPSCalculation) => number | null | undefined;
  ceiling?: number;
}) {
  const width = 760;
  const height = 170;
  const samples = paths.flatMap((pathId) => points.flatMap((point) => {
    const found = point.calculations.find((item) => item.pathId === pathId);
    const candidate = found ? value(found) : null;
    return candidate == null || !Number.isFinite(candidate) ? [] : [candidate];
  }));
  const max = ceiling || Math.max(1, ...samples);
  const polylines = paths.map((pathId, pathIndex) => {
    const coordinates = points.flatMap((point, index) => {
      const found = point.calculations.find((item) => item.pathId === pathId);
      const candidate = found ? value(found) : null;
      if (candidate == null || !Number.isFinite(candidate)) return [];
      const x = points.length <= 1 ? 0 : index / (points.length - 1) * width;
      const y = height - Math.min(max, Math.max(0, candidate)) / max * (height - 14) - 7;
      return [`${x.toFixed(2)},${y.toFixed(2)}`];
    });
    return { pathId, colour: COLOURS[pathIndex % COLOURS.length], coordinates: coordinates.join(" ") };
  });
  return (
    <section className="ewps-chart card" aria-label={title}>
      <div className="ewps-chart__head">
        <div><span>LIVE TIMELINE</span><strong>{title}</strong></div>
        <div className="ewps-chart__legend">
          {polylines.map((line) => <span key={line.pathId}><i style={{ background: line.colour }} />{line.pathId.slice(0, 10)}</span>)}
        </div>
      </div>
      {samples.length ? (
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title} over recorded decision points`}>
          <line x1="0" y1={height - 7} x2={width} y2={height - 7} className="ewps-chart__axis" />
          <line x1="0" y1="7" x2={width} y2="7" className="ewps-chart__grid" />
          <line x1="0" y1={height / 2} x2={width} y2={height / 2} className="ewps-chart__grid" />
          {polylines.map((line) => line.coordinates ? (
            <polyline key={line.pathId} points={line.coordinates} fill="none" stroke={line.colour} strokeWidth="2.4" vectorEffect="non-scaling-stroke" />
          ) : null)}
          {points.map((point, index) => point.hysteresis.recommendationChanged ? (
            <line
              key={`${point.decisionIndex}-crossing`}
              x1={points.length <= 1 ? 0 : index / (points.length - 1) * width}
              y1="4"
              x2={points.length <= 1 ? 0 : index / (points.length - 1) * width}
              y2={height - 4}
              className="ewps-chart__crossing"
            />
          ) : null)}
        </svg>
      ) : <p className="ewps-empty">Waiting for enough observations to draw this timeline.</p>}
    </section>
  );
}

function CandidateCard({
  candidate,
  calculation,
  preferred,
}: {
  candidate: EWPSCandidatePath;
  calculation: EWPSCalculation | null;
  preferred: boolean;
}) {
  const certainty = calculation?.certainty;
  return (
    <article className={`ewps-path-card ${preferred ? "is-preferred" : ""} ${calculation && !calculation.eligible ? "is-ineligible" : ""}`}>
      <header>
        <div>
          <span className="ewps-path-card__ordinal">{candidate.displayLabel}</span>
          <strong>{candidate.adapterName}</strong>
        </div>
        <span className={`ewps-state ${calculation?.eligible ? "is-good" : "is-muted"}`}>
          {calculation ? (calculation.eligible ? "ELIGIBLE" : "INELIGIBLE") : "WAITING"}
        </span>
      </header>
      <div className="ewps-performance-strip">
        <div><span>Latency</span><strong>{number(calculation?.raw.latencyMs, 1)}<small> ms</small></strong></div>
        <div><span>Jitter</span><strong>{number(calculation?.raw.jitterMs, 1)}<small> ms</small></strong></div>
        <div><span>Loss</span><strong>{number(calculation?.raw.lossPct, 1)}<small>%</small></strong></div>
      </div>
      <div className="ewps-certainty-grid">
        <div><span>Freshness</span><b>{number(certainty?.freshness, 3)}</b></div>
        <div><span>Stability</span><b>{number(certainty?.stability, 3)}</b></div>
        <div><span>Density</span><b>{number(certainty?.density, 3)}</b></div>
        <div><span>Topology</span><b>{number(certainty?.topology, 3)}</b></div>
      </div>
      <div className="ewps-confidence">
        <div><span>Evidence-confidence index</span><strong>{number(certainty?.composite, 3)}</strong></div>
        <div className="ewps-confidence__bar"><i style={{ width: `${(certainty?.composite || 0) * 100}%` }} /></div>
      </div>
      <dl className="ewps-costs">
        <div><dt>Raw cost</dt><dd>{number(calculation?.rawCost, 2)}</dd></div>
        <div><dt>EWPS cost</dt><dd>{number(calculation?.ewpsCost, 2)}</dd></div>
      </dl>
      <p className={`ewps-topology-note evidence--${candidate.topologyEvidence}`}>
        {candidate.topologyEvidence.replaceAll("_", " ")} · {candidate.topologyDetail}
      </p>
      {preferred ? <div className="ewps-preferred-flag">CURRENT EWPS + HYSTERESIS PREFERENCE</div> : null}
    </article>
  );
}

function ExperimentForm({
  meta,
  candidates,
  busy,
  onStart,
}: {
  meta: EWPSMeta;
  candidates: EWPSCandidatePath[];
  busy: boolean;
  onStart: (request: { name: string; workloadLabel: string; candidatePathIds: string[]; config: EWPSConfig }) => Promise<void>;
}) {
  const [name, setName] = useState("First streaming evidence session");
  const [workload, setWorkload] = useState(WORKLOADS[0]);
  const [selected, setSelected] = useState<string[]>(candidates.map((item) => item.pathId));
  const [config, setConfig] = useState<EWPSConfig>(() => structuredClone(meta.defaultConfig));
  useEffect(() => setSelected((current) => current.length ? current : candidates.map((item) => item.pathId)), [candidates]);
  function parameter<K extends keyof EWPSConfig>(key: K, value: EWPSConfig[K]) {
    setConfig((current) => ({ ...current, [key]: value }));
  }
  return (
    <section className="card ewps-form">
      <div className="ewps-section-head">
        <div><span>NEW LIVE SESSION</span><h3>Define the observation boundary</h3></div>
        <span className="ewps-shadow-chip">SHADOW · READ ONLY</span>
      </div>
      <div className="ewps-form__grid">
        <label><span>Experiment name</span><input value={name} onChange={(event) => setName(event.target.value)} maxLength={100} /></label>
        <label><span>Workload label</span><select value={workload} onChange={(event) => setWorkload(event.target.value)}>{WORKLOADS.map((item) => <option key={item}>{item}</option>)}</select></label>
      </div>
      <fieldset className="ewps-candidate-picker">
        <legend>Candidate path evidence sources</legend>
        {candidates.length ? candidates.map((candidate) => (
          <label key={candidate.pathId}>
            <input
              type="checkbox"
              checked={selected.includes(candidate.pathId)}
              onChange={(event) => setSelected((current) => event.target.checked ? [...current, candidate.pathId] : current.filter((item) => item !== candidate.pathId))}
            />
            <span><strong>{candidate.displayLabel} · {candidate.adapterName}</strong><small>{candidate.topologyEvidence.replaceAll("_", " ")}</small></span>
          </label>
        )) : <p className="ewps-empty">No active non-loopback IPv4 adapter is available. The deterministic simulator remains usable below.</p>}
      </fieldset>
      {selected.length === 1 ? <p className="ewps-research-warning">One path can validate telemetry and evidence behavior, but cannot produce a comparative path-selection result.</p> : null}
      <div className="ewps-parameter-grid">
        <label><span>λ freshness decay</span><input aria-label="lambda freshness decay" type="number" step="0.005" min="0" value={config.lambda} onChange={(event) => parameter("lambda", Number(event.target.value))} /></label>
        <label><span>k density rate</span><input aria-label="density rate" type="number" step="0.05" min="0.01" value={config.k} onChange={(event) => parameter("k", Number(event.target.value))} /></label>
        <label><span>α risk aversion</span><input aria-label="risk aversion" type="number" step="0.1" min="0" value={config.alpha} onChange={(event) => parameter("alpha", Number(event.target.value))} /></label>
        <label><span>P min eligibility</span><input aria-label="minimum evidence threshold" type="number" step="0.05" min="0" max="1" value={config.pMin} onChange={(event) => parameter("pMin", Number(event.target.value))} /></label>
        <label><span>Sample interval</span><input aria-label="sample interval" type="number" step="1" min="2" value={config.sampleIntervalSeconds} onChange={(event) => parameter("sampleIntervalSeconds", Number(event.target.value))} /></label>
        <label><span>Certainty form</span><select value={config.certaintyMode} onChange={(event) => parameter("certaintyMode", event.target.value as EWPSConfig["certaintyMode"])}><option value="product">Simple product</option><option value="weighted_geometric">Weighted geometric</option></select></label>
      </div>
      <details className="ewps-advanced">
        <summary>Weights and hysteresis controls</summary>
        <div className="ewps-parameter-grid">
          {(["freshness", "stability", "density", "topology"] as const).map((key) => (
            <label key={key}><span>{key} weight</span><input aria-label={`${key} weight`} type="number" step="0.1" min="0" value={config.weights[key]} onChange={(event) => setConfig((current) => ({ ...current, weights: { ...current.weights, [key]: Number(event.target.value) } }))} /></label>
          ))}
          <label><span>Min improvement</span><input aria-label="minimum improvement" type="number" step="0.01" min="0" max="1" value={config.hysteresis.minimumImprovement} onChange={(event) => setConfig((current) => ({ ...current, hysteresis: { ...current.hysteresis, minimumImprovement: Number(event.target.value) } }))} /></label>
          <label><span>Min dwell seconds</span><input aria-label="minimum dwell seconds" type="number" step="1" min="0" value={config.hysteresis.minimumDwellSeconds} onChange={(event) => setConfig((current) => ({ ...current, hysteresis: { ...current.hysteresis, minimumDwellSeconds: Number(event.target.value) } }))} /></label>
          <label><span>Evidence duration</span><input aria-label="minimum evidence duration" type="number" step="1" min="0" value={config.hysteresis.minimumEvidenceSeconds} onChange={(event) => setConfig((current) => ({ ...current, hysteresis: { ...current.hysteresis, minimumEvidenceSeconds: Number(event.target.value) } }))} /></label>
          <label><span>Recovery hold-down</span><input aria-label="recovery hold down" type="number" step="1" min="0" value={config.hysteresis.recoveryHoldDownSeconds} onChange={(event) => setConfig((current) => ({ ...current, hysteresis: { ...current.hysteresis, recoveryHoldDownSeconds: Number(event.target.value) } }))} /></label>
        </div>
      </details>
      <div className="ewps-form__footer">
        <p>Starting records aggregate ICMP timing and interface counters only. It never changes a route.</p>
        <button className="btn btn--primary" disabled={busy || !selected.length || !name.trim()} onClick={() => void onStart({ name: name.trim(), workloadLabel: workload, candidatePathIds: selected, config })}>
          {busy ? "Preparing…" : "Start experiment"}
        </button>
      </div>
    </section>
  );
}

function SummaryPanel({ summary, candidates }: { summary: EWPSSummary; candidates: EWPSCandidatePath[] }) {
  return (
    <section className="card ewps-summary">
      <div className="ewps-section-head"><div><span>COMPLETED EXPERIMENT</span><h3>Research summary</h3></div><strong>{duration(summary.durationSeconds)}</strong></div>
      <div className="ewps-summary__metrics">
        <div><span>Total measurements</span><strong>{summary.totalSamples}</strong></div>
        <div><span>Algorithm disagreement</span><strong>{(summary.algorithmDisagreementRate * 100).toFixed(1)}%</strong></div>
        <div><span>Recommendation changes</span><strong>{summary.ewpsRecommendationChanges}</strong></div>
        <div><span>Hysteresis suppressions</span><strong>{summary.hysteresisSuppressedChanges}</strong></div>
        <div><span>Stale evidence events</span><strong>{summary.staleEvidenceEvents}</strong></div>
        <div><span>Telemetry failures</span><strong>{summary.telemetryFailures}</strong></div>
      </div>
      <div className="ewps-summary__paths">
        {Object.entries(summary.measurementsPerPath).map(([pathId, count]) => (
          <div key={pathId}><strong>{pathLabel(pathId, candidates)}</strong><span>{count} measurements · avg confidence {number(summary.averageConfidencePerPath[pathId], 3)} · preferred {number(summary.preferredPercentPerPath[pathId], 1)}% · ineligible {duration(summary.ineligibleSecondsPerPath[pathId] || 0)}</span></div>
        ))}
      </div>
    </section>
  );
}

export default function EWPSObservatory() {
  const [meta, setMeta] = useState<EWPSMeta | null>(null);
  const [candidates, setCandidates] = useState<EWPSCandidatePath[]>([]);
  const [session, setSession] = useState<EWPSExperimentSession | null>(null);
  const [timeline, setTimeline] = useState<EWPSTimeline | null>(null);
  const [summary, setSummary] = useState<EWPSSummary | null>(null);
  const [scenarios, setScenarios] = useState<EWPSSimulatorScenario[]>([]);
  const [scenarioId, setScenarioId] = useState("latency-flap-ewps-stable");
  const [simulation, setSimulation] = useState<EWPSSimulatorResult | null>(null);
  const [replay, setReplay] = useState<EWPSReplayResult | null>(null);
  const [creatingNew, setCreatingNew] = useState(false);
  const creatingRef = useRef(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (initial = false) => {
    try {
      const [nextMeta, nextCandidates, nextCurrent, nextScenarios] = await Promise.all([
        api.ewpsMeta(), api.ewpsCandidates(), api.ewpsCurrent(), api.ewpsSimulatorScenarios(),
      ]);
      setMeta(nextMeta);
      setCandidates(nextCandidates);
      setScenarios(nextScenarios);
      if (!creatingRef.current) {
        setSession(nextCurrent);
        if (nextCurrent) {
          const nextTimeline = await api.ewpsTimeline(nextCurrent.experimentId);
          setTimeline(nextTimeline);
          if (nextCurrent.status === "COMPLETED") setSummary(await api.ewpsSummary(nextCurrent.experimentId));
        } else {
          setTimeline(null);
          setSummary(null);
        }
      }
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (initial) setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(true); }, [refresh]);
  useEffect(() => {
    const handle = window.setInterval(() => void refresh(), session?.status === "RUNNING" ? 2000 : 5000);
    return () => window.clearInterval(handle);
  }, [refresh, session?.status]);

  const last = timeline?.decisions.at(-1) || null;
  const elapsed = session?.startedAt
    ? (new Date(session.endedAt || last?.timestamp || Date.now()).getTime() - new Date(session.startedAt).getTime()) / 1000
    : 0;
  const visiblePaths = useMemo(() => {
    if (!session) return candidates;
    return session.candidatePathIds.map((pathId) => candidates.find((item) => item.pathId === pathId) || {
      pathId,
      displayLabel: pathId.slice(0, 10),
      adapterName: "Recorded path",
      topologyEvidence: "unknown" as const,
      topologyDetail: "The original local adapter is not currently active.",
      eligibleForLiveMeasurement: false,
    });
  }, [candidates, session]);

  async function startExperiment(request?: { name: string; workloadLabel: string; candidatePathIds: string[]; config: EWPSConfig }) {
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

  async function pauseExperiment() {
    if (!session) return;
    setBusy("pause");
    try { setSession(await api.ewpsPause(session.experimentId)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(null); }
  }

  async function stopExperiment() {
    if (!session) return;
    setBusy("stop");
    try { const result = await api.ewpsStop(session.experimentId); setSession(result.session); setSummary(result.summary); setTimeline(await api.ewpsTimeline(session.experimentId)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(null); }
  }

  async function runReplay() {
    if (!session) return;
    setBusy("replay");
    try { setReplay(await api.ewpsReplay(session.experimentId)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(null); }
  }

  async function runSimulation() {
    if (!meta) return;
    setBusy("simulator");
    try { setSimulation(await api.ewpsRunSimulator(scenarioId, meta.defaultConfig)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(null); }
  }

  if (loading) return <section className="card ewps-loading">Loading the EWPS research boundary…</section>;
  if (!meta) return <section className="card ewps-loading">The EWPS model metadata is unavailable.</section>;
  const showForm = !session || creatingNew;
  const chartPoints = timeline?.decisions || [];

  return (
    <div className="ewps-observatory">
      <section className="ewps-mode-banner" role="status">
        <div><span className="ewps-mode-banner__pulse" /><strong>SHADOW MODE — NO ROUTING CHANGES</strong></div>
        <p>EWPS records recommendations only. It cannot modify host or network routing state.</p>
        <span>{meta.releaseId}</span>
      </section>
      <header className="ewps-hero card">
        <div>
          <span className="eyebrow">EXPERIMENTAL NETWORKING RESEARCH</span>
          <h2>EWPS Observatory</h2>
          <p>Evidence-Weighted Path Selection · uncertainty is priced into path cost and compared against conventional strategies.</p>
        </div>
        <dl>
          <div><dt>MODEL</dt><dd>{meta.modelVersion}</dd></div>
          <div><dt>MODE</dt><dd>SHADOW</dd></div>
          <div><dt>DECISIONS</dt><dd>{session?.decisionPoints || 0}</dd></div>
        </dl>
      </header>
      {error ? <div className="warning-banner warning-banner--inline ewps-error">{error}</div> : null}

      {showForm ? <ExperimentForm meta={meta} candidates={candidates} busy={busy === "start"} onStart={startExperiment} /> : null}

      {session && !creatingNew ? (
        <>
          <section className="ewps-session-bar card">
            <div><span>CURRENT EXPERIMENT</span><strong>{session.name}</strong><small>{session.workloadLabel}</small></div>
            <div><span>ELAPSED</span><strong>{duration(elapsed)}</strong><small>{session.status}</small></div>
            <div><span>MODEL / SAMPLES</span><strong>{session.ewpsModelVersion}</strong><small>{session.totalMeasurements} measurements</small></div>
            <div className="ewps-session-actions">
              {session.status === "CREATED" || session.status === "PAUSED" ? <button className="btn btn--primary" disabled={Boolean(busy)} onClick={() => void startExperiment()}>{session.status === "PAUSED" ? "Resume" : "Start experiment"}</button> : null}
              {session.status === "RUNNING" ? <button className="btn" disabled={Boolean(busy)} onClick={() => void pauseExperiment()}>Pause</button> : null}
              {session.status !== "COMPLETED" ? <button className="btn btn--ghost" disabled={Boolean(busy)} onClick={() => void stopExperiment()}>Stop</button> : null}
              {session.status === "COMPLETED" ? <button className="btn btn--primary" onClick={() => { creatingRef.current = true; setCreatingNew(true); setReplay(null); }}>New experiment</button> : null}
            </div>
          </section>

          <section className="ewps-path-grid">
            {visiblePaths.map((candidate) => <CandidateCard key={candidate.pathId} candidate={candidate} calculation={latestCalculation(timeline, candidate.pathId)} preferred={last?.hysteresis.preferredPathId === candidate.pathId} />)}
          </section>

          <section className="card ewps-topology">
            <div className="ewps-section-head"><div><span>EVIDENCE PATH VIEW</span><h3>No invented hops</h3></div><small>Relationships reflect SwitchOps evidence semantics only.</small></div>
            <div className="ewps-topology__canvas">
              <div className="ewps-topology__origin"><span>LOCAL HOST</span><strong>Observation source</strong></div>
              {visiblePaths.map((candidate) => (
                <div className={`ewps-topology__path evidence--${candidate.topologyEvidence}`} key={candidate.pathId}>
                  <i /><div><span>{candidate.displayLabel}</span><strong>{candidate.topologyEvidence.replaceAll("_", " ")}</strong></div><i />
                  <div className="ewps-topology__unknown"><span>?</span><small>UNOBSERVED HOPS</small></div>
                </div>
              ))}
              <div className="ewps-topology__target"><span>FIXED PROBE</span><strong>{meta.fixedProbeTargetToken}</strong></div>
            </div>
          </section>

          <div className="ewps-chart-grid">
            <LineChart title="Evidence-weighted cost" points={chartPoints} paths={session.candidatePathIds} value={(item) => item.ewpsCost} />
            <LineChart title="Composite evidence-confidence index" points={chartPoints} paths={session.candidatePathIds} value={(item) => item.certainty.composite} ceiling={1} />
          </div>

          <div className="ewps-analysis-grid">
            <section className="card ewps-algorithms">
              <div className="ewps-section-head"><div><span>ALGORITHM COMPARISON</span><h3>Same telemetry, different decisions</h3></div></div>
              {last ? last.algorithms.filter((item) => item.algorithm !== "lowest_loss").map((choice) => (
                <div key={choice.algorithm} className={choice.pathId && choice.pathId !== last.hysteresis.preferredPathId ? "is-disagreement" : ""} title={choice.reason}>
                  <span>{choice.algorithm.replaceAll("_", " ")}</span><strong>{pathLabel(choice.pathId, candidates)}</strong>
                </div>
              )) : <p className="ewps-empty">Recommendations appear after the first bounded observation.</p>}
            </section>
            <section className="card ewps-explanation">
              <div className="ewps-section-head"><div><span>DETERMINISTIC DECISION EXPLANATION</span><h3>Why this preference?</h3></div></div>
              <p>{last?.explanation || "Waiting for the first decision point."}</p>
              {last?.hysteresis.suppressed ? <div className="ewps-suppressed">SUPPRESSED · {last.hysteresis.switchBlockedBy.replaceAll("_", " ")}</div> : null}
            </section>
          </div>

          <section className="card ewps-events">
            <div className="ewps-section-head"><div><span>MATERIAL EVENTS</span><h3>Evidence and preference changes</h3></div><small>{chartPoints.flatMap((item) => item.events).length} markers</small></div>
            <ol>{chartPoints.slice(-20).reverse().flatMap((point) => point.events.map((event) => <li key={`${point.decisionIndex}-${event}`}><time>{new Date(point.timestamp).toLocaleTimeString()}</time><span>{event.replaceAll("_", " ").replace(":", " · ")}</span></li>))}</ol>
            {!chartPoints.some((item) => item.events.length) ? <p className="ewps-empty">No material variance, staleness, loss, eligibility, or recommendation event is recorded yet.</p> : null}
          </section>

          {summary ? <SummaryPanel summary={summary} candidates={candidates} /> : null}

          {session.status === "COMPLETED" ? (
            <section className="card ewps-research-actions">
              <div><span>REPRODUCIBILITY</span><h3>Replay and privacy-safe export</h3><p>The original experiment remains immutable. Replay feeds its recorded inputs through the same v0.1 engine.</p></div>
              <div>
                <button className="btn" disabled={busy === "replay"} onClick={() => void runReplay()}>Replay original</button>
                {(["jsonl", "json", "csv"] as const).map((format) => <button className="btn btn--ghost" key={format} onClick={() => void downloadEwpsExport(session.experimentId, format)}>Export {format.toUpperCase()}</button>)}
              </div>
              {replay ? <p className="ewps-replay-digest"><span>DETERMINISTIC DIGEST</span><code>{replay.deterministicDigest}</code> · {replay.decisions.length} reproduced decisions</p> : null}
            </section>
          ) : null}
        </>
      ) : null}

      <section className="card ewps-simulator">
        <div><span className="eyebrow">DETERMINISTIC MODEL LAB</span><h3>EWPS simulator</h3><p>Exercise uncertainty, recovery, failure, and flapping with the exact engine used by live observations.</p></div>
        <label><span>Scenario</span><select value={scenarioId} onChange={(event) => setScenarioId(event.target.value)}>{scenarios.map((item) => <option value={item.scenarioId} key={item.scenarioId}>{item.name}</option>)}</select></label>
        <button className="btn" disabled={busy === "simulator"} onClick={() => void runSimulation()}>{busy === "simulator" ? "Running…" : "Run deterministic scenario"}</button>
        {simulation ? <div className="ewps-simulator__result"><strong>{simulation.scenario.name}</strong><span>{simulation.scenario.description}</span><code>{simulation.decisions.length} decisions · {String(simulation.summary.suppressedRecommendations)} suppressions · shadow mode</code></div> : null}
      </section>
      <p className="ewps-confidence-disclaimer">P<sub>cert</sub> is a dimensionless evidence-confidence index built from heuristic functions. It is not a statistically calibrated probability of correctness.</p>
    </div>
  );
}
