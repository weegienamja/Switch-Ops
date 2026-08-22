import { describe, expect, it } from "vitest";
import {
  buildSparkline,
  gapThreshold,
  medianInterval,
  normaliseObservations,
  type RawObservation,
} from "@/lib/sparkline";

const OPTIONS = { width: 240, height: 44, ceiling: 100 };
const BASE = Date.UTC(2026, 7, 22, 4, 0, 0);

function at(offsetMs: number, value: number | null): RawObservation {
  return { timestamp: new Date(BASE + offsetMs).toISOString(), value };
}

/** Every coordinate a path emits must be a real number. */
function pathNumbers(path: string): number[] {
  return (path.match(/-?\d+(\.\d+)?/g) || []).map(Number);
}

function allPaths(model: ReturnType<typeof buildSparkline>): string[] {
  return model.segments.map((segment) => segment.path).filter((p): p is string => p !== null);
}

describe("normalisation", () => {
  it("sorts out-of-order input so the line cannot cross itself", () => {
    const { points } = normaliseObservations([
      at(60_000, 10),
      at(0, 20),
      at(30_000, 30),
    ]);
    expect(points.map((p) => p.t - BASE)).toEqual([0, 30_000, 60_000]);
    expect(points.map((p) => p.v)).toEqual([20, 30, 10]);
  });

  it("drops observations with no value rather than plotting a hole", () => {
    const { points, dropped } = normaliseObservations([at(0, 5), at(1000, null), at(2000, 7)]);
    expect(points).toHaveLength(2);
    expect(dropped).toBe(1);
  });

  it("drops unparseable timestamps instead of producing NaN geometry", () => {
    const { points, dropped } = normaliseObservations([
      { timestamp: "not a date", value: 5 },
      at(0, 6),
    ]);
    expect(points).toHaveLength(1);
    expect(dropped).toBe(1);
  });

  it("collapses duplicate timestamps to the later value", () => {
    const { points, dropped } = normaliseObservations([at(0, 10), at(0, 40), at(1000, 20)]);
    expect(points).toHaveLength(2);
    expect(points[0].v).toBe(40);
    expect(dropped).toBe(1);
  });
});

describe("gap rule", () => {
  it("derives the threshold from the record's own rhythm", () => {
    const points = normaliseObservations(
      Array.from({ length: 10 }, (_, index) => at(index * 300_000, 10)),
    ).points;
    expect(medianInterval(points)).toBe(300_000);
    // 4 x median, inside the configured bounds.
    expect(gapThreshold(points, OPTIONS)).toBe(1_200_000);
  });

  it("never breaks below the floor however tight the sampling", () => {
    const points = normaliseObservations(
      Array.from({ length: 10 }, (_, index) => at(index * 1000, 10)),
    ).points;
    expect(gapThreshold(points, OPTIONS)).toBe(90_000);
  });

  it("always breaks beyond the ceiling however sparse the record", () => {
    const points = normaliseObservations([at(0, 1), at(6 * 3600_000, 2), at(12 * 3600_000, 3)]).points;
    expect(gapThreshold(points, OPTIONS)).toBe(1_800_000);
  });

  it("falls back to the ceiling when there is no interval to measure", () => {
    expect(medianInterval([])).toBe(0);
    expect(gapThreshold(normaliseObservations([at(0, 1)]).points, OPTIONS)).toBe(1_800_000);
  });
});

describe("segmentation", () => {
  it("breaks the line across a long silence instead of implying measurement", () => {
    // Three samples, twelve-hour hole, three more.
    const model = buildSparkline(
      [
        at(0, 10), at(300_000, 12), at(600_000, 11),
        at(12 * 3600_000, 40), at(12 * 3600_000 + 300_000, 42), at(12 * 3600_000 + 600_000, 41),
      ],
      OPTIONS,
    );
    expect(model.segments).toHaveLength(2);
    expect(allPaths(model)).toHaveLength(2);
    // Nothing spans the hole.
    for (const path of allPaths(model)) {
      expect(path.split("M")).toHaveLength(2);
    }
  });

  it("keeps evenly spaced observations as one unbroken line", () => {
    const model = buildSparkline(
      Array.from({ length: 40 }, (_, index) => at(index * 300_000, 20 + (index % 5))),
      OPTIONS,
    );
    expect(model.segments).toHaveLength(1);
    expect(allPaths(model)).toHaveLength(1);
  });

  it("reproduces the real lab's shape: dense clusters split by real gaps", () => {
    // Mirrors the recorded 24 h window: bursts of activity, long quiet spells.
    const raw: RawObservation[] = [];
    let offset = 0;
    for (let burst = 0; burst < 4; burst += 1) {
      for (let index = 0; index < 10; index += 1) {
        raw.push(at(offset, 10 + index));
        offset += 11_000; // 11 s apart, as observed
      }
      offset += 3 * 3600_000; // three-hour silence
    }
    const model = buildSparkline(raw, OPTIONS);
    expect(model.segments).toHaveLength(4);
    expect(model.usable).toBe(40);
  });
});

describe("dense point reduction", () => {
  it("never emits more markers than pixel columns", () => {
    const model = buildSparkline(
      Array.from({ length: 400 }, (_, index) => at(index * 1000, 50)),
      OPTIONS,
    );
    expect(model.buckets.length).toBeLessThanOrEqual(OPTIONS.width);
    expect(model.usable).toBe(400);
  });

  it("collapses a tight cluster to a handful of columns", () => {
    // 15 observations 11 s apart inside a 13 h span: sub-pixel spacing.
    const raw = [
      ...Array.from({ length: 15 }, (_, index) => at(index * 11_000, 30)),
      at(13 * 3600_000, 35),
    ];
    const model = buildSparkline(raw, OPTIONS);
    const firstSegment = model.segments[0];
    expect(firstSegment.buckets.length).toBeLessThan(5);
    expect(firstSegment.buckets[0].count).toBeGreaterThan(1);
  });

  it("preserves a spike hidden inside a dense column", () => {
    const raw = Array.from({ length: 30 }, (_, index) => at(index * 1000, index === 14 ? 95 : 10));
    const model = buildSparkline(raw, { ...OPTIONS, width: 10 });
    expect(model.spikes.length).toBeGreaterThan(0);
    const peak = Math.max(...model.buckets.map((bucket) => bucket.vMax));
    expect(peak).toBe(95);
  });
});

describe("geometry validity", () => {
  it("emits no NaN in any path", () => {
    const model = buildSparkline(
      [at(0, 10), at(60_000, 20), { timestamp: "bad", value: 5 }, at(120_000, null), at(180_000, 30)],
      OPTIONS,
    );
    for (const path of allPaths(model)) {
      expect(path).not.toContain("NaN");
      for (const value of pathNumbers(path)) expect(Number.isFinite(value)).toBe(true);
    }
  });

  it("keeps every coordinate inside the plot area", () => {
    const model = buildSparkline(
      Array.from({ length: 50 }, (_, index) => at(index * 60_000, index * 5)),
      OPTIONS,
    );
    for (const bucket of model.buckets) {
      expect(bucket.x).toBeGreaterThanOrEqual(0);
      expect(bucket.x).toBeLessThanOrEqual(OPTIONS.width);
      expect(bucket.yMin).toBeGreaterThanOrEqual(0);
      expect(bucket.yMax).toBeLessThanOrEqual(OPTIONS.height);
    }
  });

  it("clamps values above the ceiling rather than drawing off-chart", () => {
    const model = buildSparkline([at(0, 50), at(60_000, 500)], OPTIONS);
    for (const bucket of model.buckets) {
      expect(bucket.yMin).toBeGreaterThanOrEqual(0);
    }
  });

  it("handles a single observation without a path", () => {
    const model = buildSparkline([at(0, 42)], OPTIONS);
    expect(model.buckets).toHaveLength(1);
    expect(allPaths(model)).toHaveLength(0);
    // Centred, not jammed against the edge.
    expect(model.buckets[0].x).toBeCloseTo(OPTIONS.width / 2, 0);
  });

  it("handles two observations", () => {
    const model = buildSparkline([at(0, 10), at(60_000, 20)], OPTIONS);
    expect(allPaths(model)).toHaveLength(1);
    expect(model.buckets).toHaveLength(2);
  });

  it("handles an empty series", () => {
    const model = buildSparkline([], OPTIONS);
    expect(model.buckets).toEqual([]);
    expect(model.segments).toEqual([]);
  });

  it("handles every observation sharing one instant", () => {
    const model = buildSparkline([at(0, 10), at(0, 20), at(0, 30)], OPTIONS);
    expect(model.buckets).toHaveLength(1);
    for (const bucket of model.buckets) expect(Number.isFinite(bucket.x)).toBe(true);
  });

  it("draws a flat series as a flat line, not a jitter", () => {
    const model = buildSparkline(
      Array.from({ length: 20 }, (_, index) => at(index * 60_000, 25)),
      OPTIONS,
    );
    const ys = model.buckets.map((bucket) => bucket.yLast);
    expect(new Set(ys.map((y) => y.toFixed(4))).size).toBe(1);
    expect(model.spikes).toHaveLength(0);
  });
});

describe("responsive width", () => {
  it("recomputes geometry for a narrower container", () => {
    const raw = Array.from({ length: 60 }, (_, index) => at(index * 60_000, 30));
    const wide = buildSparkline(raw, { ...OPTIONS, width: 480 });
    const narrow = buildSparkline(raw, { ...OPTIONS, width: 120 });
    expect(narrow.buckets.length).toBeLessThanOrEqual(wide.buckets.length);
    for (const bucket of narrow.buckets) expect(bucket.x).toBeLessThanOrEqual(120);
  });

  it("survives a zero-width container without dividing by zero", () => {
    const model = buildSparkline([at(0, 10), at(60_000, 20)], { ...OPTIONS, width: 0 });
    for (const bucket of model.buckets) expect(Number.isFinite(bucket.x)).toBe(true);
  });
});
