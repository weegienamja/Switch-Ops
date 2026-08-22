/**
 * Sparkline geometry for refresh-driven observations.
 *
 * This is deliberately a pure module with no DOM: the chart's correctness is
 * arithmetic, and arithmetic can be tested without rendering anything.
 *
 * Three properties the v0.3 chart got wrong, all of which this module fixes:
 *
 *  1. It drew one continuous path across every gap, so a six-hour hole in the
 *     record looked like six hours of flat measurement. Gaps are now breaks.
 *  2. It plotted every raw point, so fifteen observations eleven seconds apart
 *     landed inside one pixel as a pile of overlapping markers. Points are now
 *     reduced per pixel column, keeping the extremes so spikes survive.
 *  3. It worked in a 100x34 viewBox stretched to the container with
 *     `preserveAspectRatio="none"`, which turned every circular marker into an
 *     ellipse. Geometry is now computed in real pixels.
 */

export interface RawObservation {
  timestamp: string;
  value: number | null | undefined;
}

/** One pixel column's worth of observations. */
export interface SparkBucket {
  x: number;
  /** Screen coordinates: yMin is the *top* of the spike (highest value). */
  yMin: number;
  yMax: number;
  yLast: number;
  vMin: number;
  vMax: number;
  vLast: number;
  tFirst: number;
  tLast: number;
  count: number;
}

export interface SparkSegment {
  buckets: SparkBucket[];
  /** Path across this segment, or null when the segment is a single point. */
  path: string | null;
}

export interface SparklineModel {
  segments: SparkSegment[];
  /** Buckets whose column held more than one distinct value. */
  spikes: SparkBucket[];
  /** Every bucket, for markers and hit targets. */
  buckets: SparkBucket[];
  width: number;
  height: number;
  /** Interval beyond which the line is broken, in milliseconds. */
  gapMs: number;
  /** Observations accepted after validation. */
  usable: number;
  /** Observations rejected as unusable (bad timestamp or no value). */
  dropped: number;
  tStart: number;
  tEnd: number;
}

export interface SparklineOptions {
  width: number;
  height: number;
  /** Value mapped to the top of the plot. */
  ceiling: number;
  /** Vertical breathing room, in pixels. */
  padding?: number;
  /** Break the line beyond this multiple of the median interval. */
  gapFactor?: number;
  /** Never break below this, however tight the median. */
  minGapMs?: number;
  /** Always break beyond this, however sparse the record. */
  maxGapMs?: number;
}

interface CleanPoint {
  t: number;
  v: number;
}

const DEFAULT_GAP_FACTOR = 4;
const DEFAULT_MIN_GAP_MS = 90_000; // 90 s
const DEFAULT_MAX_GAP_MS = 1_800_000; // 30 min

/**
 * Sort, validate and de-duplicate. Input order is not trusted: an unsorted
 * series drawn as-is produces a path that crosses back over itself, which is
 * one of the ways the old chart looked "mangled".
 */
export function normaliseObservations(raw: RawObservation[]): {
  points: CleanPoint[];
  dropped: number;
} {
  const clean: CleanPoint[] = [];
  let dropped = 0;
  for (const item of raw) {
    const t = new Date(item.timestamp).getTime();
    const v = item.value;
    if (!Number.isFinite(t) || v == null || !Number.isFinite(Number(v))) {
      dropped += 1;
      continue;
    }
    clean.push({ t, v: Number(v) });
  }
  clean.sort((a, b) => a.t - b.t);

  // Two observations sharing a timestamp would occupy the same x forever; keep
  // the later value so the series stays a function of time.
  const deduped: CleanPoint[] = [];
  for (const point of clean) {
    const previous = deduped[deduped.length - 1];
    if (previous && previous.t === point.t) {
      deduped[deduped.length - 1] = point;
      dropped += 1;
      continue;
    }
    deduped.push(point);
  }
  return { points: deduped, dropped };
}

/** Median of the positive intervals between consecutive observations. */
export function medianInterval(points: CleanPoint[]): number {
  if (points.length < 2) return 0;
  const gaps: number[] = [];
  for (let index = 1; index < points.length; index += 1) {
    const delta = points[index].t - points[index - 1].t;
    if (delta > 0) gaps.push(delta);
  }
  if (!gaps.length) return 0;
  gaps.sort((a, b) => a - b);
  const middle = Math.floor(gaps.length / 2);
  return gaps.length % 2 ? gaps[middle] : (gaps[middle - 1] + gaps[middle]) / 2;
}

/**
 * How long a silence has to be before the line is broken.
 *
 * Derived from the record's own rhythm rather than a fixed constant, because
 * SwitchOps is refresh-driven: a busy afternoon and an idle night have very
 * different natural intervals and both are legitimate.
 */
export function gapThreshold(points: CleanPoint[], options: SparklineOptions): number {
  const factor = options.gapFactor ?? DEFAULT_GAP_FACTOR;
  const minimum = options.minGapMs ?? DEFAULT_MIN_GAP_MS;
  const maximum = options.maxGapMs ?? DEFAULT_MAX_GAP_MS;
  const median = medianInterval(points);
  if (!median) return maximum;
  return Math.min(maximum, Math.max(minimum, median * factor));
}

export function buildSparkline(
  raw: RawObservation[],
  options: SparklineOptions,
): SparklineModel {
  const width = Math.max(1, Math.floor(options.width));
  const height = Math.max(1, Math.floor(options.height));
  const padding = options.padding ?? 3;
  const ceiling = options.ceiling > 0 ? options.ceiling : 1;

  const { points, dropped } = normaliseObservations(raw);
  const empty: SparklineModel = {
    segments: [],
    spikes: [],
    buckets: [],
    width,
    height,
    gapMs: 0,
    usable: points.length,
    dropped,
    tStart: 0,
    tEnd: 0,
  };
  if (!points.length) return empty;

  const tStart = points[0].t;
  const tEnd = points[points.length - 1].t;
  const span = tEnd - tStart;

  const usableHeight = Math.max(1, height - padding * 2);
  const yOf = (value: number) => {
    const ratio = Math.max(0, Math.min(1, value / ceiling));
    return height - padding - ratio * usableHeight;
  };
  // A single observation, or several at one instant, sits mid-plot rather
  // than hard against the left edge where it reads as a rendering fault.
  const xOf = (t: number) => (span <= 0 ? width / 2 : ((t - tStart) / span) * (width - 1));

  const gapMs = gapThreshold(points, options);

  // Split into runs of observations that are close enough in time to join.
  const runs: CleanPoint[][] = [];
  let current: CleanPoint[] = [points[0]];
  for (let index = 1; index < points.length; index += 1) {
    if (points[index].t - points[index - 1].t > gapMs) {
      runs.push(current);
      current = [];
    }
    current.push(points[index]);
  }
  runs.push(current);

  const segments: SparkSegment[] = [];
  const allBuckets: SparkBucket[] = [];
  const spikes: SparkBucket[] = [];

  for (const run of runs) {
    // Reduce to at most one bucket per pixel column. Keeping min and max means
    // a spike between two samples is never averaged away.
    const byColumn = new Map<number, SparkBucket>();
    for (const point of run) {
      const x = xOf(point.t);
      const column = Math.round(x);
      const existing = byColumn.get(column);
      const y = yOf(point.v);
      if (!existing) {
        byColumn.set(column, {
          x: column,
          yMin: y,
          yMax: y,
          yLast: y,
          vMin: point.v,
          vMax: point.v,
          vLast: point.v,
          tFirst: point.t,
          tLast: point.t,
          count: 1,
        });
        continue;
      }
      existing.yMin = Math.min(existing.yMin, y);
      existing.yMax = Math.max(existing.yMax, y);
      existing.yLast = y;
      existing.vMin = Math.min(existing.vMin, point.v);
      existing.vMax = Math.max(existing.vMax, point.v);
      existing.vLast = point.v;
      existing.tLast = point.t;
      existing.count += 1;
    }

    const buckets = Array.from(byColumn.values()).sort((a, b) => a.x - b.x);
    allBuckets.push(...buckets);
    for (const bucket of buckets) {
      if (bucket.vMin !== bucket.vMax) spikes.push(bucket);
    }

    const path =
      buckets.length > 1
        ? buckets
            .map(
              (bucket, index) =>
                `${index ? "L" : "M"} ${bucket.x.toFixed(2)} ${bucket.yLast.toFixed(2)}`,
            )
            .join(" ")
        : null;
    segments.push({ buckets, path });
  }

  return {
    segments,
    spikes,
    buckets: allBuckets,
    width,
    height,
    gapMs,
    usable: points.length,
    dropped,
    tStart,
    tEnd,
  };
}
