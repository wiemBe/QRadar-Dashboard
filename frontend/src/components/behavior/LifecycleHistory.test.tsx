import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LifecycleHistory } from "./LifecycleHistory";
import type { AnomalyTransition } from "@/lib/api";

function t(over: Partial<AnomalyTransition> = {}): AnomalyTransition {
  return {
    from_state: "CANDIDATE",
    to_state: "OPEN",
    occurred_at: "2026-07-20T10:05:00Z",
    bucket_start: "2026-07-20T10:00:00Z",
    reason: "2 consecutive abnormal bucket(s)",
    actor: "anomaly-engine",
    observed_value: 6,
    expected_value: 2,
    ...over,
  };
}

describe("transition rendering", () => {
  it("shows the full CANDIDATE → OPEN → RECOVERING → RESOLVED path", () => {
    render(
      <LifecycleHistory
        policyVersion={1}
        transitions={[
          t({ from_state: null, to_state: "CANDIDATE" }),
          t({ from_state: "CANDIDATE", to_state: "OPEN" }),
          t({ from_state: "OPEN", to_state: "RECOVERING" }),
          t({ from_state: "RECOVERING", to_state: "RESOLVED" }),
        ]}
      />,
    );
    // 4 transitions + the header row.
    expect(screen.getAllByRole("row")).toHaveLength(5);
    for (const state of ["CANDIDATE", "OPEN", "RECOVERING", "RESOLVED"]) {
      expect(screen.getAllByText(state).length).toBeGreaterThan(0);
    }
  });

  it("labels the first transition as an origin, not an unknown", () => {
    render(<LifecycleHistory policyVersion={1} transitions={[t({ from_state: null })]} />);
    expect(screen.getByText("initial")).toBeInTheDocument();
  });

  it("shows the reason, actor and policy version", () => {
    render(<LifecycleHistory policyVersion={3} transitions={[t()]} />);
    expect(screen.getByText("2 consecutive abnormal bucket(s)")).toBeInTheDocument();
    expect(screen.getByText("anomaly-engine")).toBeInTheDocument();
    expect(screen.getByText("v3")).toBeInTheDocument();
  });

  it("does not hide a transition whose reason was never recorded", () => {
    // Dropping the row would erase the fact that the state changed at all.
    render(<LifecycleHistory policyVersion={1} transitions={[t({ reason: null })]} />);
    expect(screen.getByText("not recorded")).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(2);
  });

  it("does not hide a flapping history", () => {
    // Six CANDIDATE/NORMAL flips before opening is a fact about detector
    // tuning; a page that shows only the path to the current state hides it.
    render(
      <LifecycleHistory
        policyVersion={1}
        transitions={[
          t({ from_state: "NORMAL", to_state: "CANDIDATE" }),
          t({ from_state: "CANDIDATE", to_state: "NORMAL" }),
          t({ from_state: "NORMAL", to_state: "CANDIDATE" }),
          t({ from_state: "CANDIDATE", to_state: "NORMAL" }),
          t({ from_state: "NORMAL", to_state: "CANDIDATE" }),
          t({ from_state: "CANDIDATE", to_state: "OPEN" }),
        ]}
      />,
    );
    expect(screen.getAllByRole("row")).toHaveLength(7);
  });

  it("shows an INSUFFICIENT_DATA hold rather than filtering it out", () => {
    render(
      <LifecycleHistory
        policyVersion={1}
        transitions={[
          t({
            from_state: "CANDIDATE",
            to_state: "INSUFFICIENT_DATA",
            reason: "metric bucket is partial, not a whole observation",
          }),
        ]}
      />,
    );
    expect(screen.getByText("INSUFFICIENT_DATA")).toBeInTheDocument();
    expect(screen.getByText(/not a whole observation/i)).toBeInTheDocument();
  });
});

describe("values on a transition", () => {
  it("shows observed and expected where recorded", () => {
    render(<LifecycleHistory policyVersion={1} transitions={[t()]} />);
    expect(screen.getByText("6.00")).toBeInTheDocument();
    expect(screen.getByText("2.00")).toBeInTheDocument();
  });

  it("dashes an unrecorded value rather than showing 0", () => {
    render(
      <LifecycleHistory
        policyVersion={1}
        transitions={[t({ observed_value: null, expected_value: null, bucket_start: null })]}
      />,
    );
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
  });

  it("shows a genuinely observed zero as 0", () => {
    // NO_EVENTS: the source really did send nothing.
    render(<LifecycleHistory policyVersion={1} transitions={[t({ observed_value: 0 })]} />);
    expect(screen.getByText("0.00")).toBeInTheDocument();
  });
});

describe("empty history", () => {
  it("calls an empty audit trail out as unexpected", () => {
    render(<LifecycleHistory policyVersion={1} transitions={[]} />);
    expect(screen.getByText(/itself unexpected/i)).toBeInTheDocument();
  });
});
