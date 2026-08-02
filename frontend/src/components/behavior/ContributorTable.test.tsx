import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ContributorTable } from "./ContributorTable";
import type { Contributor, ExplanationDimension } from "@/lib/api";

function contributor(over: Partial<Contributor> = {}): Contributor {
  return {
    dimension: "destination_port",
    value: "445",
    label: null,
    baseline_count: 47,
    anomaly_count: 528,
    absolute_delta: 481,
    percent_delta: 1023.4,
    anomaly_share: 0.73,
    baseline_share: 0.19,
    contribution_share: 0.959,
    baseline_rank: 3,
    anomaly_rank: 1,
    rank: 1,
    is_new: false,
    is_disappeared: false,
    ...over,
  };
}

function dimension(over: Partial<ExplanationDimension> = {}): ExplanationDimension {
  return {
    dimension: "destination_port",
    availability: "AVAILABLE",
    contributors: [contributor()],
    baseline_distinct_count: 5,
    anomaly_distinct_count: 6,
    cardinality_ratio: 1.2,
    baseline_top_share: 0.19,
    anomaly_top_share: 0.73,
    new_value_count: 1,
    disappeared_value_count: 0,
    truncated: false,
    detail: null,
    ...over,
  };
}

describe("default columns", () => {
  it("shows five data columns rather than the previous ten", () => {
    render(<ContributorTable dimension={dimension()} />);
    const headers = screen
      .getAllByRole("columnheader")
      .map((h) => h.textContent?.trim());
    expect(headers).toEqual([
      "Value",
      "Baseline",
      "Anomaly",
      "Delta",
      "Contribution",
      "Row detail",
    ]);
  });

  it("marks every header as a column header", () => {
    render(<ContributorTable dimension={dimension()} />);
    for (const th of screen.getAllByRole("columnheader")) {
      expect(th).toHaveAttribute("scope", "col");
    }
  });

  it("shows the change and the contribution share", () => {
    render(<ContributorTable dimension={dimension()} />);
    const row = screen.getByRole("row", { name: /445/ });
    expect(within(row).getByText("47")).toBeInTheDocument();
    expect(within(row).getByText("528")).toBeInTheDocument();
    expect(within(row).getByText("+481")).toBeInTheDocument();
    expect(within(row).getByText("95.9%")).toBeInTheDocument();
  });

  it("shows a reduction's contribution as a magnitude, not a negative share", () => {
    // The backend reports a negative share on a drop; "-95.9% contribution" is
    // not how a reduction is described.
    render(
      <ContributorTable
        dimension={dimension({
          contributors: [contributor({ contribution_share: -0.959, absolute_delta: -481 })],
        })}
      />,
    );
    const row = screen.getByRole("row", { name: /445/ });
    expect(within(row).getByText("95.9%")).toBeInTheDocument();
    // The delta keeps its sign: the direction of the change is a fact.
    expect(within(row).getByText("-481")).toBeInTheDocument();
  });
});

describe("row disclosure", () => {
  it("keeps the secondary fields out of the default view", () => {
    render(<ContributorTable dimension={dimension()} />);
    expect(screen.queryByText("Baseline share")).toBeNull();
    expect(screen.queryByText(/Rank, baseline to anomaly/)).toBeNull();
  });

  it("reveals them on demand without losing any of them", async () => {
    const user = userEvent.setup();
    render(<ContributorTable dimension={dimension()} />);

    await user.click(screen.getByRole("button", { name: /Show detail for 445/ }));

    expect(screen.getByText("Percent delta")).toBeInTheDocument();
    expect(screen.getByText("+1023.4%")).toBeInTheDocument();
    expect(screen.getByText("Baseline share")).toBeInTheDocument();
    expect(screen.getByText("Anomaly share")).toBeInTheDocument();
    expect(screen.getByText(/Rank, baseline to anomaly/)).toBeInTheDocument();
    expect(screen.getByText("3 → 1")).toBeInTheDocument();
  });

  it("reports its expanded state to assistive technology", async () => {
    const user = userEvent.setup();
    render(<ContributorTable dimension={dimension()} />);

    const toggle = screen.getByRole("button", { name: /Show detail for 445/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(
      screen.getByRole("button", { name: /Hide detail for 445/ }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("renders a percent delta of null as unmeasured, never as no change", async () => {
    // A new value has no baseline to be a percentage of. "0%" would assert
    // that nothing changed.
    const user = userEvent.setup();
    render(
      <ContributorTable
        dimension={dimension({
          contributors: [contributor({ percent_delta: null, is_new: true })],
        })}
      />,
    );
    expect(screen.getByText("new")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Show detail for 445/ }));
    const cell = screen.getByText("Percent delta").closest("div")!;
    expect(within(cell).getByText("—")).toBeInTheDocument();
  });
});

describe("truncated dimensions", () => {
  const truncated = dimension({
    availability: "TRUNCATED",
    truncated: true,
    new_value_count: 0,
    disappeared_value_count: 0,
    baseline_distinct_count: 20,
    anomaly_distinct_count: 20,
  });

  it("still shows the contributor rows, which are a real observation", () => {
    render(<ContributorTable dimension={truncated} />);
    expect(screen.getByRole("row", { name: /445/ })).toBeInTheDocument();
  });

  it("withholds the new and disappeared counts as indeterminate", () => {
    // Under a cap a value can look new merely because it fell below the cap in
    // the other window. Rendering the backend's zero would assert a finding.
    render(<ContributorTable dimension={truncated} />);
    expect(screen.getAllByText("indeterminate")).toHaveLength(4);
  });

  it("does not claim a complete cardinality", () => {
    render(<ContributorTable dimension={truncated} />);
    const meta = screen.getByText("Baseline cardinality").closest("div")!;
    expect(within(meta).getByText("indeterminate")).toBeInTheDocument();
  });

  it("shows a compact truncation warning", () => {
    render(<ContributorTable dimension={truncated} />);
    expect(
      screen.getByText(/top of the list, not the whole of it/i),
    ).toBeInTheDocument();
  });

  it("reports determinate counts normally when nothing was capped", () => {
    render(<ContributorTable dimension={dimension()} />);
    expect(screen.queryByText("indeterminate")).toBeNull();
    const meta = screen.getByText("New values").closest("div")!;
    expect(within(meta).getByText("1")).toBeInTheDocument();
  });
});

describe("unavailable and failed dimensions", () => {
  it("renders no empty table for an unavailable dimension", () => {
    render(
      <ContributorTable
        dimension={dimension({ availability: "UNAVAILABLE", contributors: [] })}
      />,
    );
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("states that an unavailable field was never checked", () => {
    render(
      <ContributorTable
        dimension={dimension({ availability: "UNAVAILABLE", contributors: [] })}
      />,
    );
    expect(
      screen.getByText(/No conclusion follows from the absence of contributors/i),
    ).toBeInTheDocument();
  });

  it("renders no empty table for a failed dimension", () => {
    render(
      <ContributorTable
        dimension={dimension({ availability: "FAILED", contributors: [] })}
      />,
    );
    expect(screen.queryByRole("table")).toBeNull();
    // Stated by both the dimension's meaning line and the failure panel.
    expect(screen.getAllByText(/did not complete/i).length).toBeGreaterThan(0);
  });

  it("shows the backend's sanitized reason as text", () => {
    render(
      <ContributorTable
        dimension={dimension({
          availability: "UNAVAILABLE",
          contributors: [],
          detail: "field is not populated for this log source",
        })}
      />,
    );
    expect(
      screen.getByText(/field is not populated for this log source/),
    ).toBeInTheDocument();
  });

  it("distinguishes a collected-but-empty dimension from an unchecked one", () => {
    render(<ContributorTable dimension={dimension({ contributors: [] })} />);
    expect(
      screen.getByText(/collected and no value stood out as a contributor/i),
    ).toBeInTheDocument();
  });
});

describe("value rendering", () => {
  it("shows a resolved label alongside its raw identifier", () => {
    render(
      <ContributorTable
        dimension={dimension({
          dimension: "protocol",
          contributors: [contributor({ dimension: "protocol", value: "6", label: "TCP" })],
        })}
      />,
    );
    expect(screen.getByText("TCP")).toBeInTheDocument();
    expect(screen.getByText("(6)")).toBeInTheDocument();
  });

  it("renders a value containing markup as text, never as markup", () => {
    const { container } = render(
      <ContributorTable
        dimension={dimension({
          contributors: [contributor({ value: "<img src=x onerror=alert(1)>" })],
        })}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
  });

  it("flags new and disappeared values", () => {
    render(
      <ContributorTable
        dimension={dimension({
          contributors: [
            contributor({ value: "445", is_new: true }),
            contributor({ value: "22", is_disappeared: true }),
          ],
        })}
      />,
    );
    expect(screen.getByText("new")).toBeInTheDocument();
    expect(screen.getByText("disappeared")).toBeInTheDocument();
  });
});

describe("containment", () => {
  it("puts the table in a labelled scroll region", () => {
    const { container } = render(<ContributorTable dimension={dimension()} />);
    const region = container.querySelector(".table-scroll");
    expect(region).toHaveAttribute("role", "region");
    expect(region).toHaveAttribute("tabindex", "0");
  });

  it("gives the table an accessible caption", () => {
    render(<ContributorTable dimension={dimension()} />);
    expect(
      screen.getByText(/values ranked by their contribution to the change/i),
    ).toBeInTheDocument();
  });
});
