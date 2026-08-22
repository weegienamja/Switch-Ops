"use client";
import type { SwitchSummary } from "@/lib/types";
import { statusBadgeClass } from "@/lib/format";
import HealthBadge from "./HealthBadge";

function toHealthState(s: string | undefined | null): "healthy" | "warning" | "critical" | "unknown" {
  const v = (s ?? "").toLowerCase();
  if (v === "green" || v === "healthy" || v === "ok" || v === "normal") return "healthy";
  if (v === "yellow" || v === "warning" || v === "warn") return "warning";
  if (v === "red" || v === "critical" || v === "fault" || v === "fail") return "critical";
  return "unknown";
}

export default function SwitchHero({ summary }: { summary: SwitchSummary }) {
  return (
    <div className="card">
      <div className="card__head">
        <div>
          <h2 className="card__title">{summary.hostname}</h2>
          <div className="card__subtitle">
            {summary.pid || summary.model}
            {summary.hardwareRevision ? ` · hardware ${summary.hardwareRevision}` : ""}
            {` · serial ${summary.serial || "—"} · uptime ${summary.uptime || "—"}`}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <HealthBadge
            state={toHealthState(summary.temperatureState)}
            label={`temp ${summary.temperatureC ?? "—"}°C`}
          />
          <span className={`badge ${statusBadgeClass(summary.temperatureState)}`}>
            {summary.temperatureState}
          </span>
        </div>
      </div>

      <div className="grid grid--4">
        <Stat label="Management IP" value={summary.managementIp} />
        <Stat label="Default gateway" value={summary.gateway} />
        <Stat
          label="PoE used"
          value={`${summary.poeUsedW.toFixed(1)} / ${summary.poeAvailableW.toFixed(1)} W`}
        />
        <Stat
          label="Ports up"
          value={`${summary.connectedPorts.length} connected`}
          sub={`${summary.shutdownPorts.length} shutdown`}
        />
      </div>

      {summary.summary && (
        <div
          className="mono"
          style={{
            marginTop: 14,
            fontSize: 12,
            color: "var(--text-muted)",
            borderTop: "1px solid var(--border-soft)",
            paddingTop: 12,
          }}
        >
          {summary.summary}
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="stat">
      <div className="stat__label">{label}</div>
      <div className="stat__value">{value}</div>
      {sub && <div className="stat__sub">{sub}</div>}
    </div>
  );
}
