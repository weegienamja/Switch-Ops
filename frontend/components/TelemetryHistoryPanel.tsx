import type { DeviceObservationPoint, TelemetryHistoryResponse } from "@/lib/types";

type MetricKey = "temperatureC" | "cpu5Sec" | "memoryUsedPct" | "poeUsedW";

const METRICS: Array<{ metric: MetricKey; label: string; suffix: string; ceiling: number }> = [
  { metric: "temperatureC", label: "Temperature", suffix: "°C", ceiling: 90 },
  { metric: "cpu5Sec", label: "CPU", suffix: "%", ceiling: 100 },
  { metric: "memoryUsedPct", label: "Memory", suffix: "%", ceiling: 100 },
  { metric: "poeUsedW", label: "PoE draw", suffix: " W", ceiling: 124 },
];

export default function TelemetryHistoryPanel({
  history,
}: {
  history: TelemetryHistoryResponse | null;
}) {
  const observations = history?.observations || [];
  return (
    <section className="card telemetry-history" aria-labelledby="telemetry-history-title">
      <div className="card__head">
        <div>
          <h2 className="card__title" id="telemetry-history-title">Last 24 hours</h2>
          <div className="card__subtitle">
            Refresh-driven local observations. No background polling is running.
          </div>
        </div>
        <span className="badge">{observations.length} samples</span>
      </div>
      <div className="trend-grid">
        {METRICS.map((metric) => (
          <Trend key={metric.metric} observations={observations} {...metric} />
        ))}
      </div>
      {observations.length < 2 ? (
        <p className="empty-note telemetry-history__note">
          This is the baseline. Refresh later to turn cumulative values into change-over-time evidence.
        </p>
      ) : null}
    </section>
  );
}

function Trend({
  observations,
  metric: metricKey,
  label,
  suffix,
  ceiling,
}: {
  observations: DeviceObservationPoint[];
  metric: MetricKey;
  label: string;
  suffix: string;
  ceiling: number;
}) {
  const points = observations.slice(-40);
  const latest = points.at(-1)?.[metricKey];
  return (
    <div className="trend">
      <div className="trend__head">
        <span>{label}</span>
        <strong>{latest == null ? "—" : `${Number(latest).toFixed(metricKey === "poeUsedW" ? 1 : 0)}${suffix}`}</strong>
      </div>
      <div className="trend__bars" aria-label={`${label} history`}>
        {points.length ? points.map((point, index) => {
          const value = point[metricKey];
          const height = value == null ? 4 : Math.max(4, Math.min(100, Number(value) / ceiling * 100));
          return (
            <span
              key={`${point.timestamp}-${index}`}
              style={{ height: `${height}%` }}
              title={`${new Date(point.timestamp).toLocaleString()}: ${value ?? "unavailable"}${value == null ? "" : suffix}`}
            />
          );
        }) : <span className="trend__empty-bar" />}
      </div>
    </div>
  );
}
