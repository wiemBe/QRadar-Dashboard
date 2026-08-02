// Series construction: the point at which a collection outage either does or
// does not become an apparent source outage.

import { describe, expect, it } from "vitest";

import { buildSeries, kindMeaning, unobservedIntervals } from "./timeseries";
import type { MetricBucket } from "./api";

function bucket(over: Partial<MetricBucket> = {}): MetricBucket {
  return {
    bucket_start: "2026-07-20T10:00:00Z",
    bucket_seconds: 300,
    event_count: 600,
    average_eps: 2,
    peak_eps: 2.4,
    completeness: "COMPLETE",
    last_event_at: null,
    ...over,
  };
}

const T0 = Date.parse("2026-07-20T10:00:00Z");
const FIVE_MIN = 300_000;

describe("buildSeries", () => {
  it("plots a fully observed bucket", () => {
    const [p] = buildSeries([bucket({ average_eps: 2.5 })]);
    expect(p.observed).toBe(2.5);
    expect(p.kind).toBe("COMPLETE");
  });

  it("plots a genuine zero as zero", () => {
    // A source that was observed sending nothing is a real measurement, and a
    // real finding. It must not be nulled out along with the unobserved ones.
    const [p] = buildSeries([bucket({ average_eps: 0, event_count: 0 })]);
    expect(p.observed).toBe(0);
  });

  it("does not plot a partial bucket's value as an observation", () => {
    const [p] = buildSeries([bucket({ completeness: "PARTIAL", average_eps: 0.4 })]);
    expect(p.observed).toBeNull();
    // The reported number survives for the tooltip, off the line.
    expect(p.reported).toBe(0.4);
    expect(p.kind).toBe("PARTIAL");
  });

  it("does not plot a missing bucket's value as an observation", () => {
    const [p] = buildSeries([bucket({ completeness: "MISSING", average_eps: 0 })]);
    expect(p.observed).toBeNull();
    expect(p.kind).toBe("MISSING");
  });

  it("inserts an explicit null where no bucket was stored at all", () => {
    // 10:00 then 11:00 with a 5-minute bucket width: an hour of history simply
    // is not there, and joining the two points would draw traffic nobody saw.
    const points = buildSeries([
      bucket({ bucket_start: "2026-07-20T10:00:00Z" }),
      bucket({ bucket_start: "2026-07-20T11:00:00Z" }),
    ]);
    expect(points).toHaveLength(3);
    expect(points[1].kind).toBe("GAP");
    expect(points[1].observed).toBeNull();
    expect(points[1].t).toBe(T0 + FIVE_MIN);
  });

  it("does not invent a gap between consecutive buckets", () => {
    const points = buildSeries([
      bucket({ bucket_start: "2026-07-20T10:00:00Z" }),
      bucket({ bucket_start: "2026-07-20T10:05:00Z" }),
    ]);
    expect(points).toHaveLength(2);
    expect(points.some((p) => p.kind === "GAP")).toBe(false);
  });

  it("sorts defensively so one out-of-order row cannot fabricate a gap", () => {
    const points = buildSeries([
      bucket({ bucket_start: "2026-07-20T10:05:00Z" }),
      bucket({ bucket_start: "2026-07-20T10:00:00Z" }),
    ]);
    expect(points).toHaveLength(2);
    expect(points[0].t).toBeLessThan(points[1].t);
  });

  it("returns nothing for no buckets rather than a zero point", () => {
    expect(buildSeries([])).toEqual([]);
  });
});

describe("unobservedIntervals", () => {
  it("shades a partial bucket", () => {
    const areas = unobservedIntervals([bucket({ completeness: "PARTIAL" })]);
    expect(areas).toEqual([{ start: T0, end: T0 + FIVE_MIN, kind: "PARTIAL" }]);
  });

  it("shades a missing bucket", () => {
    const areas = unobservedIntervals([bucket({ completeness: "MISSING" })]);
    expect(areas[0].kind).toBe("MISSING");
  });

  it("shades the span where no bucket exists", () => {
    const areas = unobservedIntervals([
      bucket({ bucket_start: "2026-07-20T10:00:00Z" }),
      bucket({ bucket_start: "2026-07-20T11:00:00Z" }),
    ]);
    expect(areas).toHaveLength(1);
    expect(areas[0]).toEqual({
      start: T0 + FIVE_MIN,
      end: Date.parse("2026-07-20T11:00:00Z"),
      kind: "GAP",
    });
  });

  it("shades nothing when every interval was fully observed", () => {
    expect(
      unobservedIntervals([
        bucket({ bucket_start: "2026-07-20T10:00:00Z" }),
        bucket({ bucket_start: "2026-07-20T10:05:00Z" }),
      ]),
    ).toEqual([]);
  });
});

describe("kindMeaning", () => {
  it("explains each kind of unobserved interval", () => {
    expect(kindMeaning("PARTIAL")).toMatch(/undercount/i);
    expect(kindMeaning("MISSING")).toMatch(/not evidence of silence/i);
    expect(kindMeaning("GAP")).toMatch(/no bucket/i);
    expect(kindMeaning("COMPLETE")).toMatch(/whole interval/i);
  });
});
