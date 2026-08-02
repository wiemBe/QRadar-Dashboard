import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DetectionSummary } from "./DetectionSummary";
import type { AnomalyDetail, DetectionDetail } from "@/lib/api";

function detection(over: Partial<DetectionDetail> = {}): DetectionDetail {
  return {
    reason: "average EPS 6.00 is above the baseline median 2.00",
    expected_low: 1.7,
    expected_high: 2.3,
    threshold: 3.5,
    baseline_sample_count: 30,
    baseline_completeness: 0.9,
    baseline_version: 2,
    observed_eps: 6,
    expected_eps: 2,
    observed_events: 1800,
    expected_events: 600,
    absolute_delta_events: 1200,
    bucket_seconds: 300,
    ratio: 3,
    ratio_basis: null,
    robust_score_status: "OK",
    robust_z: 13.5,
    fallback_bound: null,
    ...over,
  };
}

function anomaly(over: Partial<AnomalyDetail> = {}): AnomalyDetail {
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
    anomaly_end: "2026-07-20T10:05:00Z",
    resolved_at: null,
    duration_seconds: 300,
    evidence_status: "COMPLETE",
    suppressed: false,
    explanation: null,
    baseline_version: 2,
    policy_version: 1,
    transitions: [],
    explanation_package: null,
    detection: detection(),
    ...over,
  };
}

describe("detector reasoning", () => {
  it("states expected, observed, the band and the thresholds", () => {
    render(<DetectionSummary anomaly={anomaly()} />);
    expect(screen.getByText("1.70 – 2.30")).toBeInTheDocument();
    expect(screen.getByText("±3.50")).toBeInTheDocument();
    expect(screen.getByText("13.50")).toBeInTheDocument();
    expect(screen.getByText("3.00x")).toBeInTheDocument();
  });

  it("reports the number of consecutive abnormal buckets", () => {
    render(<DetectionSummary anomaly={anomaly({ consecutive_buckets: 4 })} />);
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("reports the baseline strength behind the verdict", () => {
    render(<DetectionSummary anomaly={anomaly()} />);
    expect(screen.getByText("30")).toBeInTheDocument();
    expect(screen.getByText("90%")).toBeInTheDocument();
  });

  it("shows the detector's own sentence", () => {
    render(<DetectionSummary anomaly={anomaly()} />);
    expect(
      screen.getByText("average EPS 6.00 is above the baseline median 2.00"),
    ).toBeInTheDocument();
  });
});

describe("MAD status", () => {
  it("marks a usable robust score as OK", () => {
    render(<DetectionSummary anomaly={anomaly()} />);
    expect(screen.getByText("OK")).toBeInTheDocument();
    expect(screen.getByText(/MAD was non-zero/i)).toBeInTheDocument();
  });

  it("flags a degenerate score and explains what carried the verdict", () => {
    render(
      <DetectionSummary
        anomaly={anomaly({
          detection: detection({
            robust_score_status: "DEGENERATE",
            fallback_bound: 2.3,
          }),
        })}
      />,
    );
    expect(screen.getByText("DEGENERATE")).toBeInTheDocument();
    expect(screen.getByText(/MAD was zero/i)).toBeInTheDocument();
    expect(screen.getByText(/Deterministic fallback bound applied: 2.30 EPS/)).toBeInTheDocument();
  });

  it("marks the confidence as capped when the score was degenerate", () => {
    render(
      <DetectionSummary
        anomaly={anomaly({ detection: detection({ robust_score_status: "DEGENERATE" }) })}
      />,
    );
    expect(screen.getByText("capped")).toBeInTheDocument();
  });

  it("does not cap the confidence when the score was usable", () => {
    render(<DetectionSummary anomaly={anomaly()} />);
    expect(screen.queryByText("capped")).toBeNull();
  });

  it("says the status was not recorded rather than assuming OK", () => {
    render(
      <DetectionSummary
        anomaly={anomaly({ detection: detection({ robust_score_status: null }) })}
      />,
    );
    expect(screen.getByText("NOT RECORDED")).toBeInTheDocument();
  });
});

describe("a ratio that does not exist", () => {
  it("explains an absent ratio rather than leaving a bare dash", () => {
    // A spike out of a zero baseline has no ratio. An unexplained em dash here
    // reads as "unchanged", which is the opposite of what happened.
    render(
      <DetectionSummary
        anomaly={anomaly({
          deviation_ratio: null,
          detection: detection({ ratio: null, ratio_basis: "expected_zero" }),
        })}
      />,
    );
    expect(screen.getByText(/baseline expected no traffic/i)).toBeInTheDocument();
  });
});

describe("missing detection evidence", () => {
  it("says the thresholds cannot be shown rather than showing zeros", () => {
    render(<DetectionSummary anomaly={anomaly({ detection: null })} />);
    expect(screen.getByText(/no structured detector evidence/i)).toBeInTheDocument();
    expect(screen.queryByText("±0.00")).toBeNull();
  });

  it("dashes individual unrecorded facts", () => {
    render(
      <DetectionSummary
        anomaly={anomaly({
          detection: detection({ expected_low: null, expected_high: null, threshold: null }),
        })}
      />,
    );
    expect(screen.getByText("— – —")).toBeInTheDocument();
    expect(screen.getByText("±—")).toBeInTheDocument();
  });
});
