import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SourceBehaviorPage from "./page";
import {
  ApiError,
  api,
  type AnomalySummary,
  type MetricBucket,
  type SourceBehavior,
} from "@/lib/api";

// The chart's own behavior is covered in VolumeChart.test.tsx. Here it is
// captured so these tests can assert what the page hands it — which is where a
// missing bucket would turn into a false zero.
const chartProps: Array<Record<string, unknown>> = [];
vi.mock("@/components/behavior/VolumeChart", () => ({
  VolumeChart: (props: Record<string, unknown>) => {
    chartProps.push(props);
    return <div data-testid="volume-chart" />;
  },
}));

const SOURCE_ID = "11111111-1111-1111-1111-111111111111";

function behavior(over: Partial<SourceBehavior> = {}): SourceBehavior {
  return {
    log_source_id: SOURCE_ID,
    name: "LAB Firewall",
    criticality: "HIGH",
    observed_eps: 6,
    expected_eps: 2,
    expected_low: 1.7,
    expected_high: 2.3,
    deviation_ratio: 3,
    state: "OPEN",
    baseline_sample_count: 30,
    baseline_completeness: 0.9,
    last_bucket_at: "2026-07-20T10:00:00Z",
    last_event_at: "2026-07-20T10:04:00Z",
    open_anomaly_count: 1,
    ...over,
  };
}

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

function anomaly(over: Partial<AnomalySummary> = {}): AnomalySummary {
  return {
    id: "a-1",
    log_source_id: SOURCE_ID,
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
    evidence_status: "PARTIAL",
    suppressed: false,
    explanation: null,
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  chartProps.length = 0;
  vi.spyOn(api, "logSource").mockResolvedValue({
    id: SOURCE_ID,
    name: "LAB Firewall",
    type_name: "Cisco ASA",
    description: null,
    criticality: "HIGH",
    owner: "netsec",
    owner_email: null,
    qradar_status: "SUCCESS",
    health_score: 88,
    health_breakdown: null,
    business_hours_only: false,
    expected_interval_seconds: 300,
    last_event_time: "2026-07-20T10:04:00Z",
  });
  vi.spyOn(api, "sourceMetrics").mockResolvedValue([bucket()]);
  vi.spyOn(api, "anomalies").mockResolvedValue({
    items: [],
    total: 0,
    limit: 20,
    offset: 0,
  });
});

async function renderPage(source: SourceBehavior | Error = behavior()) {
  if (source instanceof Error) {
    vi.spyOn(api, "sourceBehavior").mockRejectedValue(source);
  } else {
    vi.spyOn(api, "sourceBehavior").mockResolvedValue(source);
  }
  render(await SourceBehaviorPage({ params: Promise.resolve({ id: SOURCE_ID }) }));
}

describe("observed vs expected", () => {
  it("reports both together with the deviation", async () => {
    await renderPage();
    const grid = document.querySelector(".grid") as HTMLElement;
    expect(within(grid).getByText("Observed EPS").nextSibling).toHaveTextContent("6.00");
    expect(within(grid).getByText("Expected EPS").nextSibling).toHaveTextContent("2.00");
    expect(within(grid).getByText("Deviation").nextSibling).toHaveTextContent("3.00x");
  });

  it("reports the expected band", async () => {
    await renderPage();
    expect(screen.getByRole("row", { name: /Expected band/ })).toHaveTextContent("1.70 – 2.30");
  });

  it("shows source metadata alongside the behavior", async () => {
    await renderPage();
    expect(screen.getByText(/Cisco ASA/)).toBeInTheDocument();
    expect(screen.getByText(/owner netsec/)).toBeInTheDocument();
  });

  it("survives a failed metadata fetch", async () => {
    vi.spyOn(api, "logSource").mockRejectedValue(new Error("down"));
    await renderPage();
    expect(screen.getByRole("heading", { name: "LAB Firewall" })).toBeInTheDocument();
  });
});

describe("no baseline", () => {
  it("shows a dash rather than an expected EPS of 0", async () => {
    // Rendering 0 would invent an expectation and make any traffic at all look
    // like a spike against it.
    await renderPage(
      behavior({ state: "INSUFFICIENT_DATA", expected_eps: null, deviation_ratio: null }),
    );
    const grid = document.querySelector(".grid") as HTMLElement;
    expect(within(grid).getByText("Expected EPS").nextSibling).toHaveTextContent("—");
  });

  it("passes no expected line to the chart", async () => {
    await renderPage(behavior({ state: "INSUFFICIENT_DATA", expected_eps: null }));
    expect(chartProps[0].expected).toBeNull();
  });

  it("says an empty anomaly list is not a clean result", async () => {
    await renderPage(behavior({ state: "INSUFFICIENT_DATA" }));
    expect(screen.getByText(/not a clean result/i)).toBeInTheDocument();
  });
});

describe("incomplete and missing buckets", () => {
  it("counts how many intervals were fully observed", async () => {
    vi.spyOn(api, "sourceMetrics").mockResolvedValue([
      bucket({ bucket_start: "2026-07-20T10:00:00Z" }),
      bucket({ bucket_start: "2026-07-20T10:05:00Z", completeness: "PARTIAL" }),
      bucket({ bucket_start: "2026-07-20T10:10:00Z", completeness: "MISSING" }),
    ]);
    await renderPage();
    expect(screen.getByText("1 of 3 intervals fully observed")).toBeInTheDocument();
  });

  it("says an uncollected interval is an undercount, not a measurement", async () => {
    vi.spyOn(api, "sourceMetrics").mockResolvedValue([
      bucket({ completeness: "PARTIAL" }),
    ]);
    await renderPage();
    expect(screen.getByText(/undercount, not a\s+measurement/i)).toBeInTheDocument();
    expect(screen.getByText(/never enter the baseline/i)).toBeInTheDocument();
  });

  it("hands the raw buckets to the chart so completeness survives", async () => {
    // The page must not pre-flatten these into values; the chart is what
    // decides that a non-COMPLETE bucket becomes a null.
    const buckets = [bucket({ completeness: "MISSING", average_eps: 0 })];
    vi.spyOn(api, "sourceMetrics").mockResolvedValue(buckets);
    await renderPage();
    expect(chartProps[0].buckets).toEqual(buckets);
  });

  it("says nothing about incomplete intervals when all were observed", async () => {
    await renderPage();
    expect(screen.queryByText(/undercount/i)).toBeNull();
  });

  it("reports no collection as an absence, not as zero traffic", async () => {
    vi.spyOn(api, "sourceMetrics").mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText("no buckets collected")).toBeInTheDocument();
  });

  it("reports a failed metric fetch as a failed request", async () => {
    vi.spyOn(api, "sourceMetrics").mockRejectedValue(new Error("timeout"));
    await renderPage();
    expect(screen.getByText(/failed request, not an absence of traffic/i)).toBeInTheDocument();
    expect(screen.queryByTestId("volume-chart")).toBeNull();
  });
});

describe("anomaly overlay", () => {
  it("passes the anomaly intervals to the chart", async () => {
    vi.spyOn(api, "anomalies").mockResolvedValue({
      items: [anomaly()],
      total: 1,
      limit: 20,
      offset: 0,
    });
    await renderPage();
    expect(chartProps[0].anomalies).toEqual([
      { start: "2026-07-20T10:00:00Z", end: null, label: "VOLUME_SPIKE" },
    ]);
  });

  it("omits an anomaly with no start rather than guessing one", async () => {
    vi.spyOn(api, "anomalies").mockResolvedValue({
      items: [anomaly({ anomaly_start: null })],
      total: 1,
      limit: 20,
      offset: 0,
    });
    await renderPage();
    expect(chartProps[0].anomalies).toEqual([]);
  });

  it("lists the anomalies with links to their investigations", async () => {
    vi.spyOn(api, "anomalies").mockResolvedValue({
      items: [anomaly()],
      total: 1,
      limit: 20,
      offset: 0,
    });
    await renderPage();
    expect(screen.getByRole("link", { name: "Investigate" })).toHaveAttribute(
      "href",
      "/anomalies/a-1",
    );
  });

  it("reports a failed anomaly fetch separately from an empty result", async () => {
    vi.spyOn(api, "anomalies").mockRejectedValue(new Error("down"));
    await renderPage();
    expect(screen.getByText(/Anomaly history could not be loaded/i)).toBeInTheDocument();
  });
});

describe("error states", () => {
  it("reports a missing source", async () => {
    await renderPage(new ApiError(404, "log source not found"));
    expect(screen.getByRole("alert")).toHaveTextContent(/does not exist/i);
  });

  it("reports a forbidden response", async () => {
    await renderPage(new ApiError(403, "forbidden"));
    expect(screen.getByRole("alert")).toHaveTextContent(/do not have permission/i);
  });

  it("reports an expired session without rendering behavior data", async () => {
    await renderPage(new ApiError(401, "unauthorized"));
    expect(screen.getByRole("alert")).toHaveTextContent(/session has expired/i);
    expect(screen.queryByTestId("volume-chart")).toBeNull();
  });
});
