import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SourceBehaviorPage from "./page";
import {
  ApiError,
  api,
  type AnomalySummary,
  type BaselineCell,
  type LogSourceDetail,
  type MetricBucket,
  type Page,
  type SourceBehavior,
} from "@/lib/api";

// The chart's own behavior is covered in VolumeChart.test.tsx. Here it is
// captured so these tests can assert what the page hands it — which is where a
// missing bucket would turn into a false zero.
const chartProps: Array<Record<string, unknown>> = [];
vi.mock("@/components/behavior/VolumeChart", () => ({
  VolumeChart: (props: Record<string, unknown>) => {
    chartProps.push(props);
    return (
      <>
        <div data-testid="volume-chart" />
        {typeof props.textSummary === "string" && <p>{props.textSummary}</p>}
      </>
    );
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

function meta(over: Partial<LogSourceDetail> = {}): LogSourceDetail {
  return {
    id: SOURCE_ID,
    qradar_id: 227,
    enabled: true,
    monitoring_enabled: true,
    maintenance_mode: false,
    timezone_name: "UTC",
    name: "LAB Firewall",
    type_name: "Netgate pfSense",
    description: null,
    criticality: "HIGH",
    owner: "soc",
    owner_email: null,
    qradar_status: "SUCCESS",
    health_score: 90,
    health_breakdown: null,
    business_hours_only: false,
    expected_interval_seconds: 60,
    last_event_time: "2026-07-20T10:04:00Z",
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

function page(items: AnomalySummary[]): Page<AnomalySummary> {
  return { items, total: items.length, limit: 20, offset: 0 };
}

beforeEach(() => {
  vi.restoreAllMocks();
  chartProps.length = 0;
});

async function renderPage({
  source = behavior(),
  buckets = [bucket()],
  anomalies = [] as AnomalySummary[],
  cells = [cell()],
  metadata = meta(),
  params = {} as Record<string, string | undefined>,
}: {
  source?: SourceBehavior;
  buckets?: MetricBucket[];
  anomalies?: AnomalySummary[];
  cells?: BaselineCell[];
  metadata?: LogSourceDetail | null;
  params?: Record<string, string | undefined>;
} = {}) {
  vi.spyOn(api, "sourceBehavior").mockResolvedValue(source);
  vi.spyOn(api, "logSource").mockResolvedValue(metadata as LogSourceDetail);
  vi.spyOn(api, "sourceMetrics").mockResolvedValue(buckets);
  vi.spyOn(api, "anomalies").mockResolvedValue(page(anomalies));
  vi.spyOn(api, "sourceBaselines").mockResolvedValue(cells);
  return render(
    await SourceBehaviorPage({
      params: Promise.resolve({ id: SOURCE_ID }),
      searchParams: Promise.resolve(params),
    }),
  );
}

describe("header", () => {
  it("has exactly one h1, naming the source once", async () => {
    const { container } = await renderPage();
    const h1s = container.querySelectorAll("h1");
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent("LAB Firewall");
  });

  it("gives the behavior state the dominant badge", async () => {
    await renderPage();
    expect(screen.getByText("OPEN").className).toContain("pill-strong");
  });

  it("shows the QRadar identifier as secondary metadata", async () => {
    await renderPage();
    expect(screen.getByText(/QRadar ID 227/)).toBeInTheDocument();
  });

  it("flags disabled monitoring and maintenance mode quietly", async () => {
    const { container } = await renderPage({
      metadata: meta({ monitoring_enabled: false, maintenance_mode: true }),
    });
    // Scoped to the header: both words also appear in the Source metadata
    // disclosure, where they are a label rather than a status.
    const header = container.querySelector(".incident-header") as HTMLElement;
    expect(within(header).getByText("Monitoring disabled").className).toContain("pill-quiet");
    expect(within(header).getByText("Maintenance mode").className).toContain("pill-quiet");
  });

  it("links back to the source inventory", async () => {
    await renderPage();
    expect(screen.getByRole("link", { name: /All sources/ })).toHaveAttribute(
      "href",
      "/behavior/sources",
    );
  });
});

describe("primary metrics", () => {
  it("shows exactly four", async () => {
    const { container } = await renderPage();
    expect(container.querySelector(".grid-4")!.querySelectorAll(".card")).toHaveLength(4);
  });

  it("shows observed, expected, deviation and last event", async () => {
    const { container } = await renderPage();
    const primary = container.querySelector(".grid-4") as HTMLElement;
    expect(within(primary).getByText("Observed EPS")).toBeInTheDocument();
    expect(within(primary).getByText("Expected EPS")).toBeInTheDocument();
    expect(within(primary).getByText("Deviation")).toBeInTheDocument();
    expect(within(primary).getByText("Last event")).toBeInTheDocument();
  });

  it("keeps baseline internals out of the primary row", async () => {
    const { container } = await renderPage();
    const primary = container.querySelector(".grid-4") as HTMLElement;
    expect(within(primary).queryByText(/Baseline samples/i)).toBeNull();
    expect(within(primary).queryByText(/completeness/i)).toBeNull();
  });

  it("renders a measured zero as 0.00, not as an em dash", async () => {
    // "This source sent no events" and "we did not observe this source" lead
    // to opposite actions.
    const { container } = await renderPage({ source: behavior({ observed_eps: 0 }) });
    const primary = container.querySelector(".grid-4") as HTMLElement;
    expect(within(primary).getByText("0.00")).toBeInTheDocument();
  });

  it("renders an absent measurement as an em dash, not as zero", async () => {
    const { container } = await renderPage({
      source: behavior({ observed_eps: null }),
    });
    const primary = container.querySelector(".grid-4") as HTMLElement;
    expect(within(primary).getByText("—")).toBeInTheDocument();
  });

  it("shows Still learning instead of an expected EPS of zero", async () => {
    const { container } = await renderPage({
      source: behavior({
        state: "INSUFFICIENT_DATA",
        expected_eps: null,
        deviation_ratio: null,
      }),
    });
    const primary = container.querySelector(".grid-4") as HTMLElement;
    expect(within(primary).getByText("Still learning")).toBeInTheDocument();
  });

  it("does not compute a deviation without an expectation", async () => {
    const { container } = await renderPage({
      source: behavior({
        state: "INSUFFICIENT_DATA",
        expected_eps: null,
        deviation_ratio: null,
      }),
    });
    const primary = container.querySelector(".grid-4") as HTMLElement;
    expect(within(primary).queryByText(/x$/)).toBeNull();
  });

  it("never lets an unbaselined silent source look healthy", async () => {
    await renderPage({
      source: behavior({
        state: "INSUFFICIENT_DATA",
        observed_eps: 0,
        expected_eps: null,
        deviation_ratio: null,
      }),
    });
    expect(screen.getByText("INSUFFICIENT_DATA")).toBeInTheDocument();
    expect(screen.getByText(/not being assessed/i)).toBeInTheDocument();
    expect(screen.getByText(/Baseline still learning/)).toBeInTheDocument();
  });

  it("notes a silent source beneath the metrics", async () => {
    await renderPage({ source: behavior({ observed_eps: 0 }) });
    expect(screen.getByText(/Source is currently silent/)).toBeInTheDocument();
  });
});

describe("order of the page", () => {
  it("puts the chart before the anomalies and the technical detail", async () => {
    const { container } = await renderPage({ anomalies: [anomaly()] });
    const headings = Array.from(container.querySelectorAll("h2")).map(
      (h) => h.textContent,
    );
    expect(headings.indexOf("Observed volume")).toBeLessThan(
      headings.indexOf("Anomalies"),
    );
    expect(headings.indexOf("Anomalies")).toBeLessThan(
      headings.indexOf("Technical detail"),
    );
  });

  it("puts data quality before the technical detail", async () => {
    const { container } = await renderPage();
    const headings = Array.from(container.querySelectorAll("h2")).map(
      (h) => h.textContent,
    );
    expect(headings.indexOf("Data quality")).toBeLessThan(
      headings.indexOf("Technical detail"),
    );
  });
});

describe("bucket completeness", () => {
  it("hands the raw buckets to the chart so completeness survives", async () => {
    const buckets = [
      bucket({ bucket_start: "2026-07-20T10:00:00Z" }),
      bucket({ bucket_start: "2026-07-20T10:01:00Z", completeness: "PARTIAL" }),
    ];
    await renderPage({ buckets });
    expect(chartProps[0].buckets).toEqual(buckets);
  });

  it("treats a COMPLETE zero as a real measurement", async () => {
    // 62 of 112 live buckets on the lab firewall are exactly this: collected
    // in full, and genuinely zero. Drawing them as gaps would hide real
    // silence; drawing gaps as zero would invent traffic.
    const buckets = [
      bucket({ average_eps: 0, event_count: 0, completeness: "COMPLETE" }),
    ];
    await renderPage({ buckets });
    expect(chartProps[0].buckets).toEqual(buckets);
    expect(screen.queryByText(/not fully collected/i)).toBeNull();
  });

  it("counts missing intervals as uncollected in the summary", async () => {
    await renderPage({
      buckets: [
        bucket(),
        bucket({ bucket_start: "2026-07-20T10:01:00Z", completeness: "MISSING" }),
      ],
    });
    expect(screen.getByText(/1 interval not collected/)).toBeInTheDocument();
  });

  it("counts partial intervals separately from missing ones", async () => {
    await renderPage({
      buckets: [
        bucket(),
        bucket({ bucket_start: "2026-07-20T10:01:00Z", completeness: "PARTIAL" }),
      ],
    });
    expect(screen.getByText(/1 interval only partly collected/)).toBeInTheDocument();
  });

  it("reports no collection as an absence, not as zero traffic", async () => {
    await renderPage({ buckets: [], source: behavior({ last_bucket_at: null }) });
    expect(screen.getByText(/No metric buckets have been collected/i)).toBeInTheDocument();
  });

  it("reports a failed metric fetch as a failed request", async () => {
    vi.spyOn(api, "sourceBehavior").mockResolvedValue(behavior());
    vi.spyOn(api, "logSource").mockResolvedValue(meta());
    vi.spyOn(api, "sourceMetrics").mockRejectedValue(new Error("timeout"));
    vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    vi.spyOn(api, "sourceBaselines").mockResolvedValue([]);
    render(
      await SourceBehaviorPage({
        params: Promise.resolve({ id: SOURCE_ID }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/not an absence of traffic/i);
  });
});

describe("chart text summary", () => {
  it("describes the range, the values and the anomaly count", async () => {
    await renderPage({
      buckets: [bucket({ average_eps: 2 }), bucket({ bucket_start: "2026-07-20T10:01:00Z", average_eps: 6 })],
      anomalies: [anomaly()],
    });
    const summary = chartProps[0].textSummary as string;
    expect(summary).toContain("last 6 hours");
    expect(summary).toContain("2.00 to 6.00 EPS");
    expect(summary).toContain("expected baseline is 2.00 EPS");
    expect(summary).toContain("1 anomaly was detected");
  });

  it("says a still-learning source has no expectation", async () => {
    await renderPage({
      source: behavior({ state: "INSUFFICIENT_DATA", expected_eps: null }),
    });
    expect(chartProps[0].textSummary as string).toContain("still learning");
  });

  it("passes no expected line when there is no baseline", async () => {
    await renderPage({
      source: behavior({ state: "INSUFFICIENT_DATA", expected_eps: null }),
    });
    expect(chartProps[0].expected).toBeNull();
  });
});

describe("anomalies", () => {
  it("treats only lifecycle states as active", async () => {
    // Not `resolved_at IS NULL`: a NORMAL row with no resolved timestamp is
    // not an active incident.
    await renderPage({
      anomalies: [
        anomaly({ id: "open", state: "OPEN" }),
        anomaly({ id: "normal", state: "NORMAL", resolved_at: null }),
      ],
    });
    const active = screen.getByRole("region", { name: "Active anomalies" });
    expect(within(active).getAllByRole("row")).toHaveLength(2); // header + one
    expect(within(active).getByText("OPEN")).toBeInTheDocument();
  });

  it("puts a resolved anomaly in the closed list with a link", async () => {
    await renderPage({
      anomalies: [anomaly({ id: "done", state: "RESOLVED", resolved_at: "2026-07-20T11:00:00Z" })],
    });
    const closed = screen.getByRole("region", { name: "Recently closed anomalies" });
    expect(within(closed).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "/anomalies/done",
    );
  });

  it("orders open before candidate before recovering", async () => {
    await renderPage({
      anomalies: [
        anomaly({ id: "r", state: "RECOVERING" }),
        anomaly({ id: "c", state: "CANDIDATE" }),
        anomaly({ id: "o", state: "OPEN" }),
      ],
    });
    const active = screen.getByRole("region", { name: "Active anomalies" });
    const states = within(active)
      .getAllByRole("row")
      .slice(1)
      .map((r) => r.textContent ?? "");
    expect(states[0]).toContain("OPEN");
    expect(states[1]).toContain("CANDIDATE");
    expect(states[2]).toContain("RECOVERING");
  });

  it("shows a calm empty state rather than an empty table", async () => {
    await renderPage({ anomalies: [] });
    expect(screen.queryByRole("region", { name: "Active anomalies" })).toBeNull();
    expect(screen.getByText(/No anomalies were detected in the last 6 hours/)).toBeInTheDocument();
  });

  it("says an empty result is not a clean one for an unbaselined source", async () => {
    await renderPage({
      source: behavior({ state: "INSUFFICIENT_DATA", expected_eps: null }),
      anomalies: [],
    });
    expect(screen.getByText(/This is not a clean result/i)).toBeInTheDocument();
  });

  it("reports a failed anomaly fetch separately from an empty result", async () => {
    vi.spyOn(api, "sourceBehavior").mockResolvedValue(behavior());
    vi.spyOn(api, "logSource").mockResolvedValue(meta());
    vi.spyOn(api, "sourceMetrics").mockResolvedValue([bucket()]);
    vi.spyOn(api, "anomalies").mockRejectedValue(new Error("boom"));
    vi.spyOn(api, "sourceBaselines").mockResolvedValue([]);
    render(
      await SourceBehaviorPage({
        params: Promise.resolve({ id: SOURCE_ID }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/not an absence of anomalies/i);
  });
});

describe("baseline quality", () => {
  it("reports a reliable baseline with its sample count", async () => {
    await renderPage({ cells: [cell({ sample_count: 8 })] });
    // "Reliable" is also a column header in the baseline-history table.
    const panel = screen.getByRole("heading", { name: "Baseline quality" })
      .closest("section") as HTMLElement;
    expect(within(panel).getByText("Reliable")).toBeInTheDocument();
    expect(
      screen.getByText(/based on 8 complete samples for this weekday and hour/i),
    ).toBeInTheDocument();
  });

  it("reports a still-learning baseline without calling it healthy", async () => {
    await renderPage({
      source: behavior({ state: "INSUFFICIENT_DATA", expected_eps: null, baseline_sample_count: 2 }),
      cells: [],
    });
    // Stated twice on purpose: once as the metric's value, once as the
    // baseline's status.
    expect(screen.getAllByText("Still learning").length).toBeGreaterThan(0);
    // Said by the lifecycle-state line and again by the baseline explanation.
    expect(screen.getAllByText(/not the same as being healthy/i).length).toBeGreaterThan(0);
  });

  it("explains zero variability without presenting it as an error", async () => {
    await renderPage({ cells: [cell({ mad: 0, sample_count: 9 })] });
    expect(screen.getByText("Zero variability")).toBeInTheDocument();
    expect(
      screen.getByText(/normal for a steady low-volume source/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/deterministic expected-band test/i)).toBeInTheDocument();
    // Once in the notes line under the metrics, once in the full explanation.
    expect(screen.getAllByText(/Baseline variability is zero/).length).toBeGreaterThan(1);
  });

  it("flags a cell the backend has not marked reliable", async () => {
    await renderPage({ cells: [cell({ is_reliable: false })] });
    expect(screen.getByText("Incomplete")).toBeInTheDocument();
  });
});

describe("collection health", () => {
  it("reports fully collected windows as current", async () => {
    // A full 1-hour window is 60 sixty-second buckets; anything less means
    // intervals with no stored bucket, which is not "fully collected".
    const now = Date.now();
    const full = Array.from({ length: 60 }, (_, i) =>
      bucket({ bucket_start: new Date(now - (59 - i) * 60_000).toISOString() }),
    );
    await renderPage({
      params: { range: "1" },
      buckets: full,
      source: behavior({ last_bucket_at: new Date(now).toISOString() }),
    });
    expect(screen.getByText("Current")).toBeInTheDocument();
  });

  it("counts intervals with no stored bucket as uncollected", async () => {
    // Counting only the rows that exist would report "1 of 1 fully observed"
    // over a window the chart draws as almost entirely gaps.
    await renderPage({
      params: { range: "1" },
      source: behavior({ last_bucket_at: new Date().toISOString() }),
      buckets: [bucket({ bucket_start: new Date().toISOString() })],
    });
    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(screen.getByText(/have no stored bucket at all/)).toBeInTheDocument();
  });

  it("reports partly collected windows as partial", async () => {
    const now = Date.now();
    const buckets = Array.from({ length: 60 }, (_, i) =>
      bucket({
        bucket_start: new Date(now - (59 - i) * 60_000).toISOString(),
        completeness: i === 0 ? "PARTIAL" : "COMPLETE",
      }),
    );
    await renderPage({
      params: { range: "1" },
      source: behavior({ last_bucket_at: new Date(now).toISOString() }),
      buckets,
    });
    expect(screen.getByText("Partial")).toBeInTheDocument();
    // Said by the collection explanation and again by the chart summary.
    expect(screen.getAllByText(/only partly collected/).length).toBeGreaterThan(0);
  });

  it("reports a stale watermark as delayed, not as silence", async () => {
    await renderPage({
      source: behavior({ last_bucket_at: "2020-01-01T00:00:00Z" }),
    });
    expect(screen.getByText("Delayed")).toBeInTheDocument();
    expect(
      screen.getByText(/says nothing about whether it is sending events/i),
    ).toBeInTheDocument();
  });

  it("marks an unavailable QRadar status rather than inventing one", async () => {
    await renderPage({ metadata: meta({ qradar_status: null }) });
    expect(screen.getByText("unavailable")).toBeInTheDocument();
  });
});

describe("advanced detail", () => {
  it("collapses every technical disclosure by default", async () => {
    const { container } = await renderPage();
    const details = container.querySelectorAll("details.disclosure");
    expect(details.length).toBeGreaterThanOrEqual(4);
    for (const d of details) {
      expect(d).not.toHaveAttribute("open");
    }
  });

  it("offers baseline history, buckets, collection and metadata", async () => {
    await renderPage();
    expect(screen.getByText("Baseline history")).toBeInTheDocument();
    expect(screen.getByText("Metric buckets")).toBeInTheDocument();
    expect(screen.getByText("Collection details")).toBeInTheDocument();
    expect(screen.getByText("Source metadata")).toBeInTheDocument();
  });

  it("keeps the baseline history out of the first viewport", async () => {
    const { container } = await renderPage();
    const chart = container.querySelector('[data-testid="volume-chart"]')!;
    const history = screen.getByText("Baseline history");
    expect(
      chart.compareDocumentPosition(history) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});

describe("time range", () => {
  it("defaults to six hours", async () => {
    await renderPage();
    expect(
      screen.getByRole("link", { name: "6 hours" }),
    ).toHaveAttribute("aria-current", "page");
  });

  it("honours a valid range and requests a bounded window", async () => {
    await renderPage({ params: { range: "24" } });
    expect(screen.getByRole("link", { name: "24 hours" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    // The spy renderPage installed, so the recorded call is the real one.
    const spy = vi.mocked(api.sourceMetrics);
    const call = spy.mock.calls[0][1] as { since?: string; limit?: number };
    expect(call.since).toBeTruthy();
    expect(call.limit).toBeLessThanOrEqual(5000);
  });

  it("falls back safely on an invalid range", async () => {
    await renderPage({ params: { range: "9999; DROP" } });
    expect(screen.getByRole("link", { name: "6 hours" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("offers the range as keyboard-operable links", async () => {
    await renderPage();
    for (const label of ["1 hour", "6 hours", "12 hours", "24 hours"]) {
      expect(screen.getByRole("link", { name: label })).toHaveAttribute("href");
    }
  });
});

describe("accessibility", () => {
  it("contains every table in a labelled scroll region", async () => {
    const { container } = await renderPage({ anomalies: [anomaly()] });
    for (const region of container.querySelectorAll(".table-scroll")) {
      expect(region).toHaveAttribute("role", "region");
      expect(region).toHaveAttribute("tabindex", "0");
      expect(region.getAttribute("aria-label")).toBeTruthy();
    }
  });

  it("marks every column header as a column header", async () => {
    await renderPage({ anomalies: [anomaly()] });
    for (const th of screen.getAllByRole("columnheader")) {
      expect(th).toHaveAttribute("scope", "col");
    }
  });

  it("exposes timestamps as machine-readable time elements", async () => {
    const { container } = await renderPage();
    expect(container.querySelector("time")).toHaveAttribute("dateTime");
  });
});

describe("error states", () => {
  it("reports a missing source", async () => {
    vi.spyOn(api, "sourceBehavior").mockRejectedValue(new ApiError(404, "nope"));
    render(
      await SourceBehaviorPage({
        params: Promise.resolve({ id: SOURCE_ID }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/does not exist/i);
  });

  it("reports a forbidden response", async () => {
    vi.spyOn(api, "sourceBehavior").mockRejectedValue(new ApiError(403, "no"));
    render(
      await SourceBehaviorPage({
        params: Promise.resolve({ id: SOURCE_ID }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/do not have permission/i);
  });

  it("reports an expired session without rendering behavior data", async () => {
    vi.spyOn(api, "sourceBehavior").mockRejectedValue(new ApiError(401, "no"));
    render(
      await SourceBehaviorPage({
        params: Promise.resolve({ id: SOURCE_ID }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/session has expired/i);
    expect(screen.queryByText("LAB Firewall")).toBeNull();
  });

  it("survives a failed metadata fetch", async () => {
    vi.spyOn(api, "sourceBehavior").mockResolvedValue(behavior());
    vi.spyOn(api, "logSource").mockRejectedValue(new Error("boom"));
    vi.spyOn(api, "sourceMetrics").mockResolvedValue([bucket()]);
    vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    vi.spyOn(api, "sourceBaselines").mockResolvedValue([]);
    render(
      await SourceBehaviorPage({
        params: Promise.resolve({ id: SOURCE_ID }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(screen.getByRole("heading", { level: 1, name: "LAB Firewall" })).toBeInTheDocument();
  });

  it("reports failed baseline history inside its disclosure", async () => {
    vi.spyOn(api, "sourceBehavior").mockResolvedValue(behavior());
    vi.spyOn(api, "logSource").mockResolvedValue(meta());
    vi.spyOn(api, "sourceMetrics").mockResolvedValue([bucket()]);
    vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    vi.spyOn(api, "sourceBaselines").mockRejectedValue(new Error("boom"));
    render(
      await SourceBehaviorPage({
        params: Promise.resolve({ id: SOURCE_ID }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(
      screen.getByText(/Baseline history could not be loaded/i),
    ).toBeInTheDocument();
  });
});
