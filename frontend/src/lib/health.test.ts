import { describe, expect, it } from "vitest";

import {
  formatAge,
  formatDateTime,
  healthMeaning,
  healthTone,
  isUnestablished,
  magnitudeTone,
} from "./health";

describe("isUnestablished", () => {
  it.each(["INSUFFICIENT_DATA", "NOT_EVALUATED", "UNKNOWN"])(
    "treats %s as an absence of evidence",
    (status) => {
      expect(isUnestablished(status)).toBe(true);
    },
  );

  it.each(["HEALTHY", "MISSING", "NEVER_OBSERVED", "DISABLED", "COVERED"])(
    "treats %s as an actual verdict",
    (status) => {
      expect(isUnestablished(status)).toBe(false);
    },
  );

  it("handles null and undefined", () => {
    expect(isUnestablished(null)).toBe(false);
    expect(isUnestablished(undefined)).toBe(false);
  });
});

describe("healthTone", () => {
  // The core honesty property. An unevaluated technique rendered green is a
  // false assurance; rendered red it is a false alarm. It must be neither.
  it.each(["INSUFFICIENT_DATA", "NOT_EVALUATED", "UNKNOWN"])(
    "gives %s a neutral tone, never ok and never crit",
    (status) => {
      const tone = healthTone(status);
      expect(tone).toBe("");
      expect(tone).not.toBe("ok");
      expect(tone).not.toBe("crit");
    },
  );

  it("distinguishes a real gap from an unmeasured one", () => {
    expect(healthTone("MISSING")).toBe("crit");
    expect(healthTone("NOT_EVALUATED")).toBe("");
    expect(healthTone("NEVER_OBSERVED")).toBe("crit");
    expect(healthTone("INSUFFICIENT_DATA")).toBe("");
  });

  it("marks healthy and covered as ok", () => {
    expect(healthTone("HEALTHY")).toBe("ok");
    expect(healthTone("COVERED")).toBe("ok");
  });

  it("marks impaired states as warnings", () => {
    for (const s of ["NOISY", "INACTIVE", "DEGRADED", "PARTIAL", "DEPENDENCY_DEGRADED"]) {
      expect(healthTone(s)).toBe("warn");
    }
  });

  it("returns a neutral tone for an unrecognised status", () => {
    expect(healthTone("SOMETHING_NEW")).toBe("");
    expect(healthTone(null)).toBe("");
  });
});

describe("healthMeaning", () => {
  it("states plainly that INSUFFICIENT_DATA is not a finding", () => {
    expect(healthMeaning("INSUFFICIENT_DATA")).toContain("not a finding");
  });

  it("does not claim a technique is uncovered when it was never evaluated", () => {
    const text = healthMeaning("NOT_EVALUATED").toLowerCase();
    expect(text).toContain("no evaluation");
    expect(text).not.toContain("no rule");
  });

  it("returns an empty string for an unknown status rather than inventing one", () => {
    expect(healthMeaning("WAT")).toBe("");
  });
});

describe("magnitudeTone", () => {
  it.each([
    [10, "crit"],
    [7, "crit"],
    [6, "warn"],
    [4, "warn"],
    [3, "ok"],
    [0, "ok"],
  ])("maps magnitude %i to %s", (mag, expected) => {
    expect(magnitudeTone(mag as number)).toBe(expected);
  });

  it("stays neutral when magnitude is unknown", () => {
    expect(magnitudeTone(null)).toBe("");
    expect(magnitudeTone(undefined)).toBe("");
  });
});

describe("formatAge", () => {
  it("formats days and hours", () => {
    expect(formatAge(90000)).toBe("1d 1h");
  });

  it("formats hours and minutes", () => {
    expect(formatAge(3660)).toBe("1h 1m");
  });

  it("formats minutes alone", () => {
    expect(formatAge(120)).toBe("2m");
  });

  it("renders an unknown age as a dash, not as zero", () => {
    // "0m" would read as "brand new", which is a different claim.
    expect(formatAge(null)).toBe("—");
    expect(formatAge(undefined)).toBe("—");
  });
});

describe("formatDateTime", () => {
  it("renders a dash for null rather than the epoch", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime("")).toBe("—");
  });

  it("renders a dash for an unparseable value", () => {
    expect(formatDateTime("not-a-date")).toBe("—");
  });

  it("renders a valid timestamp", () => {
    expect(formatDateTime("2026-07-20T12:00:00Z")).not.toBe("—");
  });
});
