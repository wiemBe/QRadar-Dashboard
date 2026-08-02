import { describe, expect, it } from "vitest";

import type {
  AnomalyDetail,
  Contributor,
  ExplanationDimension,
  ExplanationPackage,
} from "./api";
import { summarizeAnomaly, summarizeTimeline } from "./summarize";

function contributor(over: Partial<Contributor> = {}): Contributor {
  return {
    dimension: "event_name",
    value: "Firewall - Deny",
    label: null,
    baseline_count: 65,
    anomaly_count: 539,
    absolute_delta: 474,
    percent_delta: 729.2,
    anomaly_share: 0.75,
    baseline_share: 0.27,
    contribution_share: 0.99,
    baseline_rank: 2,
    anomaly_rank: 1,
    rank: 1,
    is_new: false,
    is_disappeared: false,
    ...over,
  };
}

function dimension(over: Partial<ExplanationDimension> = {}): ExplanationDimension {
  return {
    dimension: "event_name",
    availability: "AVAILABLE",
    contributors: [contributor()],
    baseline_distinct_count: 2,
    anomaly_distinct_count: 2,
    cardinality_ratio: 1,
    baseline_top_share: 0.27,
    anomaly_top_share: 0.75,
    new_value_count: 0,
    disappeared_value_count: 0,
    truncated: false,
    detail: null,
    ...over,
  };
}

function packaged(dimensions: ExplanationDimension[]): ExplanationPackage {
  return {
    status: "PARTIAL",
    error: null,
    anomaly_window_start: "2026-08-02T18:35:00Z",
    anomaly_window_end: "2026-08-02T18:39:00Z",
    baseline_window_start: "2026-08-02T17:35:00Z",
    baseline_window_end: "2026-08-02T17:39:00Z",
    comparison_strategy: "PRECEDING_WINDOW",
    anomaly_total_events: 717,
    baseline_total_events: 240,
    requested_at: "2026-08-02T18:41:00Z",
    completed_at: "2026-08-02T18:41:02Z",
    collection_duration_ms: 1800,
    query_provenance: {},
    schema_version: 1,
    dimensions,
  };
}

function anomaly(over: Partial<AnomalyDetail> = {}): AnomalyDetail {
  return {
    id: "a-1",
    log_source_id: "s-1",
    log_source_name: "LAB Phase A Firewall",
    anomaly_type: "VOLUME_SPIKE",
    state: "RESOLVED",
    severity: "MEDIUM",
    observed_value: 5.983,
    expected_value: 1.983,
    deviation_ratio: 3.0168,
    robust_z: 20.168,
    absolute_delta: 240,
    consecutive_buckets: 4,
    confidence: 0.457,
    detected_at: "2026-08-02T18:35:00Z",
    opened_at: "2026-08-02T18:37:39Z",
    anomaly_start: "2026-08-02T18:35:00Z",
    anomaly_end: "2026-08-02T18:39:00Z",
    resolved_at: "2026-08-02T18:41:39Z",
    duration_seconds: 240,
    evidence_status: "COMPLETE",
    suppressed: false,
    explanation: null,
    baseline_version: 1,
    policy_version: 1,
    transitions: [],
    explanation_package: null,
    detection: null,
    ...over,
  };
}

describe("summarizeAnomaly — volume clause", () => {
  it("describes a spike as an increase from the expected value", () => {
    expect(summarizeAnomaly(anomaly()).text).toContain(
      "Event volume increased from an expected 1.98 EPS to 5.98 EPS.",
    );
  });

  it("describes a drop as a decrease", () => {
    const s = summarizeAnomaly(
      anomaly({
        anomaly_type: "VOLUME_DROP",
        observed_value: 1,
        expected_value: 4.983,
        deviation_ratio: 0.2,
      }),
    );
    expect(s.text).toContain(
      "Event volume decreased from an expected 4.98 EPS to 1.00 EPS.",
    );
  });

  it("describes silence without inventing an observed value", () => {
    const s = summarizeAnomaly(
      anomaly({
        anomaly_type: "NO_EVENTS",
        observed_value: 0,
        expected_value: 5,
        deviation_ratio: null,
      }),
    );
    expect(s.text).toContain(
      "No events were observed for a source that normally produces approximately 5.00 EPS.",
    );
  });

  it("does not state a change between numbers it does not have", () => {
    const s = summarizeAnomaly(
      anomaly({ observed_value: null, expected_value: null }),
    );
    expect(s.text).toContain("Event volume deviated from its expected baseline.");
    expect(s.text).not.toContain("0.00");
  });
});

describe("summarizeAnomaly — contributor clause", () => {
  const spikeEvidence = packaged([
    dimension({
      dimension: "event_name",
      contributors: [contributor({ dimension: "event_name", value: "Firewall - Deny" })],
    }),
    dimension({
      dimension: "destination_port",
      contributors: [
        contributor({
          dimension: "destination_port",
          value: "445",
          contribution_share: 0.959,
        }),
      ],
    }),
    dimension({
      dimension: "source_ip",
      contributors: [
        contributor({
          dimension: "source_ip",
          value: "203.0.113.50",
          contribution_share: 0.958,
        }),
      ],
    }),
  ]);

  it("names the strongest contributors in dimension-priority order", () => {
    const s = summarizeAnomaly(anomaly({ explanation_package: spikeEvidence }));
    expect(s.text).toContain(
      "The largest observed contributors were Firewall - Deny traffic, destination port 445 and source IP 203.0.113.50.",
    );
  });

  it("calls a drop's contributors reductions", () => {
    // The backend reports a negative contribution share on a drop. Calling
    // those values "contributors" reads as though they increased.
    const s = summarizeAnomaly(
      anomaly({
        anomaly_type: "VOLUME_DROP",
        observed_value: 1,
        expected_value: 4.983,
        explanation_package: packaged([
          dimension({
            dimension: "event_name",
            contributors: [
              contributor({
                dimension: "event_name",
                value: "Firewall - Permit",
                contribution_share: -0.704,
                absolute_delta: -451,
              }),
            ],
          }),
        ]),
      }),
    );
    expect(s.text).toContain("The largest observed reduction was in Firewall - Permit traffic.");
  });

  // Causation is not measured. A contribution share says how much of the
  // change a value accounts for, and nothing about why.
  it("never claims a cause", () => {
    const s = summarizeAnomaly(anomaly({ explanation_package: spikeEvidence }));
    expect(s.text).not.toMatch(/caused|because|due to|responsible for/i);
  });

  it("speaks for no dimension that was not collected", () => {
    const s = summarizeAnomaly(
      anomaly({
        explanation_package: packaged([
          dimension({ dimension: "username", availability: "UNAVAILABLE", contributors: [] }),
          dimension({
            dimension: "qid",
            availability: "FAILED",
            contributors: [contributor({ dimension: "qid", value: "should-not-appear" })],
          }),
        ]),
      }),
    );
    expect(s.text).not.toContain("should-not-appear");
    expect(s.text).toContain("Contributor evidence is not available.");
  });

  it("uses a truncated dimension's rows but says the result was capped", () => {
    const s = summarizeAnomaly(
      anomaly({
        explanation_package: packaged([
          dimension({
            dimension: "source_port",
            availability: "TRUNCATED",
            truncated: true,
            contributors: [contributor({ dimension: "source_port", value: "18819" })],
          }),
        ]),
      }),
    );
    expect(s.text).toContain("source port 18819");
    expect(s.caveat).toBe("Some results were limited to the top values.");
  });

  it("falls back honestly when evidence is absent", () => {
    expect(summarizeAnomaly(anomaly()).text).toContain(
      "Contributor evidence is not available.",
    );
  });

  it("distinguishes pending collection from absent evidence", () => {
    expect(
      summarizeAnomaly(anomaly({ evidence_status: "PENDING" })).text,
    ).toContain("Contributor evidence is still being collected.");
  });

  it("distinguishes failed collection from absent evidence", () => {
    expect(summarizeAnomaly(anomaly({ evidence_status: "FAILED" })).text).toContain(
      "Contributor evidence collection failed",
    );
  });
});

describe("summarizeAnomaly — caveat", () => {
  it("states the limitation on a partial package", () => {
    expect(summarizeAnomaly(anomaly({ evidence_status: "PARTIAL" })).caveat).toBe(
      "Some QRadar fields were unavailable or truncated.",
    );
  });

  it("adds no caveat to a complete one", () => {
    expect(summarizeAnomaly(anomaly({ evidence_status: "COMPLETE" })).caveat).toBeNull();
  });
});

describe("summarizeAnomaly — determinism", () => {
  it("returns the same sentence for the same input", () => {
    const a = anomaly({ explanation_package: packaged([dimension()]) });
    expect(summarizeAnomaly(a)).toEqual(summarizeAnomaly(a));
  });
});

describe("summarizeTimeline", () => {
  const base = {
    observed: [1, 2, 6, 1],
    expected: 2,
    anomalyStart: "2026-08-02T18:35:00Z",
    anomalyEnd: "2026-08-02T18:39:00Z",
    state: "RESOLVED",
    incompleteBuckets: 0,
    totalBuckets: 4,
  };

  it("states the observed range, the baseline and the interval", () => {
    const text = summarizeTimeline(base);
    expect(text).toContain("ranged from 1.00 to 6.00 EPS");
    expect(text).toContain("expected baseline was 2.00 EPS");
    expect(text).toContain("now RESOLVED");
  });

  it("says an open anomaly has not ended", () => {
    const text = summarizeTimeline({ ...base, anomalyEnd: null, state: "OPEN" });
    expect(text).toContain("has not ended");
  });

  it("reports uncollected intervals as gaps rather than zero traffic", () => {
    const text = summarizeTimeline({ ...base, incompleteBuckets: 3 });
    expect(text).toContain("3 intervals were not fully collected");
    expect(text).toContain("gap rather than as zero traffic");
  });

  it("ignores nulls when computing the observed range", () => {
    const text = summarizeTimeline({ ...base, observed: [null, 4, null, 9] });
    expect(text).toContain("ranged from 4.00 to 9.00 EPS");
  });

  it("says so when nothing was observed at all", () => {
    const text = summarizeTimeline({ ...base, observed: [null, null] });
    expect(text).toContain("No volume was observed in this window");
  });
});
