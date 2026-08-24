"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type {
  CapabilityState,
  ConfiguredLabDevice,
  EvidenceConfidence,
  FindingSeverity,
  LabAssuranceState,
  LabDeviceCreateRequest,
  LabDeviceList,
  ProbeState,
} from "@/lib/labTypes";

type Tab = "overview" | "topology" | "paths" | "failures" | "performance" | "segmentation" | "findings" | "capabilities";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "topology", label: "Topology" },
  { id: "paths", label: "Paths" },
  { id: "failures", label: "Failure domains" },
  { id: "performance", label: "Performance" },
  { id: "segmentation", label: "Segmentation" },
  { id: "findings", label: "Design findings" },
  { id: "capabilities", label: "Capabilities" },
];

const EMPTY_DEVICE: LabDeviceCreateRequest = {
  label: "",
  host: "",
  username: "",
  password: "",
  enableSecret: "",
  deviceType: "cisco_ios",
};

function tone(value: CapabilityState | EvidenceConfidence | FindingSeverity | ProbeState | string): string {
  if (["SUPPORTED", "CONFIRMED", "HEALTHY", "CURRENT", "PROVEN"].includes(value)) return "good";
  if (["CRITICAL", "UNREACHABLE", "FAILED"].includes(value)) return "bad";
  if (["WARNING", "DEGRADED", "HIGH", "PARTIAL", "INFERRED"].includes(value)) return "warn";
  return "neutral";
}

function StateChip({ value }: { value: string }) {
  return <span className={`assurance-chip assurance-chip--${tone(value)}`}>{value.replaceAll("_", " ")}</span>;
}

export default function LabAssurancePanel() {
  const [state, setState] = useState<LabAssuranceState | null>(null);
  const [devices, setDevices] = useState<LabDeviceList | null>(null);
  const [active, setActive] = useState<Tab>("overview");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deviceForm, setDeviceForm] = useState<LabDeviceCreateRequest>(EMPTY_DEVICE);
  const [probe, setProbe] = useState({ label: "Default gateway", target: "" });
  const [pathTarget, setPathTarget] = useState("");

  async function load() {
    setError(null);
    try {
      const [nextState, nextDevices] = await Promise.all([
        api.labAssuranceState(),
        api.labAssuranceDevices(),
      ]);
      setState(nextState);
      setDevices(nextDevices);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function refresh() {
    setBusy("refresh");
    setError(null);
    try {
      const result = await api.refreshLabAssurance();
      setState(result.state);
      setDevices(await api.labAssuranceDevices());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function addDevice(event: React.FormEvent) {
    event.preventDefault();
    setBusy("device");
    setError(null);
    try {
      await api.addLabAssuranceDevice(deviceForm);
      setDeviceForm(EMPTY_DEVICE);
      setDevices(await api.labAssuranceDevices());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function removeDevice(device: ConfiguredLabDevice) {
    if (device.primary || !window.confirm(`Remove ${device.label} from Lab Assurance? Its keyring entry will be deleted.`)) return;
    setBusy(device.id);
    setError(null);
    try {
      await api.removeLabAssuranceDevice(device.id);
      setDevices(await api.labAssuranceDevices());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function runProbe(event: React.FormEvent) {
    event.preventDefault();
    setBusy("probe");
    setError(null);
    try {
      await api.runLabProbe(probe.target, probe.label);
      setState(await api.labAssuranceState());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  const selectedPath = useMemo(
    () => state?.paths.find((item) => item.toNodeId === pathTarget) || state?.paths[0],
    [pathTarget, state],
  );
  const deviceLabels = useMemo(
    () => new Map(state?.devices.map((item) => [item.id, item.label]) || []),
    [state?.devices],
  );

  if (!state || !devices) {
    return <section className="card assurance-loading">{error || "Loading Lab Assurance…"}</section>;
  }

  return (
    <section className="lab-assurance">
      <div className="card assurance-hero">
        <div>
          <div className="eyebrow">LAB ASSURANCE · READ ONLY</div>
          <h2>Know what survives before something fails</h2>
          <p>
            Current IOS/IOS-XE evidence becomes a lab graph, explicit design findings,
            path explanations and bounded failure scenarios. Unknown evidence stays unknown.
          </p>
        </div>
        <div className="assurance-hero__actions">
          <StateChip value={state.collectionState} />
          <button className="btn" type="button" onClick={() => void refresh()} disabled={busy !== null}>
            {busy === "refresh" ? "Observing lab…" : "Refresh lab evidence"}
          </button>
        </div>
      </div>

      {error ? <div className="warning-banner warning-banner--inline">{error}</div> : null}

      <nav className="assurance-tabs" aria-label="Lab Assurance sections">
        {TABS.map((tab) => (
          <button key={tab.id} type="button" className={active === tab.id ? "is-active" : ""} onClick={() => setActive(tab.id)}>
            {tab.label}
          </button>
        ))}
      </nav>

      {active === "overview" ? (
        <div className="assurance-section">
          <div className="assurance-metrics">
            <Metric label="Collected devices" value={state.summary.observedDevices} />
            <Metric label="Proven physical edges" value={state.summary.physicalEdges} />
            <Metric label="Logical networks" value={state.summary.logicalNetworks} />
            <Metric label="Critical findings" value={state.summary.criticalFindings} toneName="bad" />
            <Metric label="Warnings" value={state.summary.warningFindings} toneName="warn" />
            <Metric label="Evidence gaps" value={state.summary.evidenceGaps} toneName="neutral" />
          </div>
          <div className="grid grid--12">
            <div className="card col-7 assurance-card">
              <div className="card__head"><div><div className="eyebrow">Highest-priority evidence</div><h3 className="card__title">What deserves attention</h3></div></div>
              <FindingList findings={state.findings.slice().sort((a, b) => severityRank(a.severity) - severityRank(b.severity)).slice(0, 6)} />
            </div>
            <div className="card col-5 assurance-card">
              <div className="card__head"><div><div className="eyebrow">Interpretation boundary</div><h3 className="card__title">Known limitations</h3></div></div>
              <ul className="assurance-notes">{state.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
            </div>
          </div>
        </div>
      ) : null}

      {active === "topology" ? (
        <div className="assurance-section grid grid--12">
          <div className="card col-5 assurance-card">
            <div className="card__head"><div><div className="eyebrow">Observed and discovered</div><h3 className="card__title">Devices</h3></div></div>
            <div className="assurance-list">
              {state.devices.map((device) => (
                <article key={device.id} className="assurance-row">
                  <div><strong>{device.label}</strong><span>{device.role} · {device.model || "model unknown"}</span></div>
                  <StateChip value={device.collectionState} />
                  <p>{device.detail}</p>
                </article>
              ))}
            </div>
          </div>
          <div className="card col-7 assurance-card">
            <div className="card__head"><div><div className="eyebrow">Evidence on every edge</div><h3 className="card__title">Relationships</h3></div></div>
            <div className="assurance-list">
              {state.edges.filter((edge) => edge.kind !== "PORT_CHANNEL_MEMBER").map((edge) => {
                const from = deviceLabels.get(edge.fromNodeId) || edge.fromNodeId;
                const to = deviceLabels.get(edge.toNodeId) || edge.toNodeId;
                return (
                  <article key={edge.id} className="assurance-row">
                    <div><strong>{from} → {to}</strong><span>{edge.fromInterface || "logical"} {edge.toInterface ? `↔ ${edge.toInterface}` : ""} · {edge.kind}</span></div>
                    <StateChip value={edge.state} />
                    <p>{edge.detail}</p>
                  </article>
                );
              })}
              {!state.edges.length ? <p className="empty-note">No evidence-backed relationships were collected.</p> : null}
            </div>
          </div>
        </div>
      ) : null}

      {active === "paths" ? (
        <div className="assurance-section card assurance-card">
          <div className="card__head assurance-card__split">
            <div><div className="eyebrow">PATH EXPLORER</div><h3 className="card__title">Why SwitchOps believes this path exists</h3></div>
            <label className="assurance-select">Destination
              <select value={selectedPath?.toNodeId || ""} onChange={(event) => setPathTarget(event.target.value)}>
                {state.paths.map((path) => {
                  const label = deviceLabels.get(path.toNodeId) || path.toNodeId;
                  return <option key={path.id} value={path.toNodeId}>{label}</option>;
                })}
              </select>
            </label>
          </div>
          {selectedPath ? (
            <>
              <div className="assurance-path">
                {selectedPath.hops.map((hop, index) => (
                  <div key={`${hop.nodeId}-${index}`} className="assurance-hop">
                    <StateChip value={hop.state} />
                    <strong>{hop.label}</strong>
                    <span>{hop.viaInterface || (index === 0 ? "source" : "interface unknown")}</span>
                  </div>
                ))}
              </div>
              <p className="assurance-detail">{selectedPath.summary}</p>
            </>
          ) : <p className="empty-note">No destination paths are available yet.</p>}
        </div>
      ) : null}

      {active === "failures" ? (
        <div className="assurance-section card assurance-card">
          <div className="card__head"><div><div className="eyebrow">WHAT-IF · NO CONFIG CHANGES</div><h3 className="card__title">Failure domains</h3></div></div>
          <div className="assurance-list">
            {state.failures.map((failure) => (
              <article key={failure.id} className="assurance-row assurance-row--wide">
                <div><strong>{failure.title}</strong><span>{failure.targetKind}</span></div>
                <StateChip value={failure.confidence} />
                <ul>{failure.consequences.map((item) => <li key={item}>{item}</li>)}</ul>
                <p><b>Control impact:</b> {failure.controlImpact}</p>
              </article>
            ))}
            {!state.failures.length ? <p className="empty-note">The observed graph is not sufficient for failure simulation.</p> : null}
          </div>
        </div>
      ) : null}

      {active === "performance" ? (
        <div className="assurance-section grid grid--12">
          <form className="card col-4 assurance-card assurance-form" onSubmit={(event) => void runProbe(event)}>
            <div className="eyebrow">BOUNDED ACTIVE PROBE</div><h3 className="card__title">Test service reachability</h3>
            <p>A fixed local ping and bounded route trace. It cannot run arbitrary shell or switch commands.</p>
            <label>Label<input required value={probe.label} onChange={(event) => setProbe({ ...probe, label: event.target.value })} /></label>
            <label>IP or hostname<input required value={probe.target} onChange={(event) => setProbe({ ...probe, target: event.target.value })} placeholder="gateway or local server" /></label>
            <button className="btn" disabled={busy !== null}>{busy === "probe" ? "Probing…" : "Run bounded probe"}</button>
          </form>
          <div className="card col-8 assurance-card">
            <div className="card__head"><div><div className="eyebrow">LINK UP ≠ SERVICE HEALTHY</div><h3 className="card__title">Recent observations</h3></div></div>
            <div className="assurance-list">
              {state.performance.slice().reverse().map((item) => (
                <article key={item.id} className="assurance-row">
                  <div><strong>{item.targetLabel}</strong><span>{item.received}/{item.transmitted} replies · {item.latencyAvgMs ?? "—"} ms avg · {item.jitterMs ?? "—"} ms jitter</span></div>
                  <StateChip value={item.state} />
                  <p>{item.detail}</p>
                </article>
              ))}
              {!state.performance.length ? <p className="empty-note">No active probes have run. Switch link state is not being presented as service health.</p> : null}
            </div>
          </div>
        </div>
      ) : null}

      {active === "segmentation" ? (
        <div className="assurance-section card assurance-card">
          <div className="card__head"><div><div className="eyebrow">LOGICAL NETWORK</div><h3 className="card__title">VLANs, gateways and policy boundaries</h3></div></div>
          <div className="assurance-table-wrap"><table className="assurance-table"><thead><tr><th>Network</th><th>Gateways</th><th>Access members</th><th>Trunks</th><th>Isolation</th></tr></thead><tbody>
            {state.logicalNetworks.map((network) => <tr key={network.id}><td><strong>{network.name}</strong><span>{network.vlanId === null ? "Routed domain" : `VLAN ${network.vlanId}`}{network.vrf ? ` · VRF ${network.vrf}` : ""}</span></td><td>{network.gatewayNodes.length}</td><td>{network.memberInterfaces.length} ports · {network.endpointNodes.length} endpoints</td><td>{network.trunkInterfaces.length}</td><td><StateChip value={network.isolationState} /></td></tr>)}
          </tbody></table></div>
          <p className="assurance-detail">Separate VLANs are separate broadcast domains. Inter-VLAN isolation remains POLICY UNKNOWN until ACL, firewall or equivalent enforcement evidence proves it.</p>
        </div>
      ) : null}

      {active === "findings" ? (
        <div className="assurance-section card assurance-card">
          <div className="card__head"><div><div className="eyebrow">NO NUMERIC HEALTH SCORE</div><h3 className="card__title">Evidence-backed design findings</h3></div></div>
          <FindingList findings={state.findings.slice().sort((a, b) => severityRank(a.severity) - severityRank(b.severity))} />
        </div>
      ) : null}

      {active === "capabilities" ? (
        <div className="assurance-section grid grid--12">
          <div className="card col-7 assurance-card">
            <div className="card__head"><div><div className="eyebrow">SUPPORTED · UNSUPPORTED · UNKNOWN</div><h3 className="card__title">Observed capability model</h3></div></div>
            <div className="assurance-table-wrap"><table className="assurance-table"><thead><tr><th>Device</th><th>Capability</th><th>Support</th><th>Configured</th></tr></thead><tbody>
              {state.capabilities.map((capability) => <tr key={capability.id}><td>{deviceLabels.get(capability.deviceId) || "Device"}</td><td><strong>{capability.name}</strong><span>{capability.detail}</span></td><td><StateChip value={capability.state} /></td><td>{capability.configured === null ? "UNKNOWN" : capability.configured ? "YES" : "NO"}</td></tr>)}
            </tbody></table></div>
          </div>
          <div className="col-5 assurance-stack">
            <div className="card assurance-card">
              <div className="card__head"><div><div className="eyebrow">EXPLICIT TARGETS</div><h3 className="card__title">IOS/IOS-XE devices</h3></div></div>
              <div className="assurance-list compact">
                {devices.devices.map((device) => <article key={device.id} className="assurance-row"><div><strong>{device.label}</strong><span>{device.deviceType} · {device.storage}</span></div>{device.primary ? <StateChip value="PRIMARY" /> : <button type="button" className="btn btn--ghost" disabled={busy !== null} onClick={() => void removeDevice(device)}>Remove</button>}</article>)}
              </div>
            </div>
            <form className="card assurance-card assurance-form" onSubmit={(event) => void addDevice(event)}>
              <div className="eyebrow">WINDOWS CREDENTIAL MANAGER ONLY</div><h3 className="card__title">Observe another device</h3>
              {!devices.keyringAvailable ? <p className="warning-banner warning-banner--inline">Windows Credential Manager is unavailable. Nothing will be written to a file.</p> : null}
              <label>Local label<input required maxLength={80} value={deviceForm.label} onChange={(event) => setDeviceForm({ ...deviceForm, label: event.target.value })} /></label>
              <label>Host or IP<input required maxLength={253} value={deviceForm.host} onChange={(event) => setDeviceForm({ ...deviceForm, host: event.target.value })} /></label>
              <label>Username<input required autoComplete="username" value={deviceForm.username} onChange={(event) => setDeviceForm({ ...deviceForm, username: event.target.value })} /></label>
              <label>Password<input required type="password" autoComplete="new-password" value={deviceForm.password} onChange={(event) => setDeviceForm({ ...deviceForm, password: event.target.value })} /></label>
              <label>Enable secret (optional)<input type="password" autoComplete="new-password" value={deviceForm.enableSecret} onChange={(event) => setDeviceForm({ ...deviceForm, enableSecret: event.target.value })} /></label>
              <label>Platform<select value={deviceForm.deviceType} onChange={(event) => setDeviceForm({ ...deviceForm, deviceType: event.target.value as LabDeviceCreateRequest["deviceType"] })}><option value="cisco_ios">Cisco IOS</option><option value="cisco_xe">Cisco IOS-XE</option></select></label>
              <button className="btn" disabled={!devices.keyringAvailable || busy !== null}>{busy === "device" ? "Saving…" : "Save for read-only observation"}</button>
            </form>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function Metric({ label, value, toneName = "good" }: { label: string; value: number; toneName?: string }) {
  return <div className={`assurance-metric assurance-metric--${toneName}`}><span>{label}</span><strong>{value}</strong></div>;
}

function severityRank(value: FindingSeverity): number {
  return { CRITICAL: 0, WARNING: 1, NOTICE: 2, UNKNOWN: 3 }[value];
}

function FindingList({ findings }: { findings: LabAssuranceState["findings"] }) {
  return <div className="assurance-list">{findings.map((finding) => (
    <article key={finding.id} className="assurance-finding">
      <div className="assurance-finding__head"><div><span>{finding.category}</span><strong>{finding.title}</strong></div><div><StateChip value={finding.severity} /><StateChip value={finding.confidence} /></div></div>
      <p>{finding.detail}</p><p><b>Why it matters:</b> {finding.consequence}</p>
      {finding.remediation ? <p><b>Next check:</b> {finding.remediation}</p> : null}
      <small>{finding.evidenceIds.length} evidence reference{finding.evidenceIds.length === 1 ? "" : "s"}</small>
    </article>
  ))}{!findings.length ? <p className="empty-note">No findings are available until evidence is collected.</p> : null}</div>;
}
