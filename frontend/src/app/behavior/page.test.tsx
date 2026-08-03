import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BehaviorPage from "./page";
import {
  ApiError,
  api,
  type AnomalySummary,
  type BehaviorSummary,
  type Page,
  type SourceBehavior,
} from "@/lib/api";

function summary(over: Partial<BehaviorSummary> = {}): BehaviorSummary {
  return {
    open_anomalies: 0,
    spikes: 0,
    drops: 0,
    silent_sources: 0,
    candidates: 0,
    recovering: 0,
    insufficient_data_sources: 0,
    monitored_sources: 0,
    evidence_pending: 0,
    evidence_failed: 0,
    recently_resolved: [],
    highest_deviation: [],
    ...over,
  };
}

function source(over: Partial<SourceBehavior> = {}): SourceBehavior {
  return {
    log_source_id: "11111111-1111-1111-1111-111111111111",
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

function anomaly(over: Partial<AnomalySummary> = {}): AnomalySummary {
  return {
    id: "a-1",
    log_source_id: "11111111-1111-1111-1111-111111111111",
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

function page(items: AnomalySummary[]): Page<AnomalySummary> {
  return { items, total: items.length, limit: 50, offset: 0 };
}

/** Mocks all three calls the overview makes. */
function mockApi({
  behavior = summary(),
  sources = [] as SourceBehavior[],
  active = [] as AnomalySummary[],
} = {}) {
  vi.spyOn(api, "behaviorSummary").mockResolvedValue(behavior);
  vi.spyOn(api, "sourceBehaviors").mockResolvedValue(sources);
  vi.spyOn(api, "anomalies").mockResolvedValue(page(active));
}

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderPage() {
  return render(await BehaviorPage());
}

describe("heading hierarchy", () => {
  it("has exactly one top-level heading", async () => {
    // The shell renders before the page, so a stray heading in the sidebar or
    // a second <h1> in a section would give the document two competing
    // outlines and make "skip to the heading" ambiguous.
    mockApi();
    await renderPage();

    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(/\S/);
  });

  it("puts every section heading below the page heading", async () => {
    mockApi({
      behavior: summary({ open_anomalies: 1, monitored_sources: 1 }),
      sources: [source()],
      active: [anomaly()],
    });
    await renderPage();

    // Non-vacuous: this page really does carry sections.
    const h2s = screen.getAllByRole("heading", { level: 2 });
    expect(h2s.length).toBeGreaterThan(0);
    // No section may claim the document's top level.
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });
});

describe("primary metrics", () => {
  it("shows exactly four primary KPI cards", async () => {
    // The page previously opened with ten equally weighted counters, which is
    // the same as opening with none.
    mockApi();
    const { container } = await renderPage();

    const primary = container.querySelector(".grid-4");
    expect(primary).not.toBeNull();
    expect(primary!.querySelectorAll(".card")).toHaveLength(4);
  });

  it("names the four the analyst triages on", async () => {
    mockApi({ behavior: summary({ open_anomalies: 3, silent_sources: 1 }) });
    await renderPage();

    expect(screen.getByText("Open anomalies")).toBeInTheDocument();
    expect(screen.getByText("Silent sources")).toBeInTheDocument();
    expect(screen.getByText("High deviation")).toBeInTheDocument();
    expect(screen.getByText("Insufficient data")).toBeInTheDocument();
  });

  it("derives high deviation without counting what it cannot measure", async () => {
    mockApi({
      sources: [
        source({ log_source_id: "s-1", deviation_ratio: 4 }),
        source({ log_source_id: "s-2", deviation_ratio: 1.1 }),
        // No baseline: no expectation to deviate from.
        source({ log_source_id: "s-3", state: "INSUFFICIENT_DATA", deviation_ratio: 9 }),
        // A ratio against nothing does not exist.
        source({ log_source_id: "s-4", deviation_ratio: null }),
      ],
    });
    await renderPage();

    expect(screen.getByText("High deviation").nextSibling).toHaveTextContent("1");
  });

  it("keeps the secondary counts out of the primary row", async () => {
    mockApi({ behavior: summary({ spikes: 2, drops: 1, evidence_pending: 4 }) });
    const { container } = await renderPage();

    const primary = container.querySelector(".grid-4")!;
    expect(within(primary as HTMLElement).queryByText("Spikes")).toBeNull();
    // Still present, in the compact strip.
    expect(screen.getByText("Spikes")).toBeInTheDocument();
    expect(screen.getByText("Evidence pending")).toBeInTheDocument();
  });

  it("counts the evidence backlog apart from evidence failures", async () => {
    mockApi({ behavior: summary({ evidence_pending: 4, evidence_failed: 2 }) });
    await renderPage();

    expect(screen.getByText("Evidence pending").nextSibling).toHaveTextContent("4");
    expect(screen.getByText("Evidence failed").nextSibling).toHaveTextContent("2");
  });
});

describe("source inventory", () => {
  it("no longer renders the full source inventory", async () => {
    // 55 of 59 rows required no action and outnumbered the ones that did.
    mockApi({
      sources: [
        source({ log_source_id: "n-1", name: "Quiet One", state: "NORMAL", open_anomaly_count: 0, deviation_ratio: 1 }),
        source({ log_source_id: "n-2", name: "Quiet Two", state: "NORMAL", open_anomaly_count: 0, deviation_ratio: 1 }),
      ],
    });
    await renderPage();

    expect(screen.queryByText("Quiet One")).toBeNull();
    expect(screen.queryByText("Quiet Two")).toBeNull();
    expect(screen.queryByText("Baseline samples")).toBeNull();
    expect(screen.queryByText("Baseline completeness")).toBeNull();
  });

  it("links on to the inventory with the fleet size", async () => {
    mockApi({ sources: [source(), source({ log_source_id: "s-2" })] });
    await renderPage();

    expect(
      screen.getByRole("link", { name: /View all 2 sources/i }),
    ).toHaveAttribute("href", "/behavior/sources");
  });
});

describe("needs attention", () => {
  it("does not list ordinary normal sources", async () => {
    mockApi({
      sources: [
        source({ name: "Quiet", state: "NORMAL", open_anomaly_count: 0, deviation_ratio: 1 }),
      ],
    });
    await renderPage();

    expect(
      screen.getByText(/Sources that are simply normal are not listed here/i),
    ).toBeInTheDocument();
  });

  it("puts a confirmed incident above a candidate", async () => {
    mockApi({
      active: [
        anomaly({ id: "cand", state: "CANDIDATE", log_source_name: "Candidate Source", log_source_id: "s-2" }),
        anomaly({ id: "open", state: "OPEN", log_source_name: "Open Source", log_source_id: "s-1" }),
      ],
    });
    await renderPage();

    const rows = screen.getAllByRole("row");
    const text = rows.map((r) => r.textContent ?? "");
    const openAt = text.findIndex((t) => t.includes("Open Source"));
    const candAt = text.findIndex((t) => t.includes("Candidate Source"));
    expect(openAt).toBeGreaterThan(-1);
    expect(openAt).toBeLessThan(candAt);
  });

  it("links an anomaly row to its investigation", async () => {
    mockApi({ active: [anomaly({ id: "a-9" })] });
    await renderPage();

    expect(screen.getByRole("link", { name: "Investigate" })).toHaveAttribute(
      "href",
      "/anomalies/a-9",
    );
  });

  it("never invents an expectation for an unbaselined source", async () => {
    // A 0 here would be an expectation the detector never formed.
    mockApi({
      sources: [
        source({
          name: "Unbaselined",
          state: "INSUFFICIENT_DATA",
          expected_eps: null,
          deviation_ratio: null,
          open_anomaly_count: 0,
        }),
      ],
    });
    await renderPage();

    const row = screen.getByRole("row", { name: /Unbaselined/ });
    expect(within(row).getByText("No adequate baseline")).toBeInTheDocument();
    // Observed over expected. The expected half is an em dash, never "0.00":
    // there is no expectation, and a zero would be one.
    const cells = within(row).getAllByRole("cell");
    expect(cells[3]).toHaveTextContent("6.00 / —");
  });
});

describe("insufficient data", () => {
  it("warns that unbaselined sources are not being judged", async () => {
    mockApi({
      behavior: summary({ insufficient_data_sources: 7, monitored_sources: 12 }),
    });
    await renderPage();

    expect(screen.getByText(/7 of 12 monitored sources/i)).toBeInTheDocument();
    expect(
      screen.getByText(/absence of a verdict, not a clean bill of health/i),
    ).toBeInTheDocument();
  });

  it("never paints the unbaselined count as healthy", async () => {
    mockApi({ behavior: summary({ insufficient_data_sources: 7 }) });
    await renderPage();

    const value = screen.getByText("Insufficient data").nextSibling as HTMLElement;
    expect(value.className).not.toContain("ok");
  });

  it("says so on the card itself, not only in a paragraph further down", async () => {
    mockApi({ behavior: summary({ insufficient_data_sources: 7 }) });
    await renderPage();

    expect(
      screen.getByText("Not being judged — a zero above does not cover these."),
    ).toBeInTheDocument();
  });

  it("omits the warning when every source has a baseline", async () => {
    mockApi({
      behavior: summary({ insufficient_data_sources: 0, monitored_sources: 12 }),
    });
    await renderPage();

    expect(screen.queryByText(/not being judged, so no anomaly count/i)).toBeNull();
  });
});

describe("recently resolved", () => {
  it("links each resolved anomaly to its investigation", async () => {
    mockApi({
      behavior: summary({
        recently_resolved: [anomaly({ id: "r-1", state: "RESOLVED" })],
      }),
    });
    await renderPage();

    expect(screen.getByRole("link", { name: "Investigate" })).toHaveAttribute(
      "href",
      "/anomalies/r-1",
    );
  });

  it("says nothing has been resolved yet", async () => {
    mockApi();
    await renderPage();

    expect(screen.getByText(/No anomalies have been resolved yet/i)).toBeInTheDocument();
  });
});

describe("source health", () => {
  it("offers the distribution as text, not only as a bar", async () => {
    mockApi({
      behavior: summary({ silent_sources: 1 }),
      sources: [
        source({ log_source_id: "s-1", state: "OPEN" }),
        source({ log_source_id: "s-2", state: "NORMAL" }),
      ],
    });
    await renderPage();

    expect(
      screen.getByText(/2 monitored sources: 1 attention required, 1 normal/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/1 is currently silent/i)).toBeInTheDocument();
  });

  it("keeps unbaselined sources out of the normal group", async () => {
    mockApi({
      sources: [
        source({ log_source_id: "s-1", state: "NORMAL" }),
        source({ log_source_id: "s-2", state: "INSUFFICIENT_DATA" }),
      ],
    });
    await renderPage();

    expect(screen.getByText(/1 insufficient data/i)).toBeInTheDocument();
  });
});

describe("empty states", () => {
  it("says no source is monitored rather than showing an empty table", async () => {
    mockApi();
    await renderPage();

    expect(screen.getByText(/No monitored log sources/i)).toBeInTheDocument();
  });
});

describe("error states", () => {
  it("reports an unreachable backend as a failure, not as an empty fleet", async () => {
    vi.spyOn(api, "behaviorSummary").mockRejectedValue(new Error("ECONNREFUSED"));
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    await renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent(/backend may be unreachable/i);
    expect(screen.queryByText("Open anomalies")).toBeNull();
  });

  it("reports a forbidden response as a permission problem", async () => {
    vi.spyOn(api, "behaviorSummary").mockRejectedValue(new ApiError(403, "forbidden"));
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    await renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent(/do not have permission/i);
  });

  it("reports an expired session on 401 without rendering any anomaly data", async () => {
    vi.spyOn(api, "behaviorSummary").mockRejectedValue(new ApiError(401, "unauthorized"));
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([source()]);
    vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    await renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent(/session has expired/i);
    expect(screen.queryByText("LAB Firewall")).toBeNull();
  });

  it("still renders the worklist when the active-anomaly call fails", async () => {
    // The anomaly list enriches the worklist; it is not the page.
    vi.spyOn(api, "behaviorSummary").mockResolvedValue(summary({ open_anomalies: 2 }));
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([source({ deviation_ratio: 5 })]);
    vi.spyOn(api, "anomalies").mockRejectedValue(new Error("ECONNREFUSED"));
    await renderPage();

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("Open anomalies")).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /LAB Firewall/ })).toBeInTheDocument();
  });
});
