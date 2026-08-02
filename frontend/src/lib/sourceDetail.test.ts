import { describe, expect, it } from "vitest";

import type { AnomalySummary, BaselineCell, MetricBucket, SourceBehavior } from "./api";
import {
  DEFAULT_RANGE,
  baselineQuality,
  collectionHealth,
  currentCell,
  isActiveAnomaly,
  parseRange,
  partitionAnomalies,
  rangeLabel,
  summarizeSourceTimeline,
} from "./sourceDetail";

function source(over: Partial<SourceBehavior> = {}): SourceBehavior {
  return {
    log_source_id: "s-1",
    name: "LAB Firewall",
    criticality: "HIGH",
    observed_eps: 6,
    expected_eps: 2,
    expected_low: 1.7,
    expected_high: 2.3,
    deviation_ratio: 3,
    state: "OPEN",
    baseline_sample_count: 12,
    baseline_completeness: 0.9,
    last_bucket_at: "2026-07-20T10:00:00Z",
    last_event_at: "2026-07-20T10:04:00Z",
    open_anomaly_count: 1,
    ...over,
  };
}

function cell(over: Partial<BaselineCell> = {}): BaselineCell {
  return {
    metric_name: "average_eps",
    weekday: 7,
    hour: 18,
    median: 2,
    mad: 0.5,
    p05: 1.7,
    p95: 2.3,
    sample_count: 12,
    is_reliable: true,
    completeness: 0.9,
    baseline_version: 3,
    computed_at: "2026-07-20T09:00:00Z",
    ...over,
  };
}

function bucket(over: Partial<MetricBucket> = {}): MetricBucket {
  return {
    bucket_start: "2026-07-20T10:00:00Z",
    bucket_seconds: 60,
    event_count: 360,
    average_eps: 6,
    peak_eps: 7,
    completeness: "COMPLETE",
    last_event_at: "2026-07-20T10:00:59Z",
    ...over,
  };
}

function anomaly(over: Partial<AnomalySummary> = {}): AnomalySummary {
  return {
    id: "a-1",
    log_source_id: "s-1",
    log_source_name: "LAB Firewall",
    anomaly_type: "VOLUME_SPIKE",
    state: "OPEN",
    severity: "HIGH",
    observed_value: 6,
    expected_value: 2,
    deviation_ratio: 3,
    robust_z: 13.5,
    absolute_delta: 1200,
    consecutive_buckets: 2,
    confidence: 0.82,
    detected_at: "2026-07-20T10:05:00Z",
    opened_at: "2026-07-20T10:05:00Z",
    anomaly_start: "2026-07-20T10:00:00Z",
    anomaly_end: null,
    resolved_at: null,
    duration_seconds: null,
    evidence_status: "COMPLETE",
    suppressed: false,
    explanation: null,
    ...over,
  };
}

/** A window with every interval present, for the "current" cases. */
function fullWindow(hours: number, now = Date.parse("2026-07-20T10:00:00Z")) {
  const count = (hours * 3600) / 60;
  return Array.from({ length: count }, (_, i) =>
    bucket({ bucket_start: new Date(now - (count - 1 - i) * 60_000).toISOString() }),
  );
}

describe("parseRange", () => {
  it("accepts the four bounded windows", () => {
    for (const h of [1, 6, 12, 24]) {
      expect(parseRange(String(h))).toBe(h);
    }
  });

  it("falls back to the default on anything else", () => {
    // The value arrives from a URL a third party may have written.
    expect(parseRange(undefined)).toBe(DEFAULT_RANGE);
    expect(parseRange("9999")).toBe(DEFAULT_RANGE);
    expect(parseRange("-1")).toBe(DEFAULT_RANGE);
    expect(parseRange("'; DROP TABLE")).toBe(DEFAULT_RANGE);
    expect(parseRange("6.5")).toBe(6); // parseInt truncates to a known value
  });

  it("labels the windows in words", () => {
    expect(rangeLabel(1)).toBe("1 hour");
    expect(rangeLabel(24)).toBe("24 hours");
  });
});

describe("currentCell", () => {
  it("matches the cell the expectation came from", () => {
    const cells = [cell({ hour: 18, median: 2 }), cell({ hour: 20, median: 5 })];
    expect(currentCell(cells, 5)?.hour).toBe(20);
  });

  it("matches nothing when there is no expectation", () => {
    expect(currentCell([cell()], null)).toBeNull();
  });

  it("matches nothing rather than the nearest cell", () => {
    // Guessing a cell would attach the wrong sample count and MAD to the
    // quality verdict.
    expect(currentCell([cell({ median: 2 })], 4)).toBeNull();
  });
});

describe("baselineQuality", () => {
  it("calls a matched, reliable, varying baseline reliable", () => {
    const q = baselineQuality(source(), [cell({ sample_count: 8 })]);
    expect(q.status).toBe("RELIABLE");
    expect(q.explanation).toContain("8 complete samples");
  });

  it("calls an unbaselined source still learning and never healthy", () => {
    const q = baselineQuality(
      source({ state: "INSUFFICIENT_DATA", expected_eps: null }),
      [],
    );
    expect(q.status).toBe("STILL_LEARNING");
    expect(q.tone).toBe("");
    expect(q.explanation).toContain("not the same as being healthy");
  });

  it("treats a null expectation as still learning even outside that state", () => {
    const q = baselineQuality(source({ state: "OPEN", expected_eps: null }), []);
    expect(q.status).toBe("STILL_LEARNING");
  });

  it("explains a zero MAD as a property, not as an error", () => {
    const q = baselineQuality(source(), [cell({ mad: 0, sample_count: 9 })]);
    expect(q.status).toBe("DEGENERATE");
    expect(q.explanation).toContain("normal for a steady low-volume source");
    expect(q.explanation).toContain("deterministic expected-band test");
  });

  it("flags a cell the backend has not marked reliable", () => {
    const q = baselineQuality(source(), [cell({ is_reliable: false })]);
    expect(q.status).toBe("INCOMPLETE");
  });
});

describe("collectionHealth", () => {
  const now = Date.parse("2026-07-20T10:00:30Z");

  it("calls a complete window current", () => {
    const h = collectionHealth(fullWindow(1), "2026-07-20T10:00:00Z", 3600, now);
    expect(h.status).toBe("CURRENT");
    expect(h.absent).toBe(0);
  });

  it("counts intervals with no stored bucket as uncollected", () => {
    // The chart shades these as gaps; reporting "1 of 1 fully observed" beside
    // it would be true of the rows and false of the window.
    const h = collectionHealth([bucket()], "2026-07-20T10:00:00Z", 3600, now);
    expect(h.status).toBe("PARTIAL");
    expect(h.expected).toBe(60);
    expect(h.absent).toBe(59);
    expect(h.explanation).toContain("no stored bucket at all");
  });

  it("counts partially collected intervals separately", () => {
    const window = fullWindow(1);
    window[0] = { ...window[0], completeness: "PARTIAL" };
    const h = collectionHealth(window, "2026-07-20T10:00:00Z", 3600, now);
    expect(h.status).toBe("PARTIAL");
    expect(h.incomplete).toBe(1);
    expect(h.absent).toBe(0);
  });

  it("calls a stale watermark delayed without calling the source silent", () => {
    const h = collectionHealth(fullWindow(1), "2026-07-20T09:00:00Z", 3600, now);
    expect(h.status).toBe("DELAYED");
    expect(h.explanation).toContain("says nothing about whether it is sending events");
  });

  it("reports no collection as an absence, not as zero traffic", () => {
    const h = collectionHealth([], null, 3600, now);
    expect(h.status).toBe("NONE");
    expect(h.explanation).toContain("not an observation that the source is silent");
  });

  it("never reports a collection failure it has not observed", () => {
    // The API exposes no failure counter, so no status may claim one.
    const statuses = [
      collectionHealth([], null, 3600, now).status,
      collectionHealth([bucket()], "2026-07-20T10:00:00Z", 3600, now).status,
      collectionHealth(fullWindow(1), "2026-01-01T00:00:00Z", 3600, now).status,
    ];
    expect(statuses).not.toContain("FAILED");
  });
});

describe("partitionAnomalies", () => {
  it("derives active from the lifecycle state", () => {
    expect(isActiveAnomaly(anomaly({ state: "OPEN" }))).toBe(true);
    expect(isActiveAnomaly(anomaly({ state: "CANDIDATE" }))).toBe(true);
    expect(isActiveAnomaly(anomaly({ state: "RECOVERING" }))).toBe(true);
    expect(isActiveAnomaly(anomaly({ state: "RESOLVED" }))).toBe(false);
  });

  it("does not treat NORMAL with no resolved timestamp as active", () => {
    // The defect the backend corrected in ec5d352: an active incident is
    // defined by lifecycle state, never by `resolved_at IS NULL`.
    const a = anomaly({ state: "NORMAL", resolved_at: null });
    expect(isActiveAnomaly(a)).toBe(false);
    expect(partitionAnomalies([a]).active).toHaveLength(0);
    expect(partitionAnomalies([a]).recent).toHaveLength(1);
  });

  it("orders active by severity of lifecycle state", () => {
    const { active } = partitionAnomalies([
      anomaly({ id: "r", state: "RECOVERING" }),
      anomaly({ id: "c", state: "CANDIDATE" }),
      anomaly({ id: "o", state: "OPEN" }),
    ]);
    expect(active.map((a) => a.id)).toEqual(["o", "c", "r"]);
  });

  it("orders closed anomalies most recently closed first", () => {
    const { recent } = partitionAnomalies([
      anomaly({ id: "old", state: "RESOLVED", resolved_at: "2026-07-20T09:00:00Z" }),
      anomaly({ id: "new", state: "RESOLVED", resolved_at: "2026-07-20T11:00:00Z" }),
    ]);
    expect(recent.map((a) => a.id)).toEqual(["new", "old"]);
  });
});

describe("summarizeSourceTimeline", () => {
  const base = {
    hours: 6,
    observed: [2, 6, 4],
    currentEps: 4,
    expectedEps: 2,
    missing: 0,
    partial: 0,
    anomalyCount: 0,
  };

  it("states the window, the range, the current value and the baseline", () => {
    const text = summarizeSourceTimeline(base);
    expect(text).toContain("last 6 hours");
    expect(text).toContain("2.00 to 6.00 EPS");
    expect(text).toContain("most recent observation is 4.00 EPS");
    expect(text).toContain("expected baseline is 2.00 EPS");
    expect(text).toContain("No anomalies were detected");
  });

  it("says a still-learning source has no expectation", () => {
    expect(summarizeSourceTimeline({ ...base, expectedEps: null })).toContain(
      "still learning",
    );
  });

  it("describes uncollected intervals as gaps rather than zero traffic", () => {
    const text = summarizeSourceTimeline({ ...base, missing: 2, partial: 1 });
    expect(text).toContain("2 intervals not collected");
    expect(text).toContain("1 interval only partly collected");
    expect(text).toContain("rather than as zero traffic");
  });

  it("says so when nothing in the window was observed", () => {
    expect(
      summarizeSourceTimeline({ ...base, observed: [null, null], currentEps: null }),
    ).toContain("no observed series to describe");
  });

  it("counts anomalies in the window", () => {
    expect(summarizeSourceTimeline({ ...base, anomalyCount: 1 })).toContain(
      "1 anomaly was detected",
    );
    expect(summarizeSourceTimeline({ ...base, anomalyCount: 3 })).toContain(
      "3 anomalies were detected",
    );
  });
});
