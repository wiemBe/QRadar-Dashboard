// Ported and extended from the removed DimensionSummary tests.
//
// The invariant that survives: the list of dimensions is the page's coverage
// statement and must be complete. Reading down it tells an analyst which
// fields the conclusion actually rests on — a question the contributor tables
// alone cannot answer, because a dimension with no contributors and a
// dimension that was never queried both show no rows.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DimensionExplorer } from "./DimensionExplorer";
import type { Contributor, ExplanationDimension } from "@/lib/api";

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
  const dim = over.dimension ?? "event_name";
  return {
    dimension: dim,
    availability: "AVAILABLE",
    contributors: [contributor({ dimension: dim, value: `${dim}-value` })],
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

/** The ten dimensions the live Phase A policy requests. */
const liveShape = [
  dimension({ dimension: "event_name" }),
  dimension({ dimension: "destination_port" }),
  dimension({ dimension: "source_ip" }),
  dimension({ dimension: "destination_ip" }),
  dimension({ dimension: "action" }),
  dimension({ dimension: "protocol" }),
  dimension({ dimension: "qid" }),
  dimension({ dimension: "category" }),
  dimension({ dimension: "source_port", availability: "TRUNCATED", truncated: true }),
  dimension({ dimension: "username", availability: "UNAVAILABLE", contributors: [] }),
];

describe("one table at a time", () => {
  it("renders exactly one contributor table for ten dimensions", () => {
    // The page previously rendered ten, one after another.
    render(<DimensionExplorer dimensions={liveShape} />);
    expect(screen.getAllByRole("table")).toHaveLength(1);
  });

  it("lists every dimension, including the ones never collected", () => {
    render(<DimensionExplorer dimensions={liveShape} />);
    for (const label of [
      "Event name",
      "Destination port",
      "Source IP",
      "Destination IP",
      "Action",
      "Protocol",
      "QID",
      "Category",
      "Source port",
      "Username",
    ]) {
      expect(screen.getByRole("button", { name: new RegExp(label) })).toBeInTheDocument();
    }
  });

  it("shows each dimension's status in the list", () => {
    render(<DimensionExplorer dimensions={liveShape} />);
    const username = screen.getByRole("button", { name: /Username/ });
    expect(within(username).getByText("UNAVAILABLE")).toBeInTheDocument();
    const sourcePort = screen.getByRole("button", { name: /Source port/ });
    expect(within(sourcePort).getByText("TRUNCATED")).toBeInTheDocument();
  });

  it("names the dimensions that were not checked", () => {
    render(<DimensionExplorer dimensions={liveShape} />);
    expect(screen.getByText(/1 dimension was not checked: Username/)).toBeInTheDocument();
    expect(screen.getByText(/No conclusion about it follows/)).toBeInTheDocument();
  });
});

describe("default selection", () => {
  it("opens on the highest-priority fully collected dimension", () => {
    render(<DimensionExplorer dimensions={liveShape} />);
    expect(
      screen.getByRole("region", { name: "Event name contributors" }),
    ).toBeInTheDocument();
  });

  it("prefers a complete dimension over a capped one", () => {
    render(
      <DimensionExplorer
        dimensions={[
          dimension({ dimension: "event_name", availability: "TRUNCATED", truncated: true }),
          dimension({ dimension: "source_ip" }),
        ]}
      />,
    );
    expect(
      screen.getByRole("region", { name: "Source IP contributors" }),
    ).toBeInTheDocument();
  });

  it("falls back to a capped dimension when nothing was collected in full", () => {
    render(
      <DimensionExplorer
        dimensions={[
          dimension({ dimension: "username", availability: "UNAVAILABLE", contributors: [] }),
          dimension({ dimension: "source_port", availability: "TRUNCATED", truncated: true }),
        ]}
      />,
    );
    expect(
      screen.getByRole("region", { name: "Source port contributors" }),
    ).toBeInTheDocument();
  });

  it("still shows a status panel when nothing was collected at all", () => {
    render(
      <DimensionExplorer
        dimensions={[
          dimension({ dimension: "username", availability: "UNAVAILABLE", contributors: [] }),
        ]}
      />,
    );
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getAllByText(/has not been checked/i).length).toBeGreaterThan(0);
  });

  it("says so when no dimension analysis exists at all", () => {
    render(<DimensionExplorer dimensions={[]} />);
    expect(screen.getByText(/No dimension analysis is stored/i)).toBeInTheDocument();
  });
});

describe("switching dimensions", () => {
  it("swaps the visible table without adding a second one", async () => {
    const user = userEvent.setup();
    render(<DimensionExplorer dimensions={liveShape} />);

    await user.click(screen.getByRole("button", { name: /Source IP/ }));

    expect(
      screen.getByRole("region", { name: "Source IP contributors" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Event name contributors" })).toBeNull();
    expect(screen.getAllByRole("table")).toHaveLength(1);
  });

  it("marks the selected dimension as current", async () => {
    const user = userEvent.setup();
    render(<DimensionExplorer dimensions={liveShape} />);

    const target = screen.getByRole("button", { name: /Destination port/ });
    await user.click(target);
    expect(target).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: /Event name/ })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("shows an unavailable dimension's panel rather than an empty table", async () => {
    const user = userEvent.setup();
    render(<DimensionExplorer dimensions={liveShape} />);

    await user.click(screen.getByRole("button", { name: /Username/ }));

    expect(screen.queryByRole("table")).toBeNull();
    expect(
      screen.getByText(/No conclusion follows from the absence of contributors/i),
    ).toBeInTheDocument();
  });

  it("keeps a truncated dimension's rows while withholding its counts", async () => {
    const user = userEvent.setup();
    render(<DimensionExplorer dimensions={liveShape} />);

    await user.click(screen.getByRole("button", { name: /Source port/ }));

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getAllByText("indeterminate")).toHaveLength(4);
  });

  it("selects with the keyboard, since the selector is real buttons", async () => {
    const user = userEvent.setup();
    render(<DimensionExplorer dimensions={liveShape} />);

    const target = screen.getByRole("button", { name: /Action/ });
    target.focus();
    await user.keyboard("{Enter}");

    expect(
      screen.getByRole("region", { name: "Action contributors" }),
    ).toBeInTheDocument();
  });
});
