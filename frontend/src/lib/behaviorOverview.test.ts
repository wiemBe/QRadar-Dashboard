import { describe, expect, it } from "vitest";

import {
  buildAttentionRows,
  countHighDeviation,
  deviationMagnitude,
  hasMeasurableDeviation,
  healthDistribution,
  healthSummaryText,
  isHighDeviation,
  PRIORITY,
  severityRank,
} from "./behaviorOverview";
import type { AnomalySummary, SourceBehavior } from "./api";

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
    state: "NORMAL",
    baseline_sample_count: 30,
    baseline_completeness: 0.9,
    last_bucket_at: "2026-07-20T10:00:00Z",
    last_event_at: "2026-07-20T10:04:00Z",
    open_anomaly_count: 0,
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

// The high-deviation rule, clause by clause. Each exclusion below is a case
// where a number exists but does not mean what a reader would take it to mean,
// and counting it would report a measurement the platform never made.
describe("hasMeasurableDeviation", () => {
  it("accepts a source with a real observation and a real baseline", () => {
    expect(hasMeasurableDeviation(source())).toBe(true);
  });

  it("rejects an unbaselined source, which has no expectation to deviate from", () => {
    expect(
      hasMeasurableDeviation(source({ state: "INSUFFICIENT_DATA" })),
    ).toBe(false);
  });

  it("rejects a null observed value — not measured is not zero", () => {
    expect(hasMeasurableDeviation(source({ observed_eps: null }))).toBe(false);
  });

  it("rejects a null expected value", () => {
    expect(hasMeasurableDeviation(source({ expected_eps: null }))).toBe(false);
  });

  it("rejects a null ratio, which the backend returns when expected is zero", () => {
    expect(hasMeasurableDeviation(source({ deviation_ratio: null }))).toBe(false);
  });

  it("rejects a quiet source matching a quiet baseline", () => {
    expect(
      hasMeasurableDeviation(
        source({ observed_eps: 0, expected_eps: 0, deviation_ratio: 0 }),
      ),
    ).toBe(false);
  });

  it("rejects a source with no collected bucket", () => {
    expect(hasMeasurableDeviation(source({ last_bucket_at: null }))).toBe(false);
  });
});

describe("isHighDeviation", () => {
  it("counts a source at or above 2x expected", () => {
    expect(isHighDeviation(source({ deviation_ratio: 2 }))).toBe(true);
    expect(isHighDeviation(source({ deviation_ratio: 3 }))).toBe(true);
  });

  it("counts a drop as symmetrically as a spike", () => {
    expect(isHighDeviation(source({ deviation_ratio: 0.5 }))).toBe(true);
    expect(isHighDeviation(source({ deviation_ratio: 0.1 }))).toBe(true);
  });

  it("leaves a source inside the band alone", () => {
    expect(isHighDeviation(source({ deviation_ratio: 1.4 }))).toBe(false);
    expect(isHighDeviation(source({ deviation_ratio: 0.8 }))).toBe(false);
  });

  it("never counts a source it cannot measure", () => {
    expect(
      isHighDeviation(source({ state: "INSUFFICIENT_DATA", deviation_ratio: 9 })),
    ).toBe(false);
    expect(isHighDeviation(source({ deviation_ratio: null }))).toBe(false);
  });

  it("counts across a fleet without counting the unmeasurable", () => {
    expect(
      countHighDeviation([
        source({ deviation_ratio: 3 }),
        source({ deviation_ratio: 1 }),
        source({ state: "INSUFFICIENT_DATA" }),
        source({ observed_eps: 0, expected_eps: 0, deviation_ratio: 0 }),
      ]),
    ).toBe(1);
  });
});

describe("deviationMagnitude", () => {
  it("folds a drop onto the same scale as the equivalent spike", () => {
    expect(deviationMagnitude(4)).toBe(deviationMagnitude(0.25));
  });

  it("ranks a total stop above every finite deviation", () => {
    expect(deviationMagnitude(0)).toBe(Number.POSITIVE_INFINITY);
  });

  it("treats an absent ratio as no measurement rather than a small one", () => {
    expect(deviationMagnitude(null)).toBe(0);
  });
});

describe("severityRank", () => {
  it("orders the severities and treats an absent one as lowest", () => {
    expect(severityRank("CRITICAL")).toBeGreaterThan(severityRank("HIGH"));
    expect(severityRank("HIGH")).toBeGreaterThan(severityRank("LOW"));
    expect(severityRank(null)).toBe(0);
    expect(severityRank("NONSENSE")).toBe(0);
  });
});

describe("buildAttentionRows", () => {
  it("never lists an ordinary normal source", () => {
    const rows = buildAttentionRows(
      [source({ state: "NORMAL", deviation_ratio: 1.1 })],
      [],
    );
    expect(rows).toHaveLength(0);
  });

  it("orders open before candidate before recovering", () => {
    const rows = buildAttentionRows(
      [],
      [
        anomaly({ id: "r", state: "RECOVERING", log_source_id: "s-3" }),
        anomaly({ id: "c", state: "CANDIDATE", log_source_id: "s-2" }),
        anomaly({ id: "o", state: "OPEN", log_source_id: "s-1" }),
      ],
    );
    expect(rows.map((r) => r.key)).toEqual([
      "anomaly:o",
      "anomaly:c",
      "anomaly:r",
    ]);
  });

  it("ranks a confirmed no-events outage by its state, not its detector", () => {
    // An OPEN no-events incident is a confirmed outage. Ranking it below a
    // candidate volume spike because of its detector would bury the worse
    // finding.
    const rows = buildAttentionRows(
      [],
      [
        anomaly({ id: "cand", state: "CANDIDATE", log_source_id: "s-2" }),
        anomaly({
          id: "out",
          state: "OPEN",
          anomaly_type: "NO_EVENTS",
          log_source_id: "s-1",
        }),
      ],
    );
    expect(rows[0].key).toBe("anomaly:out");
    expect(rows[0].priority).toBe(PRIORITY.OPEN);
    expect(rows[0].issue).toBe("No events received");
  });

  it("breaks a priority tie by severity, then by deviation", () => {
    const rows = buildAttentionRows(
      [],
      [
        anomaly({ id: "med", severity: "MEDIUM", log_source_id: "s-1" }),
        anomaly({ id: "crit", severity: "CRITICAL", log_source_id: "s-2" }),
        anomaly({ id: "high", severity: "HIGH", log_source_id: "s-3" }),
      ],
    );
    expect(rows.map((r) => r.key)).toEqual([
      "anomaly:crit",
      "anomaly:high",
      "anomaly:med",
    ]);
  });

  it("adds a materially deviating source that has not tripped a detector", () => {
    const rows = buildAttentionRows(
      [source({ log_source_id: "s-9", deviation_ratio: 4, state: "NORMAL" })],
      [],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].issue).toBe("Deviating from baseline");
    expect(rows[0].href).toBe("/behavior/sources/s-9");
  });

  it("lists an unbaselined source last, and without inventing an expectation", () => {
    const rows = buildAttentionRows(
      [
        source({
          log_source_id: "s-8",
          state: "INSUFFICIENT_DATA",
          expected_eps: null,
          deviation_ratio: null,
        }),
        source({ log_source_id: "s-9", deviation_ratio: 4 }),
      ],
      [],
    );
    const unbaselined = rows[rows.length - 1];
    expect(unbaselined.priority).toBe(PRIORITY.INSUFFICIENT_DATA);
    expect(unbaselined.issue).toBe("No adequate baseline");
    // Not zero: there is no expectation, and a zero would be one.
    expect(unbaselined.expected).toBeNull();
  });

  it("does not list a source twice when it already has an anomaly", () => {
    const rows = buildAttentionRows(
      [source({ log_source_id: "s-1", deviation_ratio: 4 })],
      [anomaly({ log_source_id: "s-1" })],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].key).toBe("anomaly:a-1");
  });

  it("ignores resolved anomalies, which are history rather than work", () => {
    const rows = buildAttentionRows(
      [],
      [anomaly({ state: "RESOLVED", deviation_ratio: 9 })],
    );
    expect(rows).toHaveLength(0);
  });
});

describe("healthDistribution", () => {
  it("keeps unbaselined sources out of the normal group", () => {
    const groups = healthDistribution([
      source({ state: "NORMAL" }),
      source({ state: "INSUFFICIENT_DATA" }),
    ]);
    const normal = groups.find((g) => g.key === "normal");
    const insufficient = groups.find((g) => g.key === "insufficient");
    expect(normal?.count).toBe(1);
    expect(insufficient?.count).toBe(1);
    // Neither good news nor bad: no verdict was reached.
    expect(insufficient?.tone).toBe("");
  });

  it("groups open and candidate as attention required", () => {
    const groups = healthDistribution([
      source({ state: "OPEN" }),
      source({ state: "CANDIDATE" }),
    ]);
    expect(groups.find((g) => g.key === "attention")?.count).toBe(2);
  });

  it("omits groups with no members rather than listing zeroes", () => {
    const groups = healthDistribution([source({ state: "NORMAL" })]);
    expect(groups.map((g) => g.key)).toEqual(["normal"]);
  });
});

describe("healthSummaryText", () => {
  it("states the distribution as a sentence", () => {
    const groups = healthDistribution([
      source({ state: "OPEN" }),
      source({ state: "NORMAL" }),
    ]);
    expect(healthSummaryText(groups, 2, 0)).toBe(
      "2 monitored sources: 1 attention required, 1 normal.",
    );
  });

  it("adds the silent count from the backend's own definition", () => {
    const groups = healthDistribution([source({ state: "NORMAL" })]);
    expect(healthSummaryText(groups, 1, 1)).toContain("1 is currently silent");
  });

  it("says so when nothing is monitored", () => {
    expect(healthSummaryText([], 0, 0)).toBe("No monitored log sources.");
  });
});
