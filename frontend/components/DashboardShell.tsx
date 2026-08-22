"use client";

import { useEffect, useMemo, useState } from "react";
import { motion } from "motion/react";
import { api } from "@/lib/api";
import { mergeLiveInterfaces } from "@/lib/live";
import { useLiveOperations } from "@/lib/useLiveOperations";
import type {
  AuditResponse,
  ExpectedRelationship,
  ReconciliationSummary,
  ConfigurationHistoryResponse,
  EnvironmentStatus,
  GuideOperation,
  InterfaceErrorsResponse,
  LogsResponse,
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
import { fadeUp } from "@/lib/animation";
import AdvancedOperationsPanel from "./AdvancedOperationsPanel";
import AuditTimeline from "./AuditTimeline";
import ConfigBackupPanel from "./ConfigBackupPanel";
import ConfigurationHistoryPanel from "./ConfigurationHistoryPanel";
import DeploymentPlanPanel from "./DeploymentPlanPanel";
import CpuMemoryPanel from "./CpuMemoryPanel";
import EnvironmentPanel from "./EnvironmentPanel";
import ErrorPanel from "./ErrorPanel";
import ErrorState from "./ErrorState";
import HealthPanel from "./HealthPanel";
import LabGuide from "./LabGuide";
import LoadingState from "./LoadingState";
import LogsPanel from "./LogsPanel";
import LiveStatusBadge from "./LiveStatusBadge";
import MacTable from "./MacTable";
import MockScenarioPanel from "./MockScenarioPanel";
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

type View = "overview" | "network" | "events" | "guide" | "change";

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
  mockScenario: "baseline" | "ap_attached";
  sectionErrors: Record<string, string>;
}

const VIEWS: Array<{ id: View; label: string; description: string }> = [
  { id: "overview", label: "Overview", description: "Current and historical health" },
  { id: "network", label: "Visual network", description: "Your lab, port by port" },
  { id: "events", label: "What changed", description: "Meaningful network events" },
  { id: "guide", label: "Lab Guide", description: "Read-only guided learning" },
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
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [scenarioBusy, setScenarioBusy] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [activeView, setActiveView] = useState<View>("overview");
  const [selectedPort, setSelectedPort] = useState("Gi0/4");
  const live = useLiveOperations(Boolean(data?.setup && (data.setup.configured || data.setup.mockMode)));
  const liveMerged = useMemo(() => {
    if (!data?.topology || !data.interfaces || !data.poe) return null;
    return mergeLiveInterfaces(data.topology, data.interfaces, data.poe, live.interfaces);
  }, [data, live.interfaces]);

  async function loadAll(silent = false) {
    if (!silent) setLoading(true);
    setRefreshing(true);
    setError(null);
    try {
      const setup = await api.setupStatus();
      if (!setup.configured && !setup.mockMode) {
        setData({ setup } as DashboardData);
        return;
      }
      const [dashboard, guide, mockScenario] = await Promise.all([
        api.dashboard(),
        api.guideOperations(),
        setup.mockMode ? api.mockScenario() : Promise.resolve(null),
      ]);
      let history: TelemetryHistoryResponse | null = null;
      try {
        history = await api.telemetryHistory(dashboard.topology.rootDeviceId, 24);
      } catch {
        // History is a local enhancement; current switch telemetry remains usable.
      }
      let intent: ExpectedRelationship[] = [];
      try {
        intent = (await api.topologyIntent(dashboard.topology.rootDeviceId)).relationships;
      } catch {
        // Recorded intent is optional; reconciliation falls back to descriptions.
      }
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
        mockScenario: mockScenario?.scenario || "baseline",
        sectionErrors: dashboard.sectionErrors,
      });
      setLastRefresh(new Date(dashboard.telemetry.observedAt));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    void loadAll();
  }, []);

  async function changeMockScenario(scenario: "baseline" | "ap_attached") {
    setScenarioBusy(true);
    try {
      await api.setMockScenario(scenario);
      setActiveView("network");
      setSelectedPort("Gi0/4");
      await loadAll(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setScenarioBusy(false);
    }
  }

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} onRetry={() => void loadAll()} />;
  if (!data) return <ErrorState message="No data." onRetry={() => void loadAll()} />;

  if (!data.setup.configured && !data.setup.mockMode) {
    return <SetupWizard onComplete={() => void loadAll()} />;
  }

  const summary = data.summary;
  const currentTopology = liveMerged?.topology || data.topology;
  const currentInterfaces = liveMerged?.interfaces || data.interfaces;
  const currentPoe = liveMerged?.poe || data.poe;
  const selectedInterface = currentTopology.interfaces.find((item) => item.port === selectedPort);
  return (
    <div className="app-shell">
      <div className="local-banner">
        <span>LOCAL LAB</span>
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

          {activeView === "network" ? (
            <>
              {data.setup.mockMode ? (
                <motion.div variants={fadeUp}>
                  <MockScenarioPanel
                    scenario={data.mockScenario}
                    busy={scenarioBusy || refreshing}
                    onChange={(scenario) => void changeMockScenario(scenario)}
                  />
                </motion.div>
              ) : null}
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
              <motion.div variants={fadeUp}>
                <AdvancedOperationsPanel key={selectedPort} selected={selectedInterface} live={live} />
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
                    <li><strong>Learn</strong><span>Understand the port in the Lab Guide</span></li>
                    <li><strong>Plan</strong><span>Describe the intent and validate it</span></li>
                    <li><strong>Review</strong><span>Read the proposed IOS and the impact</span></li>
                    <li><strong>Apply</strong><span>Use bounded controls in Visual network</span></li>
                  </ol>
                </div>
              </motion.div>
              <motion.div variants={fadeUp}>
                <DeploymentPlanPanel interfaces={currentInterfaces.interfaces} />
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
          SwitchOps · real hardware as an interactive lab · local-only · bounded commands
        </footer>
      </div>

      {settingsOpen ? (
        <SettingsPanel
          setup={data.setup}
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
