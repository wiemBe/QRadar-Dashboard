import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomalyDetailPage from "./page";
import {
  ApiError,
  api,
  type AnomalyDetail,
  type Contributor,
  type ExplanationDimension,
  type ExplanationPackage,
  type MetricBucket,
} from "@/lib/api";

// ECharts needs a real canvas, which jsdom does not provide. The chart's own
// behaviour is covered in VolumeChart.test.tsx; here it is stubbed down to the
// two things this page asserts about it — that it occupies the chart slot, and
// that it receives a text summary of what it plots.
vi.mock("@/components/behavior/VolumeChart", () => ({
  VolumeChart: ({
    ariaLabel,
    textSummary,
  }: {
    ariaLabel?: string;
    textSummary?: string;
  }) => (
    <>
      <div className="chart" role="img" aria-label={ariaLabel} />
      {textSummary && <p>{textSummary}</p>}
    </>
  ),
}));

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
    contribution_share: 0.994,
    baseline_rank: 2,
    anomaly_rank: 1,
    rank: 1,
    is_new: false,
    is_disappeared: false,
    ...over,
  };
}

function dimension(over: Partial<ExplanationDimension> = {}): ExplanationDimension {
  const dim = over.dimension ?? "event_name";
  return {
    dimension: dim,
    availability: "AVAILABLE",
    contributors: [contributor({ dimension: dim })],
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

/** The live Phase A spike's evidence shape: several available, one capped, one absent. */
function livePackage(over: Partial<ExplanationPackage> = {}): ExplanationPackage {
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
    query_provenance: {
      queries: [
        {
          dimension: "qid",
          window: "anomaly",
          rows: 2,
          truncated: false,
          error: null,
          aql: 'SELECT qid AS "value" FROM events WHERE logsourceid = 227 START 1785695700000 STOP 1785695820000',
        },
      ],
    },
    schema_version: 1,
    dimensions: [
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
            baseline_count: 47,
            anomaly_count: 528,
            absolute_delta: 481,
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
      dimension({ dimension: "destination_ip" }),
      dimension({ dimension: "action" }),
      dimension({
        dimension: "source_port",
        availability: "TRUNCATED",
        truncated: true,
        new_value_count: 0,
        disappeared_value_count: 0,
      }),
      dimension({
        dimension: "username",
        availability: "UNAVAILABLE",
        contributors: [],
        detail: "field is not populated for this log source",
      }),
    ],
    ...over,
  };
}

function anomaly(over: Partial<AnomalyDetail> = {}): AnomalyDetail {
  return {
    id: "b29f1227",
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
    evidence_status: "PARTIAL",
    suppressed: false,
    explanation: "average EPS 5.98 is above the baseline median 1.98",
    baseline_version: 1,
    policy_version: 1,
    transitions: [
      {
        from_state: "INSUFFICIENT_DATA",
        to_state: "CANDIDATE",
        occurred_at: "2026-08-02T18:36:39Z",
        bucket_start: "2026-08-02T18:36:00Z",
        reason: "first abnormal bucket",
        actor: "detector",
        observed_value: 5.98,
        expected_value: 1.98,
      },
      {
        from_state: "CANDIDATE",
        to_state: "OPEN",
        occurred_at: "2026-08-02T18:37:39Z",
        bucket_start: "2026-08-02T18:37:00Z",
        reason: "2 consecutive abnormal bucket(s) reached the confirmation threshold",
        actor: "detector",
        observed_value: 5.98,
        expected_value: 1.98,
      },
    ],
    explanation_package: livePackage(),
    detection: {
      reason: "average EPS 5.98 is above the baseline median 1.98",
      expected_low: 1.983,
      expected_high: 1.983,
      threshold: 3.5,
      baseline_sample_count: 8,
      baseline_completeness: 1,
      baseline_version: 1,
      observed_eps: 5.9833,
      expected_eps: 1.9833,
      observed_events: 359,
      expected_events: 119,
      absolute_delta_events: 240,
      bucket_seconds: 60,
      ratio: 3.0168,
      ratio_basis: null,
      robust_score_status: "DEGENERATE",
      robust_z: 20.168,
      fallback_bound: 1.9833,
    },
    ...over,
  };
}

function bucket(over: Partial<MetricBucket> = {}): MetricBucket {
  return {
    bucket_start: "2026-08-02T18:35:00Z",
    bucket_seconds: 60,
    event_count: 359,
    average_eps: 5.98,
    peak_eps: 6.5,
    completeness: "COMPLETE",
    last_event_at: "2026-08-02T18:35:59Z",
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderPage(
  detail: AnomalyDetail = anomaly(),
  buckets: MetricBucket[] = [bucket()],
) {
  vi.spyOn(api, "anomaly").mockResolvedValue(detail);
  vi.spyOn(api, "sourceMetrics").mockResolvedValue(buckets);
  return render(await AnomalyDetailPage({ params: Promise.resolve({ id: detail.id }) }));
}

describe("incident header", () => {
  it("has exactly one h1, naming the source", async () => {
    const { container } = await renderPage();
    const h1s = container.querySelectorAll("h1");
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent("LAB Phase A Firewall");
  });

  it("names the detector, state and severity", async () => {
    await renderPage();
    expect(screen.getByText("Volume spike")).toBeInTheDocument();
    expect(screen.getByText("RESOLVED")).toBeInTheDocument();
    expect(screen.getByText("MEDIUM")).toBeInTheDocument();
  });

  it("gives the lifecycle state the strong badge and severity a quiet one", async () => {
    await renderPage();
    expect(screen.getByText("RESOLVED").className).toContain("pill-strong");
    expect(screen.getByText("MEDIUM").className).toContain("pill-quiet");
  });

  it("says a still-running anomaly has not ended rather than inventing an end", async () => {
    await renderPage(anomaly({ anomaly_end: null, state: "OPEN", duration_seconds: null }));
    expect(screen.getByText(/still running/)).toBeInTheDocument();
  });

  it("links back to the list and to the source", async () => {
    await renderPage();
    expect(screen.getByRole("link", { name: /All anomalies/ })).toHaveAttribute(
      "href",
      "/anomalies",
    );
    expect(screen.getByRole("link", { name: /View source behavior/ })).toHaveAttribute(
      "href",
      "/behavior/sources/s-1",
    );
  });
});

describe("deterministic summary", () => {
  it("states the change and the strongest contributors", async () => {
    await renderPage();
    expect(
      screen.getByText(
        /Event volume increased from an expected 1\.98 EPS to 5\.98 EPS\. The largest observed contributors were Firewall - Deny traffic, destination port 445 and source IP 203\.0\.113\.50\./,
      ),
    ).toBeInTheDocument();
  });

  it("summarises a drop as a reduction", async () => {
    await renderPage(
      anomaly({
        anomaly_type: "VOLUME_DROP",
        observed_value: 1,
        expected_value: 4.983,
        deviation_ratio: 0.2,
        explanation_package: livePackage({
          dimensions: [
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
          ],
        }),
      }),
    );
    expect(
      screen.getByText(
        /Event volume decreased from an expected 4\.98 EPS to 1\.00 EPS\. The largest observed reduction was in Firewall - Permit traffic\./,
      ),
    ).toBeInTheDocument();
  });

  it("summarises silence without a contributor comparison", async () => {
    await renderPage(
      anomaly({
        anomaly_type: "NO_EVENTS",
        observed_value: 0,
        expected_value: 5,
        deviation_ratio: null,
        evidence_status: "NOT_REQUESTED",
        explanation_package: null,
      }),
    );
    expect(
      screen.getByText(
        /No events were observed for a source that normally produces approximately 5\.00 EPS\./,
      ),
    ).toBeInTheDocument();
  });

  it("falls back honestly when evidence is absent", async () => {
    await renderPage(
      anomaly({ explanation_package: null, evidence_status: "NOT_REQUESTED" }),
    );
    expect(
      screen.getByText(/No contributor evidence has been requested for this anomaly\./),
    ).toBeInTheDocument();
  });

  it("adds the partial-evidence caveat", async () => {
    await renderPage();
    expect(
      screen.getByText("Some QRadar fields were unavailable or truncated."),
    ).toBeInTheDocument();
  });
});

describe("primary metrics", () => {
  it("shows exactly four", async () => {
    const { container } = await renderPage();
    const primary = container.querySelector(".grid-4")!;
    expect(primary.querySelectorAll(".card")).toHaveLength(4);
  });

  it("shows observed, expected, deviation and duration", async () => {
    const { container } = await renderPage();
    const primary = container.querySelector(".grid-4") as HTMLElement;
    expect(within(primary).getByText("Observed")).toBeInTheDocument();
    expect(within(primary).getByText("5.98")).toBeInTheDocument();
    expect(within(primary).getByText("Expected")).toBeInTheDocument();
    expect(within(primary).getByText("1.98")).toBeInTheDocument();
    expect(within(primary).getByText("3.02x")).toBeInTheDocument();
    expect(within(primary).getByText("4m")).toBeInTheDocument();
  });

  it("keeps the robust z-score out of the primary row", async () => {
    const { container } = await renderPage();
    const primary = container.querySelector(".grid-4") as HTMLElement;
    expect(within(primary).queryByText(/Robust z/i)).toBeNull();
    expect(within(primary).queryByText("20.17")).toBeNull();
  });

  it("shows confidence as a secondary line, not a fifth card", async () => {
    const { container } = await renderPage();
    const primary = container.querySelector(".grid-4") as HTMLElement;
    expect(within(primary).queryByText(/Confidence/i)).toBeNull();
    expect(container.querySelector(".confidence-line")).toHaveTextContent("0.46");
  });

  it("caveats a capped confidence where it is read", async () => {
    await renderPage();
    expect(
      screen.getByText(/Confidence limited because baseline variability is zero/),
    ).toBeInTheDocument();
  });

  it("adds no caveat when MAD is sound", async () => {
    const a = anomaly();
    await renderPage(
      anomaly({ detection: { ...a.detection!, robust_score_status: "OK" } }),
    );
    expect(screen.queryByText(/Confidence limited because/)).toBeNull();
  });
});

describe("first viewport", () => {
  it("carries no version, threshold or provenance table", async () => {
    // All of these previously sat above the timeline.
    await renderPage();
    expect(screen.queryByText("Detection policy version")).toBeNull();
    expect(screen.queryByText("Robust z threshold")).toBeNull();
    expect(screen.queryByText("Comparison strategy")).toBeNull();
    expect(screen.queryByText("Evidence schema version")).toBeNull();
  });

  it("renders no raw AQL", async () => {
    await renderPage();
    expect(screen.queryByText(/SELECT qid AS/)).toBeNull();
  });

  it("renders at most one contributor table", async () => {
    // Ten dimensions previously produced ten contributor tables plus ten
    // metadata tables, 26 tables in one scroll.
    await renderPage();
    expect(screen.getAllByRole("table")).toHaveLength(1);
  });

  it("does not print the detector reason twice", async () => {
    // `explanation` and `detection.reason` are the same string in the API and
    // were both rendered verbatim.
    await renderPage();
    expect(
      screen.queryAllByText(/average EPS 5\.98 is above the baseline median/),
    ).toHaveLength(0);
  });
});

describe("timeline", () => {
  it("holds the investigation hierarchy in one fixed order", async () => {
    // Deterministic summary and the four metrics first, then the timeline,
    // then what changed, and only then the technical detail behind its tab.
    // Asserted by accessible name so a class rename cannot silently reorder
    // the page.
    const { container } = await renderPage();

    expect(
      screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent),
    ).toEqual(["Timeline", "What changed", "Investigation detail"]);

    const h1 = screen.getByRole("heading", { level: 1 });
    const metrics = container.querySelector(".grid-4")!;
    const timeline = screen.getByRole("heading", { level: 2, name: "Timeline" });

    const follows = Node.DOCUMENT_POSITION_FOLLOWING;
    expect(h1.compareDocumentPosition(metrics) & follows).toBeTruthy();
    expect(metrics.compareDocumentPosition(timeline) & follows).toBeTruthy();
  });

  it("keeps the raw technical material out of the initial render", async () => {
    // The page this replaced opened with the provenance table and the AQL
    // visible. Both are still reachable; neither is the first thing read.
    await renderPage();

    expect(screen.queryByText(/SELECT /i)).toBeNull();
    // At most one contributor table on arrival, and no second one hidden
    // behind the first.
    expect(screen.getAllByRole("table").length).toBeLessThanOrEqual(1);
  });

  it("appears before the investigation detail", async () => {
    const { container } = await renderPage();
    const headings = Array.from(container.querySelectorAll("h2")).map(
      (h) => h.textContent,
    );
    expect(headings.indexOf("Timeline")).toBeLessThan(
      headings.indexOf("Investigation detail"),
    );
  });

  it("appears after the primary metrics", async () => {
    const { container } = await renderPage();
    const grid = container.querySelector(".grid-4")!;
    const chart = container.querySelector(".chart")!;
    expect(
      grid.compareDocumentPosition(chart) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("carries a text summary of what it shows", async () => {
    await renderPage(anomaly(), [
      bucket({ bucket_start: "2026-08-02T18:35:00Z", average_eps: 1.98 }),
      bucket({ bucket_start: "2026-08-02T18:36:00Z", average_eps: 5.98 }),
    ]);
    expect(
      screen.getByText(/Observed volume ranged from 1\.98 to 5\.98 EPS/),
    ).toBeInTheDocument();
    expect(screen.getByText(/now RESOLVED/)).toBeInTheDocument();
  });

  it("notes uncollected intervals in the summary", async () => {
    await renderPage(anomaly(), [
      bucket({ average_eps: 5.98 }),
      bucket({ bucket_start: "2026-08-02T18:36:00Z", completeness: "PARTIAL" }),
    ]);
    expect(screen.getByText(/1 interval was not fully collected/)).toBeInTheDocument();
  });

  it("reports a failed metric fetch as absent rather than empty", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(anomaly());
    vi.spyOn(api, "sourceMetrics").mockRejectedValue(new Error("ECONNREFUSED"));
    render(await AnomalyDetailPage({ params: Promise.resolve({ id: "b29f1227" }) }));

    expect(screen.getByRole("alert")).toHaveTextContent(/absent rather than empty/i);
  });
});

describe("what changed", () => {
  it("shows three dimension-diverse headline contributors", async () => {
    const { container } = await renderPage();
    const cards = container.querySelectorAll(".contributor-card");
    expect(cards).toHaveLength(3);
    expect(cards[0]).toHaveTextContent("Event name");
    expect(cards[1]).toHaveTextContent("Destination port");
    expect(cards[2]).toHaveTextContent("Source IP");
  });

  it("shows the baseline, anomaly, delta and share for each", async () => {
    const { container } = await renderPage();
    const port = container.querySelectorAll(".contributor-card")[1];
    expect(port).toHaveTextContent("445");
    expect(port).toHaveTextContent("47 → 528");
    expect(port).toHaveTextContent("+481");
    expect(port).toHaveTextContent("95.9%");
  });

  it("offers a route into the full evidence", async () => {
    await renderPage();
    expect(screen.getByRole("link", { name: "View all evidence" })).toBeInTheDocument();
  });

  it("explains a zero-event interval rather than showing an empty panel", async () => {
    await renderPage(
      anomaly({
        anomaly_type: "NO_EVENTS",
        explanation_package: null,
        evidence_status: "NOT_REQUESTED",
      }),
    );
    expect(
      screen.getByText(/No contributor comparison is available for a zero-event interval/),
    ).toBeInTheDocument();
  });
});

describe("evidence banner", () => {
  it("states the partial status once", async () => {
    await renderPage();
    expect(screen.getAllByText(/^PARTIAL\.$/)).toHaveLength(1);
  });

  it("warns about truncation once, globally, naming the dimension", async () => {
    await renderPage();
    const warning = screen.getByText(/Some results were limited to the top values/);
    expect(warning).toHaveTextContent("Source port");
  });
});

describe("tabs", () => {
  it("defaults to Evidence", async () => {
    await renderPage();
    expect(screen.getByRole("tab", { name: "Evidence" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("offers the three panels", async () => {
    await renderPage();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Evidence",
      "Lifecycle",
      "Technical details",
    ]);
  });

  it("shows the lifecycle transitions in its own tab", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByRole("tab", { name: "Lifecycle" }));

    expect(screen.getByText("first abnormal bucket")).toBeInTheDocument();
    expect(screen.getByText(/confirmation threshold/)).toBeInTheDocument();
  });

  it("keeps the lifecycle out of the document until selected", async () => {
    await renderPage();
    expect(screen.queryByText("first abnormal bucket")).toBeNull();
  });

  it("moves the technical detail behind its tab", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByRole("tab", { name: "Technical details" }));

    expect(screen.getByText("Detection thresholds")).toBeInTheDocument();
    expect(screen.getByText("Baseline and score details")).toBeInTheDocument();
    expect(screen.getByText("Evidence provenance")).toBeInTheDocument();
    expect(screen.getByText("Ariel queries")).toBeInTheDocument();
    expect(screen.getByText("Versions and collection metadata")).toBeInTheDocument();
  });

  it("keeps the AQL collapsed inside the technical tab", async () => {
    const user = userEvent.setup();
    const { container } = await renderPage();

    await user.click(screen.getByRole("tab", { name: "Technical details" }));

    const panels = container.querySelectorAll("details.code-panel");
    expect(panels).toHaveLength(1);
    expect(panels[0]).not.toHaveAttribute("open");
  });

  it("names the query and offers a labelled copy button", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByRole("tab", { name: "Technical details" }));

    expect(screen.getByText(/QID · anomaly/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: /Copy the QID · anomaly query to the clipboard/,
      }),
    ).toBeInTheDocument();
  });

  it("moves the robust z-score and both versions into the technical tab", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByRole("tab", { name: "Technical details" }));

    expect(screen.getByText("Robust z observed")).toBeInTheDocument();
    expect(screen.getByText("Detection policy version")).toBeInTheDocument();
    expect(screen.getAllByText("Baseline version").length).toBeGreaterThan(0);
    expect(screen.getByText("MAD status")).toBeInTheDocument();
  });
});

describe("evidence tab", () => {
  it("opens on the highest-priority collected dimension", async () => {
    await renderPage();
    expect(
      screen.getByRole("region", { name: "Event name contributors" }),
    ).toBeInTheDocument();
  });

  it("switches dimensions without adding a second table", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByRole("button", { name: /Source IP/ }));

    expect(screen.getAllByRole("table")).toHaveLength(1);
    expect(
      screen.getByRole("region", { name: "Source IP contributors" }),
    ).toBeInTheDocument();
  });

  it("shows the unavailable field as a panel, not an empty table", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByRole("button", { name: /Username/ }));

    expect(screen.queryByRole("table")).toBeNull();
    expect(
      screen.getByText(/field is not populated for this log source/),
    ).toBeInTheDocument();
  });

  it("withholds a truncated dimension's counts while keeping its rows", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByRole("button", { name: /Source port/ }));

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getAllByText("indeterminate")).toHaveLength(4);
  });
});

describe("secrets", () => {
  it("renders no credential or header from the provenance blob", async () => {
    // The component reads only the named fields, so a future addition to the
    // blob cannot leak through it.
    const user = userEvent.setup();
    const a = anomaly();
    const { container } = await renderPage(
      anomaly({
        explanation_package: livePackage({
          query_provenance: {
            queries: a.explanation_package!.query_provenance.queries,
            sec_token: "SECRET-TOKEN-VALUE",
            headers: { Authorization: "Bearer SECRET" },
          } as never,
        }),
      }),
    );

    await user.click(screen.getByRole("tab", { name: "Technical details" }));

    expect(container.textContent).not.toContain("SECRET-TOKEN-VALUE");
    expect(container.textContent).not.toContain("Bearer SECRET");
  });
});

describe("error states", () => {
  it("reports a missing anomaly", async () => {
    vi.spyOn(api, "anomaly").mockRejectedValue(new ApiError(404, "not found"));
    render(await AnomalyDetailPage({ params: Promise.resolve({ id: "nope" }) }));
    expect(screen.getByRole("alert")).toHaveTextContent(/does not exist/i);
  });

  it("reports a forbidden response as a permission problem", async () => {
    vi.spyOn(api, "anomaly").mockRejectedValue(new ApiError(403, "forbidden"));
    render(await AnomalyDetailPage({ params: Promise.resolve({ id: "x" }) }));
    expect(screen.getByRole("alert")).toHaveTextContent(/do not have permission/i);
  });

  it("reports an expired session on 401", async () => {
    vi.spyOn(api, "anomaly").mockRejectedValue(new ApiError(401, "unauthorized"));
    render(await AnomalyDetailPage({ params: Promise.resolve({ id: "x" }) }));
    expect(screen.getByRole("alert")).toHaveTextContent(/session has expired/i);
  });
});
