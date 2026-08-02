import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BehaviorPage from "./page";
import { ApiError, api, type BehaviorSummary, type SourceBehavior } from "@/lib/api";

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

const anomaly = {
  id: "a-1",
  log_source_id: "11111111-1111-1111-1111-111111111111",
  log_source_name: "LAB Firewall",
  anomaly_type: "VOLUME_SPIKE",
  state: "OPEN" as const,
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
  evidence_status: "COMPLETE" as const,
  suppressed: false,
  explanation: null,
};

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderPage() {
  render(await BehaviorPage());
}

describe("summary cards", () => {
  it("reports the fleet counters", async () => {
    vi.spyOn(api, "behaviorSummary").mockResolvedValue(
      summary({ open_anomalies: 3, spikes: 2, drops: 1, monitored_sources: 12 }),
    );
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    await renderPage();

    expect(screen.getByText("Open anomalies").nextSibling).toHaveTextContent("3");
    expect(screen.getByText("Volume spikes").nextSibling).toHaveTextContent("2");
    expect(screen.getByText("Volume drops").nextSibling).toHaveTextContent("1");
  });

  it("counts evidence backlog and failures separately", async () => {
    vi.spyOn(api, "behaviorSummary").mockResolvedValue(
      summary({ evidence_pending: 4, evidence_failed: 2 }),
    );
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    await renderPage();

    expect(screen.getByText("Evidence pending").nextSibling).toHaveTextContent("4");
    expect(screen.getByText("Evidence failed").nextSibling).toHaveTextContent("2");
  });
});

describe("insufficient data", () => {
  it("warns that unbaselined sources are not being judged", async () => {
    vi.spyOn(api, "behaviorSummary").mockResolvedValue(
      summary({ insufficient_data_sources: 7, monitored_sources: 12 }),
    );
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    await renderPage();

    expect(screen.getByText(/7 of 12 monitored sources/i)).toBeInTheDocument();
    expect(
      screen.getByText(/absence of a verdict, not a clean bill of health/i),
    ).toBeInTheDocument();
  });

  it("never paints the unbaselined count as healthy", async () => {
    vi.spyOn(api, "behaviorSummary").mockResolvedValue(
      summary({ insufficient_data_sources: 7 }),
    );
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    await renderPage();

    const value = screen.getByText("Not yet baselined").nextSibling as HTMLElement;
    expect(value.className).not.toContain("ok");
  });

  it("omits the warning when every source has a baseline", async () => {
    vi.spyOn(api, "behaviorSummary").mockResolvedValue(
      summary({ insufficient_data_sources: 0, monitored_sources: 12 }),
    );
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    await renderPage();

    expect(screen.queryByText(/not being judged/i)).toBeNull();
  });

  it("shows 'still learning' instead of an expected EPS of 0", async () => {
    // An unbaselined source has no expectation. A 0 would invent one and make
    // the observed value look like a spike against it.
    vi.spyOn(api, "behaviorSummary").mockResolvedValue(summary());
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([
      source({ state: "INSUFFICIENT_DATA", expected_eps: null, deviation_ratio: null }),
    ]);
    await renderPage();

    const row = screen.getByRole("row", { name: /LAB Firewall/ });
    expect(within(row).getByText("still learning")).toBeInTheDocument();
  });
});

describe("links", () => {
  it("links an active anomaly count to its filtered list", async () => {
    vi.spyOn(api, "behaviorSummary").mockResolvedValue(summary());
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([source()]);
    await renderPage();

    const row = screen.getByRole("row", { name: /LAB Firewall/ });
    expect(within(row).getByRole("link", { name: "1" })).toHaveAttribute(
      "href",
      "/anomalies?log_source_id=11111111-1111-1111-1111-111111111111&active_only=true",
    );
  });

  it("does not link a source with no active anomaly", async () => {
    vi.spyOn(api, "behaviorSummary").mockResolvedValue(summary());
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([
      source({ open_anomaly_count: 0, state: "NORMAL" }),
    ]);
    await renderPage();

    const row = screen.getByRole("row", { name: /LAB Firewall/ });
    expect(within(row).queryByRole("link", { name: "0" })).toBeNull();
  });

  it("links each highest-deviation row to its investigation", async () => {
    vi.spyOn(api, "behaviorSummary").mockResolvedValue(
      summary({ highest_deviation: [anomaly] }),
    );
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    await renderPage();

    expect(screen.getAllByRole("link", { name: "Investigate" })[0]).toHaveAttribute(
      "href",
      "/anomalies/a-1",
    );
  });
});

describe("empty states", () => {
  it("says no source is monitored rather than showing an empty table", async () => {
    vi.spyOn(api, "behaviorSummary").mockResolvedValue(summary());
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    await renderPage();

    expect(screen.getByText(/No monitored log sources/i)).toBeInTheDocument();
  });

  it("says nothing has been resolved yet", async () => {
    vi.spyOn(api, "behaviorSummary").mockResolvedValue(summary());
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    await renderPage();

    expect(screen.getByText(/No anomalies have been resolved yet/i)).toBeInTheDocument();
  });
});

describe("error states", () => {
  it("reports an unreachable backend as a failure, not as an empty fleet", async () => {
    vi.spyOn(api, "behaviorSummary").mockRejectedValue(new Error("ECONNREFUSED"));
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    await renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent(/backend may be unreachable/i);
    expect(screen.queryByText("Open anomalies")).toBeNull();
  });

  it("reports a forbidden response as a permission problem", async () => {
    vi.spyOn(api, "behaviorSummary").mockRejectedValue(new ApiError(403, "forbidden"));
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    await renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent(/do not have permission/i);
  });

  it("reports an expired session on 401 without rendering any anomaly data", async () => {
    vi.spyOn(api, "behaviorSummary").mockRejectedValue(new ApiError(401, "unauthorized"));
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([source()]);
    await renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent(/session has expired/i);
    expect(screen.queryByText("LAB Firewall")).toBeNull();
  });
});
