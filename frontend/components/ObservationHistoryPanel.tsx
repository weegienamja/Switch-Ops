"use client";

import { useEffect, useRef, useState } from "react";
import type { DeviceObservationPoint, TelemetryHistoryResponse } from "@/lib/types";
import { buildSparkline } from "@/lib/sparkline";

/**
 * Measure the rendered width so the chart can work in real pixels.
 *
 * The previous chart used a fixed 100-unit viewBox stretched to the container
 * with preserveAspectRatio="none", which scaled x and y by different factors
 * and turned every circular marker into an ellipse.
 */
function useMeasuredWidth(fallback = 240): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const measure = () => {
      const next = element.clientWidth;
      if (next > 0) setWidth(next);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

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
 * Retained observation history from the slow background cadence and deep refreshes.
 *
 * A row exists only when a health observation was retained. The wording and
 * the chart both have to say that, otherwise
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
            SwitchOps retains health observations on a slow background cadence. Fast live port
            updates stay in memory, so this chart remains readable rather than storing every tick.
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

/**
 * Time-aware sparkline.
 *
 * Geometry comes from lib/sparkline, which sorts, de-duplicates, breaks the
 * line across real gaps in observation and reduces dense clusters to one
 * column per pixel while keeping the extremes. Everything here is rendering.
 */
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
  const [ref, width] = useMeasuredWidth();
  const height = 44;
  const model = buildSparkline(
    points.map((point) => ({ timestamp: point.timestamp, value: point[metric] as number | null })),
    { width, height, ceiling },
  );

  if (!model.buckets.length) {
    return (
      <div className="trend__dots" ref={ref} aria-label={`${label}: no values recorded`}>
        <span className="trend__dots-label">no values recorded</span>
      </div>
    );
  }

  const latest = model.buckets[model.buckets.length - 1];
  const gapCount = Math.max(0, model.segments.length - 1);

  return (
    <div className="trend__spark-wrap" ref={ref}>
      <svg
        className="trend__spark"
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={
          `${label} across ${model.usable} observation${model.usable === 1 ? "" : "s"}` +
          (gapCount ? `, in ${model.segments.length} runs separated by gaps in observation` : "") +
          `, latest ${formatValue(latest.vLast, metric, suffix)}`
        }
      >
        {/* Spike extents first, so the line sits on top of them. */}
        {model.spikes.map((bucket) => (
          <line
            key={`spike-${bucket.x}`}
            className="trend__spark-spike"
            x1={bucket.x}
            x2={bucket.x}
            y1={bucket.yMin}
            y2={bucket.yMax}
          />
        ))}
        {model.segments.map((segment, index) =>
          segment.path ? (
            <path key={`seg-${index}`} className="trend__spark-line" d={segment.path} />
          ) : null,
        )}
        {model.buckets.map((bucket) => (
          <circle
            key={`pt-${bucket.x}`}
            className="trend__spark-point"
            cx={bucket.x}
            cy={bucket.yLast}
            r={2}
          >
            <title>
              {bucket.count > 1
                ? `${new Date(bucket.tFirst).toLocaleString()} – ${new Date(bucket.tLast).toLocaleTimeString()}: ` +
                  `${bucket.count} observations, ${formatValue(bucket.vMin, metric, suffix)} to ${formatValue(bucket.vMax, metric, suffix)}`
                : `${new Date(bucket.tLast).toLocaleString()}: ${formatValue(bucket.vLast, metric, suffix)}`}
            </title>
          </circle>
        ))}
      </svg>
      {gapCount ? (
        <span className="trend__spark-gaps">
          {gapCount} gap{gapCount === 1 ? "" : "s"} in observation
        </span>
      ) : null}
    </div>
  );
}
