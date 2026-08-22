"use client";

import type { SwitchSummary } from "@/lib/types";
import { statusBadgeClass } from "@/lib/format";
import HealthBadge from "./HealthBadge";

function toHealthState(value: string | undefined | null): "healthy" | "warning" | "critical" | "unknown" {
  const normalized = (value ?? "").toLowerCase();
  if (["green", "healthy", "ok", "normal"].includes(normalized)) return "healthy";
  if (["yellow", "warning", "warn"].includes(normalized)) return "warning";
  if (["red", "critical", "fault", "fail"].includes(normalized)) return "critical";
  return "unknown";
}

export default function SwitchHero({ summary }: { summary: SwitchSummary }) {
  return (
    <section className="card switch-hero" aria-labelledby="switch-name">
      <div className="card__head">
        <div>
          <div className="eyebrow">Physical lab switch</div>
          <h2 className="card__title" id="switch-name">{summary.hostname}</h2>
          <div className="card__subtitle">
            {summary.pid || summary.model}
            {summary.hardwareRevision ? ` · hardware ${summary.hardwareRevision}` : ""}
            {` · IOS ${summary.iosVersion} · uptime ${summary.uptime || "—"}`}
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
        <Stat label="PoE used" value={`${summary.poeUsedW.toFixed(1)} / ${summary.poeAvailableW.toFixed(1)} W`} />
        <Stat
          label="Ports up"
          value={`${summary.connectedPorts.length} connected`}
          sub={`${summary.shutdownPorts.length} administratively disabled`}
        />
      </div>

      <p className="switch-hero__summary">{summary.summary}</p>
    </section>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="stat">
      <div className="stat__label">{label}</div>
      <div className="stat__value">{value}</div>
      {sub ? <div className="stat__sub">{sub}</div> : null}
    </div>
  );
}
