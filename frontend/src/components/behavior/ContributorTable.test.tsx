import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ContributorTable } from "./ContributorTable";
import type { Contributor, ExplanationDimension } from "@/lib/api";

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

describe("an available dimension", () => {
  it("renders every contributor column", () => {
    render(<ContributorTable dimension={dimension()} />);
    const row = screen.getByRole("row", { name: /203\.0\.113\.50/ });
    const cells = within(row).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent("12");
    expect(cells[2]).toHaveTextContent("4,820");
    expect(cells[3]).toHaveTextContent("+4,808");
    expect(cells[4]).toHaveTextContent("+400.6%");
    expect(cells[5]).toHaveTextContent("2.0%");
    expect(cells[6]).toHaveTextContent("68.0%");
    expect(cells[7]).toHaveTextContent("68.0%");
    expect(cells[8]).toHaveTextContent("5 → 1");
  });

  it("orders contributors as the backend ranked them", () => {
    render(
      <ContributorTable
        dimension={dimension({
          contributors: [
            contributor({ value: "10.0.0.1", rank: 1 }),
            contributor({ value: "10.0.0.2", rank: 2 }),
            contributor({ value: "10.0.0.3", rank: 3 }),
          ],
        })}
      />,
    );
    const rows = screen.getAllByRole("row").slice(-3);
    expect(rows[0]).toHaveTextContent("10.0.0.1");
    expect(rows[1]).toHaveTextContent("10.0.0.2");
    expect(rows[2]).toHaveTextContent("10.0.0.3");
  });

  it("shows a QID's label alongside its raw value", () => {
    render(
      <ContributorTable
        dimension={dimension({
          dimension: "qid",
          contributors: [contributor({ value: "1000001", label: "Firewall Deny" })],
        })}
      />,
    );
    expect(screen.getByText(/Firewall Deny/)).toBeInTheDocument();
    expect(screen.getByText("(1000001)")).toBeInTheDocument();
  });

  it("says so when a collected dimension produced no contributor", () => {
    // Distinct from "not collected": here we did look.
    render(<ContributorTable dimension={dimension({ contributors: [] })} />);
    expect(screen.getByText(/no value stood out/i)).toBeInTheDocument();
  });

  it("warns when the contributor list is truncated", () => {
    render(<ContributorTable dimension={dimension({ truncated: true })} />);
    expect(screen.getByText(/top of the list, not the whole of it/i)).toBeInTheDocument();
  });
});

describe("badges", () => {
  it("marks a newly observed value", () => {
    render(
      <ContributorTable
        dimension={dimension({
          contributors: [contributor({ is_new: true, baseline_count: 0, percent_delta: null })],
        })}
      />,
    );
    expect(screen.getByText("new")).toBeInTheDocument();
  });

  it("marks a value that disappeared", () => {
    render(
      <ContributorTable
        dimension={dimension({
          contributors: [
            contributor({ is_disappeared: true, anomaly_count: 0, absolute_delta: -12 }),
          ],
        })}
      />,
    );
    expect(screen.getByText("disappeared")).toBeInTheDocument();
  });

  it("marks neither for an ordinary contributor", () => {
    render(<ContributorTable dimension={dimension()} />);
    expect(screen.queryByText("new")).toBeNull();
    expect(screen.queryByText("disappeared")).toBeNull();
  });
});

describe("zero and null", () => {
  it("renders a measured zero as 0", () => {
    render(
      <ContributorTable
        dimension={dimension({
          contributors: [contributor({ baseline_count: 0, anomaly_share: 0 })],
        })}
      />,
    );
    const row = screen.getByRole("row", { name: /203\.0\.113\.50/ });
    const cells = within(row).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent("0");
    expect(cells[6]).toHaveTextContent("0.0%");
  });

  it("renders a new value's absent percentage as a dash, never 0%", () => {
    // A value with no baseline has no percentage change. "0%" would assert
    // that it did not change, which is the opposite of the truth.
    render(
      <ContributorTable
        dimension={dimension({
          contributors: [contributor({ percent_delta: null, is_new: true })],
        })}
      />,
    );
    const row = screen.getByRole("row", { name: /203\.0\.113\.50/ });
    expect(within(row).getAllByRole("cell")[4]).toHaveTextContent("—");
  });

  it("renders an absent rank as a dash", () => {
    render(
      <ContributorTable
        dimension={dimension({
          contributors: [contributor({ baseline_rank: null, anomaly_rank: 1 })],
        })}
      />,
    );
    expect(screen.getByRole("row", { name: /203\.0\.113\.50/ })).toHaveTextContent("— → 1");
  });
});

describe("an unavailable dimension", () => {
  const unavailable = dimension({
    availability: "UNAVAILABLE",
    detail: "field is not populated for this log source",
    baseline_distinct_count: null,
    anomaly_distinct_count: null,
    cardinality_ratio: null,
    new_value_count: 0,
    disappeared_value_count: 0,
    baseline_top_share: null,
    anomaly_top_share: null,
    contributors: [],
  });

  it("is rendered rather than hidden", () => {
    // A hidden unavailable dimension reads to an analyst as one that was
    // checked and found clean.
    render(<ContributorTable dimension={unavailable} />);
    expect(screen.getByText("UNAVAILABLE")).toBeInTheDocument();
    expect(screen.getByLabelText("Source IP contributors")).toBeInTheDocument();
  });

  it("states that the field was not exposed by the DSM", () => {
    render(<ContributorTable dimension={unavailable} />);
    expect(
      screen.getByText(/not exposed by the QRadar event schema or DSM/i),
    ).toBeInTheDocument();
  });

  it("surfaces the sanitized reason", () => {
    render(<ContributorTable dimension={unavailable} />);
    expect(screen.getByText(/field is not populated/i)).toBeInTheDocument();
  });

  it("never renders its new or disappeared counts as zero", () => {
    // The backend defaults these to 0, but nothing was counted. Showing "0 new
    // values" would be a measurement that was never taken.
    render(<ContributorTable dimension={unavailable} />);
    const row = screen.getByRole("row", { name: /New values/ });
    const cells = within(row).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent("—");
    expect(cells[3]).toHaveTextContent("—");
  });

  it("does not claim that nothing stood out", () => {
    render(<ContributorTable dimension={unavailable} />);
    expect(screen.queryByText(/no value stood out/i)).toBeNull();
  });
});

describe("a failed dimension", () => {
  it("is rendered and marked as unchecked", () => {
    render(
      <ContributorTable
        dimension={dimension({
          availability: "FAILED",
          detail: "query timed out",
          contributors: [],
        })}
      />,
    );
    expect(screen.getByText("FAILED")).toBeInTheDocument();
    expect(screen.getByText(/did not complete/i)).toBeInTheDocument();
    expect(screen.getByText(/query timed out/i)).toBeInTheDocument();
    expect(screen.queryByText(/no value stood out/i)).toBeNull();
  });
});
