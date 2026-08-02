import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DimensionSummary } from "./DimensionSummary";
import type { ExplanationDimension } from "@/lib/api";

function dim(over: Partial<ExplanationDimension> = {}): ExplanationDimension {
  return {
    dimension: "source_ip",
    availability: "AVAILABLE",
    detail: null,
    baseline_distinct_count: 5,
    anomaly_distinct_count: 42,
    cardinality_ratio: 8.4,
    new_value_count: 37,
    disappeared_value_count: 2,
    baseline_top_share: 0.33,
    anomaly_top_share: 0.68,
    truncated: false,
    contributors: [],
    ...over,
  };
}

describe("coverage statement", () => {
  it("lists one row per requested dimension", () => {
    render(
      <DimensionSummary
        dimensions={[
          dim({ dimension: "qid" }),
          dim({ dimension: "source_ip" }),
          dim({ dimension: "username", availability: "UNAVAILABLE" }),
        ]}
      />,
    );
    expect(screen.getByRole("row", { name: /QID/ })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /Source IP/ })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /Username/ })).toBeInTheDocument();
  });

  it("reports cardinality, ratio and concentration change", () => {
    render(<DimensionSummary dimensions={[dim()]} />);
    const row = screen.getByRole("row", { name: /Source IP/ });
    expect(row).toHaveTextContent("5");
    expect(row).toHaveTextContent("42");
    expect(row).toHaveTextContent("8.40x");
    expect(row).toHaveTextContent("33.0% → 68.0%");
  });

  it("names the dimensions that were not checked", () => {
    render(
      <DimensionSummary
        dimensions={[
          dim(),
          dim({ dimension: "username", availability: "UNAVAILABLE" }),
          dim({ dimension: "action", availability: "FAILED" }),
        ]}
      />,
    );
    const notice = screen.getByText(/dimensions were not checked/i).closest("div");
    expect(notice).toHaveTextContent("Username");
    expect(notice).toHaveTextContent("Action");
    expect(notice).toHaveTextContent(/No conclusion about them follows/i);
  });

  it("says nothing about unchecked dimensions when every one was collected", () => {
    render(<DimensionSummary dimensions={[dim()]} />);
    expect(screen.queryByText(/were not checked/i)).toBeNull();
  });
});

describe("unavailable rows", () => {
  const unavailable = dim({
    dimension: "username",
    availability: "UNAVAILABLE",
    detail: "field is not populated for this log source",
    baseline_distinct_count: null,
    anomaly_distinct_count: null,
    cardinality_ratio: null,
    new_value_count: 0,
    disappeared_value_count: 0,
    baseline_top_share: null,
    anomaly_top_share: null,
  });

  it("shows the availability and the sanitized reason", () => {
    render(<DimensionSummary dimensions={[unavailable]} />);
    const row = screen.getByRole("row", { name: /Username/ });
    expect(row).toHaveTextContent("UNAVAILABLE");
    expect(row).toHaveTextContent("field is not populated for this log source");
  });

  it("shows dashes rather than the backend's default zero counts", () => {
    render(<DimensionSummary dimensions={[unavailable]} />);
    const cells = within(screen.getByRole("row", { name: /Username/ })).getAllByRole("cell");
    // new_value_count and disappeared_value_count default to 0 server-side but
    // nothing was counted, so neither may be displayed as a measurement.
    expect(cells[6]).toHaveTextContent("—");
    expect(cells[7]).toHaveTextContent("—");
  });

  it("shows a genuine zero for a dimension that was collected", () => {
    render(<DimensionSummary dimensions={[dim({ new_value_count: 0 })]} />);
    const cells = within(screen.getByRole("row", { name: /Source IP/ })).getAllByRole("cell");
    expect(cells[6]).toHaveTextContent("0");
  });
});

describe("truncation", () => {
  it("notes a truncated dimension in the note column", () => {
    render(<DimensionSummary dimensions={[dim({ availability: "TRUNCATED", truncated: true })]} />);
    expect(screen.getByRole("row", { name: /Source IP/ })).toHaveTextContent("truncated");
  });

  // Under a value cap the new/disappeared counts are an artifact of the cap:
  // a value looks new only because it fell below the cap in the other window.
  // Rendering them as bare numbers would state a finding the query never
  // established, so they must read as em dashes.
  it("withholds new and disappeared counts for a truncated dimension", () => {
    render(
      <DimensionSummary
        dimensions={[
          dim({
            availability: "TRUNCATED",
            truncated: true,
            new_value_count: 20,
            disappeared_value_count: 20,
          }),
        ]}
      />,
    );
    const cells = within(screen.getByRole("row", { name: /Source IP/ })).getAllByRole("cell");
    expect(cells[6]).toHaveTextContent("—");
    expect(cells[7]).toHaveTextContent("—");
    expect(cells[6]).not.toHaveTextContent("20");
    expect(cells[7]).not.toHaveTextContent("20");
  });

  it("still reports counts for a dimension collected in full", () => {
    render(<DimensionSummary dimensions={[dim({ new_value_count: 37 })]} />);
    const cells = within(screen.getByRole("row", { name: /Source IP/ })).getAllByRole("cell");
    expect(cells[6]).toHaveTextContent("37");
  });
});
