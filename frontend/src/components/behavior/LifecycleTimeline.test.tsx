// Ported from the removed LifecycleHistory tests.
//
// The invariant: nothing is filtered. "This incident flapped CANDIDATE/NORMAL
// six times before opening" is a fact about detector tuning, and a view showing
// only the transitions leading to the current state hides exactly the evidence
// that the thresholds need adjusting.

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LifecycleTimeline } from "./LifecycleTimeline";
import type { AnomalyTransition } from "@/lib/api";

function transition(over: Partial<AnomalyTransition> = {}): AnomalyTransition {
  return {
    from_state: "CANDIDATE",
    to_state: "OPEN",
    occurred_at: "2026-08-02T18:37:39Z",
    bucket_start: "2026-08-02T18:37:00Z",
    reason: "2 consecutive abnormal bucket(s) reached the confirmation threshold",
    actor: "detector",
    observed_value: 5.98,
    expected_value: 1.98,
    ...over,
  };
}

describe("completeness", () => {
  it("renders every transition, including flapping", () => {
    render(
      <LifecycleTimeline
        policyVersion={1}
        transitions={[
          transition({ from_state: null, to_state: "CANDIDATE" }),
          transition({ from_state: "CANDIDATE", to_state: "NORMAL" }),
          transition({ from_state: "NORMAL", to_state: "CANDIDATE" }),
          transition({ from_state: "CANDIDATE", to_state: "OPEN" }),
        ]}
      />,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
  });

  it("labels the first transition's absent origin rather than calling it unknown", () => {
    render(
      <LifecycleTimeline
        policyVersion={1}
        transitions={[transition({ from_state: null, to_state: "CANDIDATE" })]}
      />,
    );
    expect(screen.getByText("initial")).toBeInTheDocument();
  });

  it("reports an empty audit trail as unexpected, not as normal", () => {
    render(<LifecycleTimeline policyVersion={1} transitions={[]} />);
    expect(screen.getByText(/itself unexpected/i)).toBeInTheDocument();
  });
});

describe("presentation", () => {
  it("shows the state change and the reason at the top level", () => {
    render(<LifecycleTimeline policyVersion={1} transitions={[transition()]} />);
    expect(screen.getByText("CANDIDATE")).toBeInTheDocument();
    expect(screen.getByText("OPEN")).toBeInTheDocument();
    expect(screen.getByText(/confirmation threshold/i)).toBeInTheDocument();
  });

  it("says so when no reason was recorded rather than leaving a blank", () => {
    render(
      <LifecycleTimeline policyVersion={1} transitions={[transition({ reason: null })]} />,
    );
    expect(screen.getByText("No reason recorded")).toBeInTheDocument();
  });

  it("moves the repeated actor and policy version into a per-transition detail", () => {
    // Both were columns repeating one value down every row of the old table.
    render(<LifecycleTimeline policyVersion={3} transitions={[transition()]} />);
    const item = screen.getByRole("listitem");
    expect(within(item).getByText("Transition detail")).toBeInTheDocument();
    expect(within(item).getByText("detector")).toBeInTheDocument();
    expect(within(item).getByText("v3")).toBeInTheDocument();
  });

  it("uses a real time element so the timestamp is machine-readable", () => {
    const { container } = render(
      <LifecycleTimeline policyVersion={1} transitions={[transition()]} />,
    );
    expect(container.querySelector("time")).toHaveAttribute(
      "dateTime",
      "2026-08-02T18:37:39Z",
    );
  });

  it("renders an unmeasured transition value as an em dash, never as zero", () => {
    render(
      <LifecycleTimeline
        policyVersion={1}
        transitions={[transition({ observed_value: null, expected_value: null })]}
      />,
    );
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("renders the ordered sequence as a list, so its order is conveyed", () => {
    const { container } = render(
      <LifecycleTimeline policyVersion={1} transitions={[transition()]} />,
    );
    expect(container.querySelector("ol")).not.toBeNull();
  });
});
