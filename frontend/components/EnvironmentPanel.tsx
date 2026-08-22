"use client";
import type { EnvironmentStatus } from "@/lib/types";
import { statusBadgeClass } from "@/lib/format";

export default function EnvironmentPanel({ env }: { env: EnvironmentStatus }) {
  return (
    <div className="card">
      <div className="card__head">
        <h3 className="card__title">Environment</h3>
        <span className={`badge ${statusBadgeClass(env.state)}`}>{env.state}</span>
      </div>
      <dl className="kv">
        <dt>Temperature</dt>
        <dd>{env.temperatureC != null ? `${env.temperatureC}°C` : "—"}</dd>
        <dt>Yellow threshold</dt>
        <dd>{env.yellowThresholdC != null ? `${env.yellowThresholdC}°C` : "—"}</dd>
        <dt>Red threshold</dt>
        <dd>{env.redThresholdC != null ? `${env.redThresholdC}°C` : "—"}</dd>
        <dt>Power</dt>
        <dd>{env.powerStatus}</dd>
      </dl>
    </div>
  );
}
