import type { DeviceObservationPoint, TelemetryHistoryResponse } from "@/lib/types";

type MetricKey = "temperatureC" | "cpu5Sec" | "memoryUsedPct" | "poeUsedW";

const METRICS: Array<{ metric: MetricKey; label: string; suffix: string; ceiling: number }> = [
  { metric: "temperatureC", label: "Temperature", suffix: "°C", ceiling: 90 },
  { metric: "cpu5Sec", label: "CPU", suffix: "%", ceiling: 100 },
  { metric: "memoryUsedPct", label: "Memory", suffix: "%", ceiling: 100 },
  { metric: "poeUsedW", label: "PoE draw", suffix: " W", ceiling: 124 },
];

/** Below this, a line chart would imply a continuity the data does not have. */
const MIN_POINTS_FOR_TREND = 3;

function formatValue(value: number | null | undefined, metric: MetricKey, suffix: string): string {
  if (value == null) return "—";
  return `${Number(value).toFixed(metric === "poeUsedW" ? 1 : 0)}${suffix}`;
}

/**
 * Refresh-driven observation history.
 *
 * SwitchOps has no background poller: a row exists only because somebody
 * pressed refresh. The wording and the chart both have to say that, otherwise
 * four points across a day read as continuous monitoring.
 */
export default function ObservationHistoryPanel({
  history,
}: {
  history: TelemetryHistoryResponse | null;
}) {
  const observations = history?.observations || [];
  const count = observations.length;
  const enoughForTrend = count >= MIN_POINTS_FOR_TREND;
  const first = observations[0];
  const latest = observations[count - 1];

  return (
    <section className="card observation-history" aria-labelledby="observation-history-title">
      <div className="card__head">
        <div>
          <h2 className="card__title" id="observation-history-title">Recent observations</h2>
          <div className="card__subtitle">
            SwitchOps records one observation each time you refresh. No background polling is
            running, so these are the only observations that exist.
          </div>
        </div>
        <span className="badge">
          {count} observation{count === 1 ? "" : "s"} · last 24h
        </span>
      </div>

      {count === 0 ? (
        <p className="empty-note observation-history__note">
          No observation has been recorded in the last 24 hours. Press “Refresh observation” to take
          one.
        </p>
      ) : (
        <>
          <div className="trend-grid">
            {METRICS.map((metric) => (
              <Trend
                key={metric.metric}
                observations={observations}
                enoughForTrend={enoughForTrend}
                {...metric}
              />
            ))}
          </div>
          <p className="empty-note observation-history__note">
            {enoughForTrend ? (
              <>
                Points are placed by the time they were taken, and gaps between them are gaps in
                observation — not flat readings.
                {first && latest ? (
                  <>
                    {" "}
                    First {new Date(first.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })},
                    latest {new Date(latest.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.
                  </>
                ) : null}
              </>
            ) : (
              <>
                {count === 1 ? "This single observation is the baseline." : `Only ${count} observations exist so far.`}{" "}
                SwitchOps needs at least {MIN_POINTS_FOR_TREND} before it will draw a trend, because
                two points cannot show a pattern.
              </>
            )}
          </p>
        </>
      )}
    </section>
  );
}

function Trend({
  observations,
  metric: metricKey,
  label,
  suffix,
  ceiling,
  enoughForTrend,
}: {
  observations: DeviceObservationPoint[];
  metric: MetricKey;
  label: string;
  suffix: string;
  ceiling: number;
  enoughForTrend: boolean;
}) {
  const points = observations.slice(-40);
  const latest = points[points.length - 1]?.[metricKey];

  return (
    <div className="trend">
      <div className="trend__head">
        <span>{label}</span>
        <strong>{formatValue(latest as number | null, metricKey, suffix)}</strong>
      </div>
      {enoughForTrend ? (
        <Sparkline points={points} metric={metricKey} ceiling={ceiling} suffix={suffix} label={label} />
      ) : (
        <ObservationDots points={points} metric={metricKey} suffix={suffix} label={label} />
      )}
    </div>
  );
}

/**
 * Discrete markers, not a chart. Used while there are too few observations for
 * a line to mean anything.
 */
function ObservationDots({
  points,
  metric,
  suffix,
  label,
}: {
  points: DeviceObservationPoint[];
  metric: MetricKey;
  suffix: string;
  label: string;
}) {
  return (
    <div className="trend__dots" aria-label={`${label}: ${points.length} recorded observation(s)`}>
      {points.map((point, index) => (
        <span
          key={`${point.timestamp}-${index}`}
          className={point[metric] == null ? "trend__dot trend__dot--empty" : "trend__dot"}
          title={`${new Date(point.timestamp).toLocaleString()}: ${formatValue(point[metric] as number | null, metric, suffix)}`}
        />
      ))}
      <span className="trend__dots-label">
        {points.length} observation{points.length === 1 ? "" : "s"}
      </span>
    </div>
  );
}

/** Time-aware sparkline: x is real elapsed time, and every sample is marked. */
function Sparkline({
  points,
  metric,
  ceiling,
  suffix,
  label,
}: {
  points: DeviceObservationPoint[];
  metric: MetricKey;
  ceiling: number;
  suffix: string;
  label: string;
}) {
  const times = points.map((point) => new Date(point.timestamp).getTime());
  const start = Math.min(...times);
  const end = Math.max(...times);
  const span = end - start || 1;
  const height = 34;
  const width = 100;

  const plotted = points
    .map((point, index) => {
      const value = point[metric];
      if (value == null) return null;
      return {
        x: ((times[index] - start) / span) * width,
        y: height - Math.max(0, Math.min(1, Number(value) / ceiling)) * (height - 4) - 2,
        point,
        value: Number(value),
      };
    })
    .filter((item): item is NonNullable<typeof item> => item !== null);

  if (!plotted.length) {
    return <div className="trend__dots" aria-label={`${label}: no values recorded`} />;
  }

  const path = plotted.map((item, index) => `${index ? "L" : "M"} ${item.x.toFixed(2)} ${item.y.toFixed(2)}`).join(" ");

  return (
    <svg
      className="trend__spark"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`${label} across ${points.length} observations, latest ${formatValue(plotted[plotted.length - 1].value, metric, suffix)}`}
    >
      <path className="trend__spark-line" d={path} vectorEffect="non-scaling-stroke" />
      {plotted.map((item, index) => (
        <circle
          key={`${item.point.timestamp}-${index}`}
          className="trend__spark-point"
          cx={item.x}
          cy={item.y}
          r="1.6"
          vectorEffect="non-scaling-stroke"
        >
          <title>{`${new Date(item.point.timestamp).toLocaleString()}: ${formatValue(item.value, metric, suffix)}`}</title>
        </circle>
      ))}
    </svg>
  );
}
