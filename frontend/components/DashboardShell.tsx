"use client";
import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { api } from "@/lib/api";
import type {
  AuditResponse,
  EnvironmentStatus,
  InterfaceErrorsResponse,
  LogsResponse,
  MacTableResponse,
  PoeResponse,
  SetupStatus,
  SwitchSummary,
} from "@/lib/types";
import type { InterfaceStatusResponse } from "@/lib/api";
import { fadeUp } from "@/lib/animation";
import SetupWizard from "./SetupWizard";
import SwitchHero from "./SwitchHero";
import SummaryCards from "./SummaryCards";
import PortTopologyMap from "./PortTopologyMap";
import PortStatusTable from "./PortStatusTable";
import PoePanel from "./PoePanel";
import EnvironmentPanel from "./EnvironmentPanel";
import CpuMemoryPanel from "./CpuMemoryPanel";
import ErrorPanel from "./ErrorPanel";
import MacTable from "./MacTable";
import LogsPanel from "./LogsPanel";
import ConfigBackupPanel from "./ConfigBackupPanel";
import AuditTimeline from "./AuditTimeline";
import SafeControlPanel from "./SafeControlPanel";
import SettingsPanel from "./SettingsPanel";
import LoadingState from "./LoadingState";
import ErrorState from "./ErrorState";

interface DashboardData {
  setup: SetupStatus;
  summary: SwitchSummary;
  interfaces: InterfaceStatusResponse;
  poe: PoeResponse;
  errors: InterfaceErrorsResponse;
  env: EnvironmentStatus;
  cpu: Awaited<ReturnType<typeof api.cpu>>;
  memory: Awaited<ReturnType<typeof api.memory>>;
  mac: MacTableResponse;
  logs: LogsResponse;
  audit: AuditResponse;
  sectionErrors: Record<string, string>;
}

export default function DashboardShell() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  async function loadAll(silent = false) {
    if (!silent) setLoading(true);
    setRefreshing(true);
    setError(null);
    try {
      const setup = await api.setupStatus();
      if (!setup.configured && !setup.mockMode) {
        setData({
          setup,
        } as DashboardData);
        setLoading(false);
        setRefreshing(false);
        return;
      }
      const dashboard = await api.dashboard();
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
        sectionErrors: dashboard.sectionErrors,
      });
      setLastRefresh(new Date());
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} onRetry={() => loadAll()} />;
  if (!data) return <ErrorState message="No data." onRetry={() => loadAll()} />;

  if (!data.setup.configured && !data.setup.mockMode) {
    return (
      <SetupWizard
        onComplete={() => loadAll()}
      />
    );
  }

  const s = data.summary;

  return (
    <div className="app-shell">
      <div className="warning-banner">
        Local lab dashboard — do not expose publicly. Access locally or via private VPN.
      </div>
      <div className="container">
        <header className="header">
          <div className="header__brand">
            <div className="header__logo">JLS</div>
            <div>
              <div className="header__title">SwitchOps</div>
              <div className="header__subtitle">
                {s.hostname} · {s.model} · {s.managementIp} · IOS {s.iosVersion}
              </div>
            </div>
          </div>
          <div className="header__meta">
            <span className={`badge ${data.setup.mockMode ? "badge--cyan" : "badge--green"}`}>
              <span className="dot" />
              {data.setup.mockMode ? "mock mode" : "real device"}
            </span>
            <span
              className={`badge ${
                s.telemetryComplete && s.healthy ? "badge--green" : "badge--amber"
              }`}
            >
              {s.telemetryComplete && s.healthy ? (
                <span className="pulse" />
              ) : (
                <span className="dot" />
              )}
              {!s.telemetryComplete ? "partial telemetry" : s.healthy ? "healthy" : "attention"}
            </span>
            {lastRefresh && (
              <span className="badge">last refresh {lastRefresh.toLocaleTimeString()}</span>
            )}
            <button
              className="btn"
              onClick={() => loadAll(true)}
              disabled={refreshing}
            >
              {refreshing ? "Refreshing…" : "Refresh"}
            </button>
            <button
              className="btn btn--ghost"
              onClick={() => setSettingsOpen(true)}
            >
              Settings
            </button>
          </div>
        </header>

        <motion.div
          className="grid grid--12"
          initial="hidden"
          animate="show"
          variants={{ show: { transition: { staggerChildren: 0.04 } } }}
        >
          <motion.div className="col-12" variants={fadeUp} custom={0}>
            <SwitchHero summary={s} />
          </motion.div>

          {Object.keys(data.sectionErrors).length > 0 ? (
            <motion.div className="col-12" variants={fadeUp} custom={0}>
              <div className="warning-banner warning-banner--inline">
                Partial telemetry: {Object.keys(data.sectionErrors).join(", ")} unavailable.
                Other dashboard data remains live.
              </div>
            </motion.div>
          ) : null}

          <motion.div className="col-12" variants={fadeUp} custom={0}>
            <SummaryCards summary={s} errors={data.errors} poe={data.poe} />
          </motion.div>

          <motion.div className="col-8" variants={fadeUp} custom={1}>
            <PortTopologyMap interfaces={data.interfaces.interfaces} />
          </motion.div>
          <motion.div className="col-4" variants={fadeUp} custom={2}>
            <EnvironmentPanel env={data.env} />
          </motion.div>

          <motion.div className="col-8" variants={fadeUp} custom={3}>
            <PortStatusTable interfaces={data.interfaces.interfaces} />
          </motion.div>
          <motion.div className="col-4" variants={fadeUp} custom={4}>
            <CpuMemoryPanel cpu={data.cpu} memory={data.memory} />
          </motion.div>

          <motion.div className="col-6" variants={fadeUp} custom={5}>
            <PoePanel poe={data.poe} />
          </motion.div>
          <motion.div className="col-6" variants={fadeUp} custom={6}>
            <ErrorPanel errors={data.errors} />
          </motion.div>

          <motion.div className="col-6" variants={fadeUp} custom={7}>
            <MacTable mac={data.mac} />
          </motion.div>
          <motion.div className="col-6" variants={fadeUp} custom={8}>
            <LogsPanel logs={data.logs} />
          </motion.div>

          <motion.div className="col-6" variants={fadeUp} custom={9}>
            <ConfigBackupPanel onChange={() => loadAll(true)} />
          </motion.div>
          <motion.div className="col-6" variants={fadeUp} custom={10}>
            <SafeControlPanel
              setup={data.setup}
              interfaces={data.interfaces.interfaces}
              onChange={() => loadAll(true)}
            />
          </motion.div>

          <motion.div className="col-12" variants={fadeUp} custom={11}>
            <AuditTimeline events={data.audit.events} />
          </motion.div>
        </motion.div>

        <div className="footer">
          SwitchOps · local-only · all commands allowlisted · backups stored locally
        </div>
      </div>

      {settingsOpen && (
        <SettingsPanel
          setup={data.setup}
          onClose={() => setSettingsOpen(false)}
          onChange={() => {
            setSettingsOpen(false);
            loadAll();
          }}
        />
      )}
    </div>
  );
}
