// Ported from the removed EvidenceStatusPanel tests.
//
// The invariant they existed to protect is unchanged: four of the six evidence
// statuses produce an investigation page with no contributors, which is
// visually identical to "we looked and nothing stood out". Only the stated
// status distinguishes them, so every status must say what it means.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EvidenceBanner } from "./EvidenceBanner";
import type { EvidenceStatus } from "@/lib/api";

describe("every status states its meaning", () => {
  const cases: Array<[EvidenceStatus, RegExp]> = [
    ["PARTIAL", /unavailable or truncated/i],
    ["UNAVAILABLE", /exposes none of the requested fields/i],
    ["FAILED", /collection failed/i],
    ["PENDING", /still being collected/i],
    ["NOT_REQUESTED", /Nothing has been looked at/i],
  ];

  for (const [status, expected] of cases) {
    it(`explains ${status}`, () => {
      render(<EvidenceBanner status={status} />);
      expect(screen.getByText(expected)).toBeInTheDocument();
    });
  }

  it("says nothing for COMPLETE, which needs no qualification", () => {
    const { container } = render(<EvidenceBanner status="COMPLETE" />);
    expect(container.querySelector(".banner")).toBeNull();
  });
});

describe("status is not carried by colour alone", () => {
  it("names the status in text", () => {
    render(<EvidenceBanner status="PARTIAL" />);
    expect(screen.getByText("PARTIAL.")).toBeInTheDocument();
  });
});

describe("honesty about incomplete evidence", () => {
  it("does not describe a partial package as healthy", () => {
    render(<EvidenceBanner status="PARTIAL" />);
    const text = screen.getByText(/unavailable or truncated/i).textContent ?? "";
    expect(text).toMatch(/have not been checked/i);
    expect(text).toMatch(/no conclusion follows from the absence/i);
  });

  it("says a pending collection is a backlog rather than a finding", () => {
    render(<EvidenceBanner status="PENDING" />);
    expect(screen.getByText(/backlog, not a finding/i)).toBeInTheDocument();
  });

  it("distinguishes a source property from a transient error", () => {
    render(<EvidenceBanner status="UNAVAILABLE" />);
    expect(
      screen.getByText(/property of the source, not a transient error/i),
    ).toBeInTheDocument();
  });

  it("announces a failed collection as an alert", () => {
    render(<EvidenceBanner status="FAILED" />);
    expect(screen.getByRole("alert")).toHaveTextContent(/collection failed/i);
  });
});

describe("truncation", () => {
  it("warns once, globally, naming the capped dimensions", () => {
    render(
      <EvidenceBanner status="PARTIAL" truncatedDimensions={["Source port", "QID"]} />,
    );
    const warning = screen.getByText(/Some results were limited to the top values/i);
    expect(warning).toHaveTextContent("Source port, QID");
  });

  it("explains why counts are withheld rather than shown as zero", () => {
    render(<EvidenceBanner status="PARTIAL" truncatedDimensions={["Source port"]} />);
    expect(
      screen.getByText(/look new simply because it fell below the cap/i),
    ).toBeInTheDocument();
  });

  it("omits the truncation banner when nothing was capped", () => {
    render(<EvidenceBanner status="COMPLETE" truncatedDimensions={[]} />);
    expect(screen.queryByText(/limited to the top values/i)).toBeNull();
  });
});

describe("collection error", () => {
  it("renders the backend's sanitized message as text", () => {
    render(<EvidenceBanner status="FAILED" error="Ariel search timed out after 60s" />);
    expect(screen.getByText(/Ariel search timed out after 60s/)).toBeInTheDocument();
  });

  it("renders an error containing markup as text, never as markup", () => {
    const { container } = render(
      <EvidenceBanner status="FAILED" error="<img src=x onerror=alert(1)>" />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
  });

  it("omits the error banner when there is no error", () => {
    render(<EvidenceBanner status="PARTIAL" error={null} />);
    expect(screen.queryByText(/Collection error/i)).toBeNull();
  });
});
