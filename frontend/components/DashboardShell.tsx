"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { motion } from "motion/react";
import {
  api,
  ApiError,
  asApiError,
  type ManagementPathAssurance,
} from "@/lib/api";
import { mergeLiveInterfaces } from "@/lib/live";
import { useLiveOperations } from "@/lib/useLiveOperations";
import type {
  AuditResponse,
  ExpectedRelationship,
  ReconciliationSummary,
  ConfigurationHistoryResponse,
  DiscoveryStatus,
  EnvironmentStatus,
  GuideOperation,
  InterfaceErrorsResponse,
  LogsResponse,
  LiveConnection,
  MacTableResponse,
  MemoryStatus,
  NetworkEventsResponse,
  PoeResponse,
  SetupStatus,
  SwitchSummary,
  TelemetryHistoryResponse,
  TelemetrySnapshotSummary,
  TopologyModel,
} from "@/lib/types";
import type { InterfaceStatusResponse } from "@/lib/api";
import type { UnifiedLabState } from "@/lib/unifiedTypes";
import { fadeUp } from "@/lib/animation";
import {
  getBackendVerification,
  onBackendUnverified,
  type BackendUnverified,
} from "@/lib/backendIntegrity";
import AdvancedOperationsPanel from "./AdvancedOperationsPanel";
import AuditTimeline from "./AuditTimeline";
import ConfigBackupPanel from "./ConfigBackupPanel";
import ChangeHistoryPanel from "./ChangeHistoryPanel";
import ConfigurationHistoryPanel from "./ConfigurationHistoryPanel";
import DeploymentPlanPanel from "./DeploymentPlanPanel";
import DiscoveryStatusPanel from "./DiscoveryStatusPanel";
import CpuMemoryPanel from "./CpuMemoryPanel";
import EnvironmentPanel from "./EnvironmentPanel";
import ErrorPanel from "./ErrorPanel";
import BackendUnverifiedNotice from "./BackendUnverifiedNotice";
import ErrorState from "./ErrorState";
import HealthPanel from "./HealthPanel";
import LabGuide from "./LabGuide";
import LoadingState from "./LoadingState";
import LogsPanel from "./LogsPanel";
import LiveStatusBadge from "./LiveStatusBadge";
import MacTable from "./MacTable";
import ManagementPathReview from "./ManagementPathReview";
import MerakiTopologyOverlay from "./MerakiTopologyOverlay";
import NetworkEventTimeline from "./NetworkEventTimeline";
import NetworkTwin from "./NetworkTwin";
import ObservationHistoryPanel from "./ObservationHistoryPanel";
import ReconciliationSummaryPanel from "./ReconciliationSummaryPanel";
import PoePanel from "./PoePanel";
import PortStatusTable from "./PortStatusTable";
import RuntimeBadge from "./RuntimeBadge";
import SettingsPanel from "./SettingsPanel";
import SetupWizard from "./SetupWizard";
import SummaryCards from "./SummaryCards";
import SwitchHero from "./SwitchHero";
import UnifiedLabPanel from "./UnifiedLabPanel";

const LabAssurancePanel = dynamic(() => import("./LabAssurancePanel"));
const EWPSObservatory = dynamic(() => import("./EWPSObservatory"));

type View = "ewps" | "overview" | "assurance" | "unified" | "network" | "events" | "guide" | "change";

interface DashboardData {
  setup: SetupStatus;
  summary: SwitchSummary;
  interfaces: InterfaceStatusResponse;
  poe: PoeResponse;
  errors: InterfaceErrorsResponse;
  env: EnvironmentStatus;
  cpu: Awaited<ReturnType<typeof api.cpu>>;
  memory: MemoryStatus;
  mac: MacTableResponse;
  logs: LogsResponse;
  audit: AuditResponse;
  telemetry: TelemetrySnapshotSummary;
  history: TelemetryHistoryResponse | null;
  events: NetworkEventsResponse;
  topology: TopologyModel;
  reconciliation: ReconciliationSummary;
  intent: ExpectedRelationship[];
  guideOperations: GuideOperation[];
  configurationHistory: ConfigurationHistoryResponse;
  discovery: DiscoveryStatus;
  sectionErrors: Record<string, string>;
  unified: UnifiedLabState | null;
}

const VIEWS: Array<{ id: View; label: string; description: string }> = [
  { id: "ewps", label: "EWPS Observatory", description: "Shadow path-selection research" },
  { id: "overview", label: "Overview", description: "Current and historical health" },
  { id: "assurance", label: "Lab Assurance", description: "Paths, risks, failure domains" },
  { id: "unified", label: "Unified inventory", description: "Catalyst + Meraki evidence" },
  { id: "network", label: "Visual network", description: "Your device, port by port" },
  { id: "events", label: "What changed", description: "Meaningful network events" },
  { id: "guide", label: "Command guide", description: "Read-only guided inspection" },
  { id: "change", label: "Change control", description: "Plan, back up, verify" },
];

function healthBadgeClass(state: SwitchSummary["health"]["state"]): string {
  if (state === "HEALTHY") return "badge--green";
  if (state === "NOTICE") return "badge--cyan";
  if (state === "CRITICAL") return "badge--red";
  return "badge--amber";
}

export default function DashboardShell() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [failureConnection, setFailureConnection] = useState<LiveConnection | null>(null);
  const [managementPath, setManagementPath] = useState<ManagementPathAssurance | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [activeView, setActiveView] = useState<View>("ewps");
  const [showSetup, setShowSetup] = useState(false);
  const [selectedPort, setSelectedPort] = useState("");
  const [unifiedBusy, setUnifiedBusy] = useState<string | null>(null);
  const [unifiedError, setUnifiedError] = useState<string | null>(null);
  // A backend this session cannot vouch for is a local runtime-integrity
  // fault, not a device diagnosis, and blocks every request.
  const [backendUnverified, setBackendUnverified] =
    useState<BackendUnverified | null>(null);
  // loadAll is captured by a mount-time effect, so the guard reads a ref
  // rather than a state value the closure would have frozen as null.
  const backendUnverifiedRef = useRef<BackendUnverified | null>(null);
  const live = useLiveOperations(Boolean(data?.setup && (data.setup.configured || data.setup.mockMode)));
  const liveMerged = useMemo(() => {
    if (!data?.topology || !data.interfaces || !data.poe) return null;
    return mergeLiveInterfaces(live.topology || data.topology, data.interfaces, data.poe, live.interfaces);
  }, [data, live.interfaces, live.topology]);

  async function loadAll(silent = false) {
    // Never query a backend this session could not verify. Retrying would
    // only re-read the process the shell already rejected.
    if (backendUnverifiedRef.current) {
      setLoading(false);
      setRefreshing(false);
      return;
    }
    if (!silent) setLoading(true);
    setRefreshing(true);
    setError(null);
    setFailureConnection(null);
    setManagementPath(null);
    try {
      const setup = await api.setupStatus();
      if (!setup.configured && !setup.mockMode) {
        setData({ setup } as DashboardData);
        return;
      }
      const [dashboard, guide] = await Promise.all([
        api.dashboard(),
        api.guideOperations(),
      ]);
      const [historyResult, intentResult, unifiedResult] = await Promise.allSettled([
        api.telemetryHistory(dashboard.topology.rootDeviceId, 24),
        api.topologyIntent(dashboard.topology.rootDeviceId),
        api.unifiedLabState(),
      ]);
      const history: TelemetryHistoryResponse | null =
        historyResult.status === "fulfilled" ? historyResult.value : null;
      const intent: ExpectedRelationship[] =
        intentResult.status === "fulfilled" ? intentResult.value.relationships : [];
      const unified: UnifiedLabState | null =
        unifiedResult.status === "fulfilled" ? unifiedResult.value : null;
      setData({
        setup,
        summary: dashboard.summary,
        interfaces: dashboard.interfaces,
        poe: dashboard.poe,
        errors: dashboard.errors,
        env: dashboard.environment,
        cpu: dashboard.cpu,
        memory: dashboard.memory,
        mac: dashboard.macTable,
        logs: dashboard.logs,
        audit: dashboard.audit,
        telemetry: dashboard.telemetry,
        history,
        events: dashboard.events,
        topology: dashboard.topology,
        reconciliation: dashboard.reconciliation,
        intent,
        guideOperations: guide.operations,
        configurationHistory: dashboard.configurationHistory,
        discovery: dashboard.discovery,
        sectionErrors: dashboard.sectionErrors,
        unified,
      });
      setSelectedPort((current) =>
        dashboard.topology.interfaces.some((item) => item.port === current)
          ? current
          : dashboard.topology.interfaces[0]?.port || "",
      );
      setLastRefresh(new Date(dashboard.telemetry.observedAt));
      if (!setup.mockMode) {
        void api.managementPath().then(setManagementPath).catch(() => undefined);
      }
    } catch (cause) {
      const apiError = asApiError(cause);
      setError(apiError);
      if (apiError.backendResponded && apiError.category.startsWith("DEVICE_")) {
        const [snapshotResult, pathResult] = await Promise.allSettled([
          api.liveState(),
          api.managementPath(),
        ]);
        if (snapshotResult.status === "fulfilled") {
          setFailureConnection(snapshotResult.value.connection);
        }
        if (pathResult.status === "fulfilled") {
          setManagementPath(pathResult.value);
        }
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    let unsubscribe: (() => void) | undefined;
    void onBackendUnverified((event) => {
      if (cancelled) return;
      // Drop anything already fetched: it came from a process the shell has
      // now rejected.
      backendUnverifiedRef.current = event;
      setBackendUnverified(event);
      setData(null);
      setError(null);
      setManagementPath(null);
      setLoading(false);
    }).then((off) => {
      if (cancelled) off();
      else unsubscribe = off;
    });
    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const verdict = await getBackendVerification();
      if (cancelled) return;
      if (verdict) {
        // Never query a backend the shell already rejected.
        backendUnverifiedRef.current = verdict;
        setBackendUnverified(verdict);
        setLoading(false);
        return;
      }
      void loadAll();
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function refreshMeraki() {
    setUnifiedBusy("refresh");
    setUnifiedError(null);
    try {
      await api.refreshMeraki();
      const unified = await api.unifiedLabState();
      setData((current) => current ? { ...current, unified } : current);
    } catch (cause) {
      setUnifiedError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setUnifiedBusy(null);
    }
  }

  async function decideIdentity(
    linkId: string,
    decision: "confirm" | "reject" | "clear",
  ) {
    setUnifiedBusy(linkId);
    setUnifiedError(null);
    try {
      const unified = await api.decideUnifiedIdentity(linkId, decision);
      setData((current) => current ? { ...current, unified } : current);
    } catch (cause) {
      setUnifiedError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setUnifiedBusy(null);
    }
  }

  // Checked before loading and before every error state: a backend the shell
  // rejected must never surface as a Catalyst or management-path diagnosis.
  if (backendUnverified) {
    return <BackendUnverifiedNotice state={backendUnverified} />;
  }
  if (loading) return <LoadingState />;
  if (error && !data) {
    return (
      <ErrorState
        error={error}
        onRetry={() => void loadAll()}
        lastSuccessfulObservation={
          managementPath?.lastKnownGood?.lastDeviceSuccessAt
          || failureConnection?.lastSuccessAt
        }
        sessionState={failureConnection?.state}
        managementPath={managementPath}
      />
    );
  }
  if (!data) {
    return (
      <ErrorState
        error={new ApiError({
          status: null,
          code: "no_dashboard_data",
          message: "No dashboard observation is available.",
          category: "BACKEND_INTERNAL_ERROR",
        })}
        onRetry={() => void loadAll()}
      />
    );
  }

  if (!data.setup.configured && !data.setup.mockMode) {
    if (showSetup) return <SetupWizard onComplete={() => { setShowSetup(false); void loadAll(); }} />;
    return (
      <div className="app-shell">
        <div className="local-banner"><span>LOCAL-ONLY</span>EWPS runs as a read-only shadow observer on this PC.</div>
        <div className="container">
          <header className="ewps-standalone-header">
            <div className="ewps-standalone-header__brand"><div className="header__logo">SO</div><div><strong>SwitchOps Research</strong><span>EWPS v0.2.4 Alpha · Recovery Safety Architecture</span></div></div>
            <button className="btn btn--ghost" onClick={() => setShowSetup(true)}>Configure Catalyst observation</button>
          </header>
          <EWPSObservatory />
        </div>
      </div>
    );
  }

  const summary = data.summary;
  const currentTopology = liveMerged?.topology || data.topology;
  const currentInterfaces = liveMerged?.interfaces || data.interfaces;
  const currentPoe = liveMerged?.poe || data.poe;
  const selectedInterface = currentTopology.interfaces.find((item) => item.port === selectedPort);
  // A host-side management-path diagnosis must never be reported as the device
  // being offline. Mirrors the same set ErrorState treats as path-specific.
  const hostPathDiagnosis =
    managementPath &&
    ["HOST_NETWORK_CHANGED", "HOST_ROUTE_MISSING", "HOST_PATH_DEGRADED"].includes(
      managementPath.diagnosis.conclusion,
    )
      ? managementPath.diagnosis
      : null;

  return (
    <div className="app-shell">
      <div className="local-banner">
        <span>LOCAL-ONLY</span>
        Data stays on this PC. Device access is serialized and commands are allowlisted.
      </div>
      <div className="container">
        <header className="header product-header">
          <div className="header__brand">
            <div className="header__logo">SO</div>
            <div>
              <div className="header__title">SwitchOps</div>
              <div className="header__subtitle">
                {summary.hostname} · {summary.pid || summary.model} · IOS {summary.iosVersion}
              </div>
            </div>
          </div>
          <div className="header__meta">
            <RuntimeBadge setup={data.setup} />
            <LiveStatusBadge
              connection={live.connection}
              streamState={live.streamState}
              freshness={live.freshness}
            />
            <span className={`badge ${healthBadgeClass(summary.health.state)}`}>
              {summary.health.state === "HEALTHY" ? <span className="pulse" /> : <span className="dot" />}
              {summary.health.state}
            </span>
            {live.config.runningModified ? <span className="badge badge--amber">UNSAVED CONFIG</span> : null}
            {lastRefresh ? <span className="badge">deep {lastRefresh.toLocaleTimeString()}</span> : null}
            <button className="btn" onClick={() => void loadAll(true)} disabled={refreshing}>
              {refreshing ? "Observing…" : "Deep refresh"}
            </button>
            <button className="btn btn--ghost" onClick={() => setSettingsOpen(true)}>Settings</button>
          </div>
        </header>

        <nav className="view-tabs" aria-label="SwitchOps views">
          {VIEWS.map((view) => (
            <button
              type="button"
              key={view.id}
              className={activeView === view.id ? "is-active" : ""}
              aria-current={activeView === view.id ? "page" : undefined}
              onClick={() => setActiveView(view.id)}
            >
              <strong>{view.label}</strong>
              <span>{view.description}</span>
            </button>
          ))}
        </nav>

        {error ? (
          <div
            className="warning-banner warning-banner--inline warning-banner--red stale-telemetry"
            role="alert"
          >
            <strong>
              {hostPathDiagnosis
                ? hostPathDiagnosis.headline.toUpperCase()
                : error.category === "DEVICE_HOST_KEY_CHANGED"
                ? "DEVICE CONNECTION BLOCKED"
                : error.category.startsWith("DEVICE_")
                  ? "DEVICE OFFLINE / RECONNECTING"
                  : "REFRESH FAILED"}
            </strong>
            {managementPath ? <span>{managementPath.diagnosis.summary}</span> : null}
            {managementPath ? (
              <span className="mono management-path-conclusion">
                Diagnosis: {managementPath.diagnosis.conclusion}
              </span>
            ) : null}
            <span>Data shown below is stale. Valid topology, interface state, and history have been retained.</span>
            {managementPath ? (
              <span>
                Confidence: {managementPath.diagnosis.confidence} · Current source: {managementPath.current.sourceIp || "unknown"}
                {managementPath.current.prefixLength == null ? "" : `/${managementPath.current.prefixLength}`}
                {managementPath.current.route.nextHop ? ` via ${managementPath.current.route.nextHop}` : ""}
              </span>
            ) : null}
            <span>Configured device: Configured Catalyst</span>
            <span>
              Last observation: {
                managementPath?.lastKnownGood?.lastDeviceSuccessAt
                  ? new Date(managementPath.lastKnownGood.lastDeviceSuccessAt).toLocaleString()
                  : new Date(data.telemetry.observedAt).toLocaleString()
              }
            </span>
            <span>
              Session state: {failureConnection?.state || live.connection.state || "offline"}
            </span>
            {managementPath ? <ManagementPathReview assurance={managementPath} /> : null}
            <span>{error.message}</span>
            {error.detail ? <span>{error.detail}</span> : null}
            <button type="button" className="btn" onClick={() => void loadAll(true)} disabled={refreshing}>
              {refreshing ? "Reconnecting…" : "Retry connection"}
            </button>
            {error.category === "BACKEND_UNREACHABLE" && !error.backendResponded ? (
              <span>Ensure the backend sidecar is running on 127.0.0.1:8765.</span>
            ) : null}
          </div>
        ) : null}

        {Object.keys(data.sectionErrors).length ? (
          <div className="warning-banner warning-banner--inline partial-telemetry">
            Partial observation: {Object.keys(data.sectionErrors).join(", ")} unavailable. Other sections remain usable.
          </div>
        ) : null}

        <motion.main
          key={activeView}
          className="view-content"
          initial="hidden"
          animate="show"
          variants={{ show: { transition: { staggerChildren: 0.04 } } }}
        >
          {activeView === "ewps" ? (
            <motion.div variants={fadeUp}>
              <EWPSObservatory />
            </motion.div>
          ) : null}
          {activeView === "overview" ? (
            <>
              <motion.div variants={fadeUp}><SwitchHero summary={summary} /></motion.div>
              <motion.div variants={fadeUp}>
                <SummaryCards summary={summary} poe={currentPoe} telemetry={data.telemetry} />
              </motion.div>
              <div className="grid grid--12">
                <motion.div className="col-6" variants={fadeUp}><HealthPanel health={summary.health} /></motion.div>
                <motion.div className="col-6" variants={fadeUp}>
                  <ReconciliationSummaryPanel
                    reconciliation={data.reconciliation}
                    onInspect={(port) => { setSelectedPort(port); setActiveView("network"); }}
                  />
                </motion.div>
                <motion.div className="col-6" variants={fadeUp}><ObservationHistoryPanel history={data.history} /></motion.div>
                <motion.div className="col-4" variants={fadeUp}><EnvironmentPanel env={data.env} /></motion.div>
                <motion.div className="col-8" variants={fadeUp}><CpuMemoryPanel cpu={data.cpu} memory={data.memory} /></motion.div>
                <motion.div className="col-12" variants={fadeUp}><ErrorPanel errors={data.errors} telemetry={data.telemetry} /></motion.div>
              </div>
            </>
          ) : null}

          {activeView === "unified" ? (
            <motion.div variants={fadeUp}>
              {unifiedError ? (
                <div className="warning-banner warning-banner--inline">{unifiedError}</div>
              ) : null}
              {data.unified ? (
                <UnifiedLabPanel
                  state={data.unified}
                  busy={unifiedBusy}
                  onRefreshMeraki={() => void refreshMeraki()}
                  onDecision={(linkId, decision) => void decideIdentity(linkId, decision)}
                />
              ) : (
                <section className="card"><p className="empty-note unified-empty">Unified evidence is temporarily unavailable. Catalyst views are unaffected.</p></section>
              )}
            </motion.div>
          ) : null}

          {activeView === "assurance" ? (
            <motion.div variants={fadeUp}>
              <LabAssurancePanel />
            </motion.div>
          ) : null}

          {activeView === "network" ? (
            <>
              <motion.div variants={fadeUp}>
                <NetworkTwin
                  topology={currentTopology}
                  telemetry={data.telemetry}
                  events={data.events.events}
                  selectedPort={selectedPort}
                  onSelectPort={setSelectedPort}
                  model={summary.pid || summary.model}
                  reconciliation={data.reconciliation}
                  intent={data.intent}
                  onIntentChange={() => void loadAll(true)}
                />
              </motion.div>
              {data.unified ? (
                <motion.div variants={fadeUp}>
                  <MerakiTopologyOverlay state={data.unified} />
                </motion.div>
              ) : null}
              <motion.div variants={fadeUp}>
                <AdvancedOperationsPanel key={selectedPort} selected={selectedInterface} live={live} />
              </motion.div>
              <motion.div variants={fadeUp}>
                <DiscoveryStatusPanel
                  discovery={{
                    ...data.discovery,
                    lldp:
                      live.lldp && live.lldp.state !== "unknown"
                        ? live.lldp
                        : data.discovery.lldp,
                  }}
                />
              </motion.div>
              <motion.details className="advanced-disclosure" variants={fadeUp}>
                <summary>
                  <span className="advanced-disclosure__label">Reference tables</span>
                  <span className="advanced-disclosure__hint">
                    Interface status, Power over Ethernet, MAC address table
                  </span>
                </summary>
                <div className="grid grid--12 advanced-disclosure__body">
                  <div className="col-12"><PortStatusTable interfaces={currentInterfaces.interfaces} /></div>
                  <div className="col-6"><PoePanel poe={currentPoe} /></div>
                  <div className="col-6"><MacTable mac={data.mac} /></div>
                </div>
              </motion.details>
            </>
          ) : null}

          {activeView === "events" ? (
            <>
              <motion.div variants={fadeUp}>
                <NetworkEventTimeline events={data.events.events} devices={data.topology.devices} />
              </motion.div>
              <motion.details className="advanced-disclosure" variants={fadeUp}>
                <summary>
                  <span className="advanced-disclosure__label">
                    Raw switch logs · {data.logs.entries.length}
                  </span>
                  <span className="advanced-disclosure__hint">
                    Unparsed IOS messages exactly as the switch reported them
                  </span>
                </summary>
                <div className="advanced-disclosure__body">
                  <LogsPanel logs={data.logs} />
                </div>
              </motion.details>
              <motion.details className="advanced-disclosure" variants={fadeUp}>
                <summary>
                  <span className="advanced-disclosure__label">
                    Developer audit trail · {data.audit.events.length}
                  </span>
                  <span className="advanced-disclosure__hint">
                    Every command SwitchOps itself ran, with timings
                  </span>
                </summary>
                <div className="advanced-disclosure__body">
                  <AuditTimeline events={data.audit.events} />
                </div>
              </motion.details>
            </>
          ) : null}

          {activeView === "guide" ? (
            <motion.div variants={fadeUp}>
              <LabGuide operations={data.guideOperations} interfaces={currentInterfaces.interfaces} />
            </motion.div>
          ) : null}

          {activeView === "change" ? (
            <>
              <motion.div variants={fadeUp}>
                <div className="change-intro">
                  <div className="eyebrow">How a change happens here</div>
                  <ol className="change-flow">
                    <li><strong>Learn</strong><span>Understand the port in the command guide</span></li>
                    <li><strong>Plan</strong><span>Declare one bounded operation and its expected effects</span></li>
                    <li><strong>Preflight</strong><span>Check policy, control path, rollback and current evidence</span></li>
                    <li><strong>Execute</strong><span>Unlock explicitly and run the trusted transaction</span></li>
                    <li><strong>Assure</strong><span>Compare before/after evidence and retain the outcome</span></li>
                  </ol>
                </div>
              </motion.div>
              <motion.div variants={fadeUp}>
                <AdvancedOperationsPanel key={`change-${selectedPort}`} selected={selectedInterface} live={live} />
              </motion.div>
              <motion.div variants={fadeUp}>
                <DeploymentPlanPanel interfaces={currentInterfaces.interfaces} />
              </motion.div>
              <motion.div variants={fadeUp}>
                <ChangeHistoryPanel active={live.changeSession} />
              </motion.div>
              <div className="grid grid--12">
                <motion.div className="col-7" variants={fadeUp}>
                  <ConfigurationHistoryPanel
                    entries={data.configurationHistory.entries}
                    onChange={() => void loadAll(true)}
                  />
                </motion.div>
                <motion.div className="col-5" variants={fadeUp}>
                  <ConfigBackupPanel onChange={() => void loadAll(true)} />
                </motion.div>
              </div>
            </>
          ) : null}
        </motion.main>

        <footer className="footer">
          SwitchOps · real device observation · local-only · bounded commands
        </footer>
      </div>

      {settingsOpen ? (
        <SettingsPanel
          setup={data.setup}
          interfaces={currentInterfaces.interfaces}
          onClose={() => setSettingsOpen(false)}
          onChange={() => {
            setSettingsOpen(false);
            void loadAll();
          }}
        />
      ) : null}
    </div>
  );
}
