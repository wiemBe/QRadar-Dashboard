import { describe, expect, it } from "vitest";

import type { Contributor, ExplanationDimension, ExplanationPackage } from "./api";
import {
  contributorDisplayValue,
  contributorStrength,
  defaultDimension,
  dimensionPriority,
  selectHeadlineContributors,
  strongestContributor,
} from "./contributors";

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
    status: "COMPLETE",
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

describe("contributorStrength", () => {
  it("ranks by magnitude, so a drop's negative share is not ranked last", () => {
    expect(contributorStrength(contributor({ contribution_share: -0.9 }))).toBe(0.9);
  });

  it("falls back to the absolute delta when no share was recorded", () => {
    expect(
      contributorStrength(
        contributor({ contribution_share: null, absolute_delta: -451 }),
      ),
    ).toBe(451);
  });

  it("treats a contributor with no share and no movement as no strength", () => {
    expect(
      contributorStrength(
        contributor({ contribution_share: null, absolute_delta: 0 }),
      ),
    ).toBe(0);
  });
});

describe("strongestContributor", () => {
  it("picks the largest mover within a dimension", () => {
    const d = dimension({
      contributors: [
        contributor({ value: "small", contribution_share: 0.1 }),
        contributor({ value: "big", contribution_share: 0.9 }),
      ],
    });
    expect(strongestContributor(d)?.value).toBe("big");
  });

  it("picks the largest reduction on a drop", () => {
    const d = dimension({
      contributors: [
        contributor({ value: "small", contribution_share: -0.1 }),
        contributor({ value: "big", contribution_share: -0.8 }),
      ],
    });
    expect(strongestContributor(d)?.value).toBe("big");
  });

  it("returns nothing for a dimension that was never collected", () => {
    expect(
      strongestContributor(dimension({ availability: "UNAVAILABLE", contributors: [] })),
    ).toBeNull();
    expect(
      strongestContributor(
        dimension({ availability: "FAILED", contributors: [contributor()] }),
      ),
    ).toBeNull();
  });

  it("is stable when two contributors tie", () => {
    const d = dimension({
      contributors: [
        contributor({ value: "first", contribution_share: 0.5 }),
        contributor({ value: "second", contribution_share: 0.5 }),
      ],
    });
    expect(strongestContributor(d)?.value).toBe("first");
  });
});

describe("selectHeadlineContributors", () => {
  // The failure this rule exists to prevent: three source IPs as the three
  // headline findings, which restates one finding three times and never says
  // what kind of traffic it was.
  it("takes at most one contributor per dimension", () => {
    const picks = selectHeadlineContributors(
      packaged([
        dimension({
          dimension: "source_ip",
          contributors: [
            contributor({ dimension: "source_ip", value: "a", contribution_share: 0.99 }),
            contributor({ dimension: "source_ip", value: "b", contribution_share: 0.98 }),
            contributor({ dimension: "source_ip", value: "c", contribution_share: 0.97 }),
          ],
        }),
        dimension({
          dimension: "event_name",
          contributors: [contributor({ dimension: "event_name", value: "Deny" })],
        }),
      ]),
    );
    expect(picks).toHaveLength(2);
    expect(picks.map((p) => p.dimension)).toEqual(["event_name", "source_ip"]);
  });

  it("orders by dimension priority, not by raw contribution", () => {
    // Protocol has the higher share, but event name tells the analyst more.
    const picks = selectHeadlineContributors(
      packaged([
        dimension({
          dimension: "protocol",
          contributors: [
            contributor({ dimension: "protocol", value: "6", contribution_share: 1 }),
          ],
        }),
        dimension({
          dimension: "event_name",
          contributors: [
            contributor({ dimension: "event_name", value: "Deny", contribution_share: 0.99 }),
          ],
        }),
      ]),
    );
    expect(picks[0].dimension).toBe("event_name");
  });

  it("returns at most three", () => {
    const picks = selectHeadlineContributors(
      packaged(
        ["event_name", "destination_port", "source_ip", "destination_ip", "action"].map(
          (dim) =>
            dimension({
              dimension: dim,
              contributors: [contributor({ dimension: dim, value: dim })],
            }),
        ),
      ),
    );
    expect(picks).toHaveLength(3);
    expect(picks.map((p) => p.dimension)).toEqual([
      "event_name",
      "destination_port",
      "source_ip",
    ]);
  });

  it("excludes dimensions that were never successfully queried", () => {
    const picks = selectHeadlineContributors(
      packaged([
        dimension({ dimension: "username", availability: "UNAVAILABLE", contributors: [] }),
        dimension({
          dimension: "qid",
          availability: "FAILED",
          contributors: [contributor({ dimension: "qid", value: "x" })],
        }),
      ]),
    );
    expect(picks).toHaveLength(0);
  });

  it("includes a truncated dimension but marks it as capped", () => {
    const picks = selectHeadlineContributors(
      packaged([
        dimension({
          dimension: "source_port",
          availability: "TRUNCATED",
          truncated: true,
          contributors: [contributor({ dimension: "source_port", value: "18819" })],
        }),
      ]),
    );
    expect(picks).toHaveLength(1);
    expect(picks[0].truncated).toBe(true);
  });

  it("returns nothing when there is no package at all", () => {
    expect(selectHeadlineContributors(null)).toEqual([]);
  });
});

describe("contributorDisplayValue", () => {
  it("prefers the resolved label, which is what means something", () => {
    // Protocol 6 is TCP; QID 114500042 is "Firewall - Deny".
    expect(contributorDisplayValue(contributor({ value: "6", label: "TCP" }))).toBe("TCP");
  });

  it("falls back to the raw value when there is no label", () => {
    expect(contributorDisplayValue(contributor({ value: "445", label: null }))).toBe("445");
  });

  it("ignores a blank label rather than rendering emptiness", () => {
    expect(contributorDisplayValue(contributor({ value: "445", label: "  " }))).toBe("445");
  });
});

describe("defaultDimension", () => {
  it("opens on the highest-priority fully collected dimension", () => {
    const d = defaultDimension([
      dimension({ dimension: "source_ip" }),
      dimension({ dimension: "event_name" }),
    ]);
    expect(d?.dimension).toBe("event_name");
  });

  it("prefers a complete dimension over a capped one", () => {
    const d = defaultDimension([
      dimension({
        dimension: "event_name",
        availability: "TRUNCATED",
        truncated: true,
      }),
      dimension({ dimension: "source_ip", availability: "AVAILABLE" }),
    ]);
    expect(d?.dimension).toBe("source_ip");
  });

  it("falls back to a capped dimension when nothing was fully collected", () => {
    const d = defaultDimension([
      dimension({ dimension: "username", availability: "UNAVAILABLE", contributors: [] }),
      dimension({ dimension: "source_port", availability: "TRUNCATED", truncated: true }),
    ]);
    expect(d?.dimension).toBe("source_port");
  });

  it("still returns a dimension when none was collected, so its status shows", () => {
    const d = defaultDimension([
      dimension({ dimension: "username", availability: "UNAVAILABLE", contributors: [] }),
    ]);
    expect(d?.dimension).toBe("username");
  });

  it("returns null only when there are no dimensions", () => {
    expect(defaultDimension([])).toBeNull();
  });
});

describe("dimensionPriority", () => {
  it("ranks meaningful fields above identifiers and noise", () => {
    expect(dimensionPriority("event_name")).toBeLessThan(dimensionPriority("qid"));
    expect(dimensionPriority("destination_port")).toBeLessThan(
      dimensionPriority("source_port"),
    );
  });

  it("puts an unknown dimension last rather than first", () => {
    expect(dimensionPriority("something_new")).toBeGreaterThan(
      dimensionPriority("username"),
    );
  });
});
