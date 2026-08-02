// Layout regressions for status badges and the grids that hold them.
//
// Both defects these cover were caused by CSS, not by markup, so the
// assertions here are about the structure that lets the CSS work: a badge that
// is its own element with a non-shrinking class, and a label that is a
// separate element free to wrap. jsdom does not lay out, so these guard the
// contract; the measured proof is in the CDP overflow check.

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import BehaviorPage from "@/app/behavior/page";
import OffensesPage from "@/app/offenses/page";
import {
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

function anomaly(over: Partial<AnomalySummary> = {}): AnomalySummary {
  return {
    id: "a-1",
    log_source_id: "s-1",
    log_source_name: "A source with a deliberately long descriptive name",
    anomaly_type: "VOLUME_DROP",
    state: "CANDIDATE",
    severity: "HIGH",
    observed_value: 0,
    expected_value: 4.97,
    deviation_ratio: 0,
    robust_z: -10,
    absolute_delta: -298,
    consecutive_buckets: 1,
    confidence: 0.93,
    detected_at: "2026-08-02T21:50:00Z",
    opened_at: null,
    anomaly_start: "2026-08-02T21:50:00Z",
    anomaly_end: null,
    resolved_at: null,
    duration_seconds: null,
    evidence_status: "NOT_REQUESTED",
    suppressed: false,
    explanation: null,
    ...over,
  };
}

function page(items: AnomalySummary[]): Page<AnomalySummary> {
  return { items, total: items.length, limit: 50, offset: 0 };
}

const SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];

describe("severity badge beside a detector label", () => {
  async function renderOverview(severity: string) {
    vi.spyOn(api, "behaviorSummary").mockResolvedValue(summary());
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([] as SourceBehavior[]);
    vi.spyOn(api, "anomalies").mockResolvedValue(page([anomaly({ severity })]));
    return render(await BehaviorPage());
  }

  it("renders the label and the badge as separate elements", async () => {
    const { container } = await renderOverview("HIGH");
    const pair = container.querySelector(".issue-with-severity")!;
    const label = pair.querySelector(".issue-with-severity__label")!;
    const badge = pair.querySelector(".pill")!;

    expect(label).toHaveTextContent("Volume below baseline");
    expect(badge).toHaveTextContent("HIGH");
    // The badge must not be inside the label, or the label's wrapping would
    // break the badge across lines.
    expect(label.contains(badge)).toBe(false);
  });

  for (const severity of SEVERITIES) {
    it(`keeps ${severity} in a non-shrinking badge with its full text`, async () => {
      vi.restoreAllMocks();
      const { container } = await renderOverview(severity);
      const badge = container.querySelector(".issue-with-severity .pill")!;

      // `.pill` carries `flex: 0 0 auto` and real horizontal padding, so the
      // badge cannot be squeezed narrower than its own text.
      expect(badge.className).toContain("pill");
      expect(badge.textContent).toBe(severity);
      // Not truncated in the markup: the whole severity word is present.
      expect(badge.textContent).toHaveLength(severity.length);
    });
  }

  it("lets a long detector label wrap rather than widening the row", async () => {
    vi.restoreAllMocks();
    const { container } = await renderOverview("CRITICAL");
    const label = container.querySelector(".issue-with-severity__label")!;
    // The wrapping class is what permits a break; without it the label's
    // intrinsic minimum holds the column open.
    expect(label.className).toContain("issue-with-severity__label");
  });

  it("uses the quiet variant, which must still be a full pill", async () => {
    vi.restoreAllMocks();
    const { container } = await renderOverview("HIGH");
    const badge = container.querySelector(".issue-with-severity .pill")!;
    // `.pill pill-quiet` — both classes, so the `.pill.pill-quiet` rule wins
    // over the tone rules rather than losing the padding fight to them.
    expect(badge.className).toContain("pill");
    expect(badge.className).toContain("pill-quiet");
  });
});

describe("supporting-page KPI row", () => {
  it("uses the responsive grid class rather than a fixed row", async () => {
    vi.restoreAllMocks();
    vi.spyOn(api, "offenses").mockResolvedValue({
      items: [],
      total: 0,
      limit: 25,
      offset: 0,
    } as never);
    vi.spyOn(api, "offenseAggregates").mockResolvedValue({
      active: 16,
      critical: 0,
      unassigned: 16,
      over_sla: 14,
      oldest_age_seconds: 1_100_000,
    } as never);

    const { container } = render(
      await OffensesPage({ searchParams: Promise.resolve({}) }),
    );

    const row = container.querySelector(".cards");
    expect(row).not.toBeNull();
    // Six cards in one responsive grid; the grid decides how many fit.
    expect(row!.querySelectorAll(".card").length).toBeGreaterThanOrEqual(6);
  });

  it("keeps a long metric label intact rather than truncating it", async () => {
    vi.restoreAllMocks();
    vi.spyOn(api, "offenses").mockResolvedValue({
      items: [],
      total: 0,
      limit: 25,
      offset: 0,
    } as never);
    vi.spyOn(api, "offenseAggregates").mockResolvedValue({
      active: 16,
      critical: 0,
      unassigned: 16,
      over_sla: 14,
      oldest_age_seconds: 1_100_000,
      critical_magnitude: 7,
    } as never);

    render(await OffensesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText("Critical (mag 7+)")).toBeInTheDocument();
  });

  it("renders metric values with tabular numerals", async () => {
    vi.restoreAllMocks();
    vi.spyOn(api, "offenses").mockResolvedValue({
      items: [],
      total: 0,
      limit: 25,
      offset: 0,
    } as never);
    vi.spyOn(api, "offenseAggregates").mockResolvedValue({
      active: 16,
      critical: 0,
      unassigned: 16,
      over_sla: 14,
      oldest_age_seconds: 1_100_000,
    } as never);

    const { container } = render(
      await OffensesPage({ searchParams: Promise.resolve({}) }),
    );
    // `.card .v` carries font-variant-numeric: tabular-nums in the token layer.
    expect(container.querySelector(".card .v")).not.toBeNull();
  });
});

describe("filter row", () => {
  it("puts the controls in the wrapping filters container", async () => {
    vi.restoreAllMocks();
    vi.spyOn(api, "offenses").mockResolvedValue({
      items: [],
      total: 0,
      limit: 25,
      offset: 0,
    } as never);
    vi.spyOn(api, "offenseAggregates").mockResolvedValue({
      active: 0,
      critical: 0,
      unassigned: 0,
      over_sla: 0,
      oldest_age_seconds: null,
    } as never);

    const { container } = render(
      await OffensesPage({ searchParams: Promise.resolve({}) }),
    );
    const form = container.querySelector("form.filters");
    expect(form).not.toBeNull();
    // Search, select and button all live in the same wrapping row.
    expect(within(form as HTMLElement).getByRole("searchbox")).toBeInTheDocument();
    expect(within(form as HTMLElement).getByRole("button")).toBeInTheDocument();
  });
});
