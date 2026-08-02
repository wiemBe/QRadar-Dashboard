// The formatting rules that keep the behavioral pages honest.
//
// Almost every test here is a variation on one property: a measured zero and an
// unmeasured value must not render the same. `value || "—"` satisfies none of
// them, and is the bug this module exists to prevent.

import { describe, expect, it } from "vitest";

import {
  completenessMeaning,
  dimensionLabel,
  dimensionMeaning,
  dimensionTone,
  evidenceMeaning,
  evidenceTone,
  formatCount,
  formatDelta,
  formatDuration,
  formatMetric,
  formatPercentDelta,
  formatRatio,
  formatShare,
  hasConfidenceLimitation,
  isUnjudged,
  isUnobserved,
  robustScoreMeaning,
  stateMeaning,
  stateTone,
} from "./behavior";

describe("zero is not the same as unmeasured", () => {
  it("renders a measured zero as 0", () => {
    expect(formatMetric(0)).toBe("0.00");
    expect(formatCount(0)).toBe("0");
    expect(formatRatio(0)).toBe("0.00x");
    expect(formatShare(0)).toBe("0.0%");
    expect(formatDelta(0)).toBe("0");
    expect(formatPercentDelta(0)).toBe("0.0%");
    expect(formatDuration(0)).toBe("0s");
  });

  it("renders an unmeasured value as an em dash, never as 0", () => {
    for (const fn of [
      formatMetric,
      formatCount,
      formatRatio,
      formatShare,
      formatDelta,
      formatPercentDelta,
      formatDuration,
    ]) {
      expect(fn(null)).toBe("—");
      expect(fn(undefined)).toBe("—");
    }
  });

  it("does not render NaN as a number", () => {
    expect(formatMetric(Number.NaN)).toBe("—");
    expect(formatRatio(Number.NaN)).toBe("—");
  });
});

describe("signed formatting", () => {
  it("signs a delta so a drop is visibly a drop", () => {
    expect(formatDelta(1200)).toBe("+1,200");
    expect(formatDelta(-1200)).toBe("-1,200");
  });

  it("signs a percentage delta", () => {
    expect(formatPercentDelta(400.6)).toBe("+400.6%");
    expect(formatPercentDelta(-42.5)).toBe("-42.5%");
  });

  it("scales a fraction into a percentage", () => {
    expect(formatShare(0.68)).toBe("68.0%");
  });
});

describe("formatDuration", () => {
  it("scales from seconds to days", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(300)).toBe("5m");
    expect(formatDuration(3900)).toBe("1h 5m");
    expect(formatDuration(90000)).toBe("1d 1h");
  });

  it("reports a still-running anomaly as unmeasured rather than 0s", () => {
    expect(formatDuration(null)).toBe("—");
  });
});

describe("lifecycle states", () => {
  it("never paints INSUFFICIENT_DATA as healthy", () => {
    // The whole point: an unbaselined source shown in green is a false
    // assurance that it was checked.
    expect(stateTone("INSUFFICIENT_DATA")).toBe("");
    expect(stateTone("NORMAL")).toBe("ok");
  });

  it("distinguishes a confirmed incident from a candidate", () => {
    expect(stateTone("OPEN")).toBe("crit");
    expect(stateTone("CANDIDATE")).toBe("warn");
    expect(stateTone("RECOVERING")).toBe("warn");
  });

  it("treats a resolved anomaly as good news", () => {
    expect(stateTone("RESOLVED")).toBe("ok");
  });

  it("keeps SUPPRESSED neutral: it is neither a finding nor an all-clear", () => {
    expect(stateTone("SUPPRESSED")).toBe("");
  });

  it("explains every state", () => {
    for (const s of [
      "INSUFFICIENT_DATA",
      "NORMAL",
      "CANDIDATE",
      "OPEN",
      "RECOVERING",
      "RESOLVED",
      "SUPPRESSED",
    ] as const) {
      expect(stateMeaning(s).length).toBeGreaterThan(20);
    }
  });

  it("flags only INSUFFICIENT_DATA as unjudged", () => {
    expect(isUnjudged("INSUFFICIENT_DATA")).toBe(true);
    expect(isUnjudged("NORMAL")).toBe(false);
    expect(isUnjudged(null)).toBe(false);
  });
});

describe("evidence status", () => {
  const ALL = [
    "NOT_REQUESTED",
    "PENDING",
    "COMPLETE",
    "PARTIAL",
    "UNAVAILABLE",
    "FAILED",
  ] as const;

  it("explains all six states", () => {
    for (const s of ALL) {
      expect(evidenceMeaning(s).length).toBeGreaterThan(20);
    }
  });

  it("only calls COMPLETE good news", () => {
    expect(evidenceTone("COMPLETE")).toBe("ok");
    expect(evidenceTone("PARTIAL")).toBe("warn");
    expect(evidenceTone("PENDING")).toBe("warn");
    expect(evidenceTone("FAILED")).toBe("crit");
  });

  it("keeps 'nothing was collected' neutral rather than green", () => {
    // NOT_REQUESTED and UNAVAILABLE mean no evidence exists. Colouring either
    // as ok would present an unexamined anomaly as a clean one.
    expect(evidenceTone("NOT_REQUESTED")).toBe("");
    expect(evidenceTone("UNAVAILABLE")).toBe("");
  });

  it("says that PARTIAL leaves dimensions unchecked", () => {
    expect(evidenceMeaning("PARTIAL")).toMatch(/not been checked/i);
  });

  it("says UNAVAILABLE is a property of the source, not an error", () => {
    expect(evidenceMeaning("UNAVAILABLE")).toMatch(/property of the source/i);
  });

  it("says FAILED is transient and says nothing about the anomaly", () => {
    expect(evidenceMeaning("FAILED")).toMatch(/transient/i);
  });
});

describe("dimension availability", () => {
  it("states explicitly that an unavailable dimension was not checked", () => {
    const text = dimensionMeaning("UNAVAILABLE");
    expect(text).toMatch(/not exposed by the QRadar event schema or DSM/i);
    expect(text).toMatch(/has not been checked/i);
  });

  it("keeps an unavailable dimension neutral, not green", () => {
    expect(dimensionTone("UNAVAILABLE")).toBe("");
    expect(dimensionTone("AVAILABLE")).toBe("ok");
    expect(dimensionTone("TRUNCATED")).toBe("warn");
    expect(dimensionTone("FAILED")).toBe("crit");
  });

  it("warns that a truncated dimension is a prefix of the list", () => {
    expect(dimensionMeaning("TRUNCATED")).toMatch(/cap/i);
  });

  it("labels every dimension the backend requests", () => {
    expect(dimensionLabel("qid")).toBe("QID");
    expect(dimensionLabel("destination_port")).toBe("Destination port");
    // An unknown dimension falls back to its raw name rather than vanishing.
    expect(dimensionLabel("some_new_field")).toBe("some_new_field");
  });
});

describe("bucket completeness", () => {
  it("treats anything but COMPLETE as not a trustworthy observation", () => {
    expect(isUnobserved("COMPLETE")).toBe(false);
    expect(isUnobserved("PARTIAL")).toBe(true);
    expect(isUnobserved("MISSING")).toBe(true);
  });

  it("says a partial bucket is an undercount, not a measurement", () => {
    expect(completenessMeaning("PARTIAL")).toMatch(/undercount/i);
  });

  it("says a missing bucket implies nothing about the source", () => {
    expect(completenessMeaning("MISSING")).toMatch(
      /says nothing about whether the source was sending/i,
    );
  });
});

describe("degenerate MAD", () => {
  it("explains that a degenerate score means the verdict rests elsewhere", () => {
    const text = robustScoreMeaning("DEGENERATE");
    expect(text).toMatch(/MAD was zero/i);
    expect(text).toMatch(/weaker evidence/i);
  });

  it("marks the confidence as limited when the score is unusable", () => {
    expect(hasConfidenceLimitation("DEGENERATE")).toBe(true);
    expect(hasConfidenceLimitation("UNAVAILABLE")).toBe(true);
    expect(hasConfidenceLimitation("OK")).toBe(false);
    expect(hasConfidenceLimitation(null)).toBe(false);
  });
});
