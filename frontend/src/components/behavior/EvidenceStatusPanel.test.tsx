import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EvidenceStatusPanel } from "./EvidenceStatusPanel";
import type { EvidenceStatus, ExplanationPackage } from "@/lib/api";

function pkg(over: Partial<ExplanationPackage> = {}): ExplanationPackage {
  return {
    status: "COMPLETE",
    error: null,
    anomaly_window_start: "2026-07-20T10:00:00Z",
    anomaly_window_end: "2026-07-20T10:05:00Z",
    baseline_window_start: "2026-07-20T09:45:00Z",
    baseline_window_end: "2026-07-20T10:00:00Z",
    comparison_strategy: "recent_normal_window",
    anomaly_total_events: 1800,
    baseline_total_events: 600,
    requested_at: "2026-07-20T10:06:00Z",
    completed_at: "2026-07-20T10:06:30Z",
    collection_duration_ms: 30000,
    query_provenance: {},
    schema_version: 1,
    dimensions: [],
    ...over,
  };
}

const ALL: EvidenceStatus[] = [
  "NOT_REQUESTED",
  "PENDING",
  "COMPLETE",
  "PARTIAL",
  "UNAVAILABLE",
  "FAILED",
];

describe("every evidence state is rendered with its meaning", () => {
  for (const status of ALL) {
    it(`renders ${status}`, () => {
      render(<EvidenceStatusPanel status={status} packaged={pkg({ status })} />);
      expect(screen.getByText(status)).toBeInTheDocument();
      // The label alone is jargon; the sentence is what an operator acts on.
      expect(screen.getByText(/./, { selector: "p.subtitle" })).toBeInTheDocument();
    });
  }
});

describe("PARTIAL", () => {
  it("says that some dimensions were not checked", () => {
    render(<EvidenceStatusPanel status="PARTIAL" packaged={pkg({ status: "PARTIAL" })} />);
    expect(screen.getByText(/have not been checked/i)).toBeInTheDocument();
    expect(
      screen.getByText(/absence of a contributor there is not evidence of absence/i),
    ).toBeInTheDocument();
  });
});

describe("UNAVAILABLE", () => {
  it("explains that this is a property of the source, not an error", () => {
    render(
      <EvidenceStatusPanel status="UNAVAILABLE" packaged={pkg({ status: "UNAVAILABLE" })} />,
    );
    expect(screen.getByText(/property of the source, not a transient error/i)).toBeInTheDocument();
  });
});

describe("FAILED", () => {
  it("surfaces the sanitized collection error", () => {
    render(
      <EvidenceStatusPanel
        status="FAILED"
        packaged={pkg({ status: "FAILED", error: "Ariel search timed out after 120s" })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Ariel search timed out after 120s");
  });

  it("says the failure implies nothing about the anomaly", () => {
    render(<EvidenceStatusPanel status="FAILED" packaged={pkg({ status: "FAILED" })} />);
    expect(screen.getByText(/says nothing about what happened/i)).toBeInTheDocument();
  });
});

describe("no package at all", () => {
  it("states that nothing has been queried, rather than showing an empty table", () => {
    render(<EvidenceStatusPanel status="NOT_REQUESTED" packaged={null} />);
    expect(screen.getByText(/Nothing about the anomalous interval has been queried/i)).toBeInTheDocument();
  });

  it("does not render window figures it does not have", () => {
    render(<EvidenceStatusPanel status="NOT_REQUESTED" packaged={null} />);
    expect(screen.queryByText(/Anomaly-window events/)).toBeNull();
  });
});

describe("window totals", () => {
  it("reports both windows' event counts", () => {
    render(<EvidenceStatusPanel status="COMPLETE" packaged={pkg()} />);
    expect(screen.getByText("1,800")).toBeInTheDocument();
    expect(screen.getByText("600")).toBeInTheDocument();
  });

  it("reports a zero-event window as 0", () => {
    render(
      <EvidenceStatusPanel
        status="COMPLETE"
        packaged={pkg({ anomaly_total_events: 0 })}
      />,
    );
    // A NO_EVENTS anomaly genuinely has zero events in its window.
    expect(screen.getByText("0")).toBeInTheDocument();
  });
});
