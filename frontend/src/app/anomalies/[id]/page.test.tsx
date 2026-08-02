import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomalyDetailPage from "./page";
import {
  ApiError,
  api,
  type AnomalyDetail,
  type Contributor,
  type ExplanationDimension,
  type ExplanationPackage,
} from "@/lib/api";

// The chart is exercised by its own test file; here it is stubbed so these
// tests are about the investigation content rather than about ECharts.
vi.mock("@/components/behavior/VolumeChart", () => ({
  VolumeChart: () => <div data-testid="volume-chart" />,
}));

function contributor(over: Partial<Contributor> = {}): Contributor {
  return {
    dimension: "source_ip",
    value: "203.0.113.50",
    label: null,
    baseline_count: 12,
    anomaly_count: 4820,
    absolute_delta: 4808,
    percent_delta: 400.6,
    anomaly_share: 0.68,
    baseline_share: 0.02,
    contribution_share: 0.68,
    baseline_rank: 5,
    anomaly_rank: 1,
    rank: 1,
    is_new: false,
    is_disappeared: false,
    ...over,
  };
}

function dimension(over: Partial<ExplanationDimension> = {}): ExplanationDimension {
  return {
    dimension: "source_ip",
    availability: "AVAILABLE",
    detail: null,
    baseline_distinct_count: 5,
    anomaly_distinct_count: 42,
    cardinality_ratio: 8.4,
    new_value_count: 37,
    disappeared_value_count: 0,
    baseline_top_share: 0.33,
    anomaly_top_share: 0.68,
    truncated: false,
    contributors: [contributor()],
    ...over,
  };
}

function packaged(over: Partial<ExplanationPackage> = {}): ExplanationPackage {
  return {
    status: "COMPLETE",
    error: null,
    anomaly_window_start: "2026-07-20T10:00:00Z",
    anomaly_window_end: "2026-07-20T10:05:00Z",
    baseline_window_start: "2026-07-20T09:45:00Z",
    baseline_window_end: "2026-07-20T10:00:00Z",
    comparison_strategy: "recent_normal_window",
    anomaly_total_events: 1800,
    baseline_total_events: 600,
    requested_at: "2026-07-20T10:06:00Z",
    completed_at: "2026-07-20T10:06:30Z",
    collection_duration_ms: 30000,
    query_provenance: {
      queries: [
        {
          dimension: "source_ip",
          window: "anomaly",
          aql: "SELECT sourceip, COUNT(*) FROM events GROUP BY sourceip",
          rows: 42,
          truncated: false,
          error: null,
        },
      ],
    },
    schema_version: 1,
    dimensions: [dimension()],
    ...over,
  };
}

function detail(over: Partial<AnomalyDetail> = {}): AnomalyDetail {
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
    explanation: "average EPS 6.00 is above the baseline median 2.00",
    baseline_version: 2,
    policy_version: 1,
    transitions: [
      {
        from_state: "CANDIDATE",
        to_state: "OPEN",
        occurred_at: "2026-07-20T10:05:00Z",
        bucket_start: "2026-07-20T10:00:00Z",
        reason: "2 consecutive abnormal bucket(s)",
        actor: "anomaly-engine",
        observed_value: 6,
        expected_value: 2,
      },
    ],
    explanation_package: packaged(),
    detection: {
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
    },
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "sourceMetrics").mockResolvedValue([]);
});

/** A header stat card's value. Scoped to the card grid, because the same
 *  labels legitimately recur as column headings further down the page. */
function cardValue(label: string): HTMLElement {
  const grid = document.querySelector(".grid") as HTMLElement;
  return within(grid).getByText(label).nextSibling as HTMLElement;
}

async function renderPage(anomaly: AnomalyDetail | Error) {
  if (anomaly instanceof Error) {
    vi.spyOn(api, "anomaly").mockRejectedValue(anomaly);
  } else {
    vi.spyOn(api, "anomaly").mockResolvedValue(anomaly);
  }
  render(await AnomalyDetailPage({ params: Promise.resolve({ id: "a-1" }) }));
}

describe("header", () => {
  it("names the source, detector, state and severity", async () => {
    await renderPage(detail());
    expect(
      screen.getByRole("heading", { name: /LAB Firewall · VOLUME_SPIKE/ }),
    ).toBeInTheDocument();
    // Both recur in the lifecycle table below, so presence is what matters.
    expect(screen.getAllByText("OPEN").length).toBeGreaterThan(0);
    expect(screen.getAllByText("HIGH").length).toBeGreaterThan(0);
  });

  it("reports the observed, expected and deviation figures", async () => {
    await renderPage(detail());
    expect(cardValue("Observed")).toHaveTextContent("6.00");
    expect(cardValue("Expected")).toHaveTextContent("2.00");
    expect(cardValue("Deviation")).toHaveTextContent("3.00x");
    expect(cardValue("Absolute delta")).toHaveTextContent("1200");
  });

  it("reports both version stamps behind the verdict", async () => {
    await renderPage(detail());
    expect(screen.getByRole("row", { name: /Baseline version/ })).toHaveTextContent("v2");
    expect(screen.getByRole("row", { name: /Baseline version/ })).toHaveTextContent("v1");
  });

  it("says a still-running anomaly has not ended, rather than inventing an end", async () => {
    await renderPage(detail({ anomaly_end: null, duration_seconds: null }));
    expect(screen.getByText("still running")).toBeInTheDocument();
    expect(cardValue("Duration")).toHaveTextContent("—");
  });

  it("links to the source's behavior page", async () => {
    await renderPage(detail());
    expect(screen.getByRole("link", { name: /view source behavior/i })).toHaveAttribute(
      "href",
      "/behavior/sources/s-1",
    );
  });
});

describe("complete evidence", () => {
  it("renders the contributor analysis", async () => {
    await renderPage(detail());
    expect(screen.getByText("203.0.113.50")).toBeInTheDocument();
    expect(screen.getByText("+4,808")).toBeInTheDocument();
  });

  it("renders the dimension summary", async () => {
    await renderPage(detail());
    expect(screen.getByRole("heading", { name: "Dimension summary" })).toBeInTheDocument();
  });

  it("renders sanitized query provenance without exposing credentials", async () => {
    await renderPage(detail());
    expect(screen.getByRole("heading", { name: "Query provenance" })).toBeInTheDocument();
    expect(screen.getByText(/SELECT sourceip/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/SEC |authorization|password|token/i);
  });
});

describe("partial evidence", () => {
  it("states that some dimensions were not checked", async () => {
    await renderPage(
      detail({
        evidence_status: "PARTIAL",
        explanation_package: packaged({
          status: "PARTIAL",
          dimensions: [
            dimension(),
            dimension({
              dimension: "username",
              availability: "UNAVAILABLE",
              detail: "field is not populated for this log source",
              contributors: [],
            }),
          ],
        }),
      }),
    );
    expect(screen.getByText(/have not been checked/i)).toBeInTheDocument();
    expect(screen.getByText(/1 dimension was not checked/i)).toBeInTheDocument();
  });
});

describe("an unavailable dimension", () => {
  it("is rendered rather than hidden", async () => {
    // The failure this guards against: an analyst reading the absence of a
    // Username section as "usernames were checked and were normal".
    await renderPage(
      detail({
        explanation_package: packaged({
          dimensions: [
            dimension(),
            dimension({
              dimension: "username",
              availability: "UNAVAILABLE",
              detail: "field is not populated for this log source",
              baseline_distinct_count: null,
              anomaly_distinct_count: null,
              cardinality_ratio: null,
              contributors: [],
            }),
          ],
        }),
      }),
    );
    const section = screen.getByLabelText("Username contributors");
    expect(section).toBeInTheDocument();
    expect(
      within(section).getByText(/not exposed by the QRadar event schema or DSM/i),
    ).toBeInTheDocument();
  });
});

describe("failed evidence", () => {
  it("shows the failure and does not present the page as clean", async () => {
    await renderPage(
      detail({
        evidence_status: "FAILED",
        explanation_package: packaged({
          status: "FAILED",
          error: "Ariel search timed out after 120s",
          dimensions: [],
        }),
      }),
    );
    expect(screen.getByText(/Ariel search timed out/)).toBeInTheDocument();
    expect(screen.getByText(/nothing here has been ruled out/i)).toBeInTheDocument();
  });
});

describe("no evidence at all", () => {
  it("says no field has been examined", async () => {
    await renderPage(
      detail({ evidence_status: "NOT_REQUESTED", explanation_package: null }),
    );
    expect(screen.getByText(/No dimension analysis is stored/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing here has been ruled out/i)).toBeInTheDocument();
  });

  it("omits the provenance section rather than rendering an empty one", async () => {
    await renderPage(detail({ explanation_package: null }));
    expect(screen.queryByRole("heading", { name: "Query provenance" })).toBeNull();
  });
});

describe("lifecycle", () => {
  it("renders the transition history", async () => {
    await renderPage(detail());
    expect(screen.getByRole("heading", { name: "Lifecycle history" })).toBeInTheDocument();
    expect(screen.getByText("2 consecutive abnormal bucket(s)")).toBeInTheDocument();
  });
});

describe("degenerate MAD", () => {
  it("states the confidence limitation on the investigation page", async () => {
    await renderPage(
      detail({
        detection: {
          ...detail().detection!,
          robust_score_status: "DEGENERATE",
          fallback_bound: 2.3,
        },
      }),
    );
    expect(screen.getByText("DEGENERATE")).toBeInTheDocument();
    expect(screen.getByText("capped")).toBeInTheDocument();
    expect(screen.getByText(/weaker evidence/i)).toBeInTheDocument();
  });
});

describe("zero and null values", () => {
  it("renders a NO_EVENTS anomaly's observed zero as 0", async () => {
    await renderPage(
      detail({
        anomaly_type: "NO_EVENTS",
        observed_value: 0,
        detection: { ...detail().detection!, observed_eps: 0, observed_events: 0 },
      }),
    );
    expect(cardValue("Observed")).toHaveTextContent("0.00");
  });

  it("renders unmeasured header figures as dashes, not zeros", async () => {
    await renderPage(
      detail({
        observed_value: null,
        expected_value: null,
        deviation_ratio: null,
        robust_z: null,
        confidence: null,
        absolute_delta: null,
      }),
    );
    expect(cardValue("Observed")).toHaveTextContent("—");
    expect(cardValue("Deviation")).toHaveTextContent("—");
    expect(cardValue("Confidence")).toHaveTextContent("—");
  });
});

describe("timeline", () => {
  it("renders the chart when metrics load", async () => {
    await renderPage(detail());
    expect(screen.getByTestId("volume-chart")).toBeInTheDocument();
  });

  it("keeps the investigation when metric history fails", async () => {
    vi.spyOn(api, "sourceMetrics").mockRejectedValue(new Error("timeout"));
    await renderPage(detail());
    expect(screen.getByText(/timeline below is absent rather than empty/i)).toBeInTheDocument();
    // The substance of the page survives a failed chart fetch.
    expect(screen.getByRole("heading", { name: "Lifecycle history" })).toBeInTheDocument();
  });
});

describe("error states", () => {
  it("reports a missing anomaly", async () => {
    await renderPage(new ApiError(404, "anomaly not found"));
    expect(screen.getByRole("alert")).toHaveTextContent(/does not exist/i);
  });

  it("reports a forbidden response without leaking evidence", async () => {
    await renderPage(new ApiError(403, "forbidden"));
    expect(screen.getByRole("alert")).toHaveTextContent(/do not have permission/i);
    expect(screen.queryByText("203.0.113.50")).toBeNull();
  });

  it("reports an expired session on 401", async () => {
    await renderPage(new ApiError(401, "unauthorized"));
    expect(screen.getByRole("alert")).toHaveTextContent(/session has expired/i);
  });

  it("reports an unreachable backend", async () => {
    await renderPage(new Error("ECONNREFUSED"));
    expect(screen.getByRole("alert")).toHaveTextContent(/backend may be unreachable/i);
  });
});
