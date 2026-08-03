// Status meaning must survive the loss of colour.
//
// The individual tone and wording rules are covered in `behavior.test.ts`.
// What is not covered anywhere is the property that holds *across* the value
// sets: that every status an operator can encounter carries text, that no two
// statuses in a set reduce to the same words, and that nothing which means
// "not measured", "not checked" or "broken" is ever dressed as good news.
//
// These are the eight representative values the UI actually renders: the four
// severities, the unbaselined state, and the three evidence outcomes that a
// green badge would misreport.

import { describe, expect, it } from "vitest";

import { sevTone, type AnomalyState, type EvidenceStatus } from "@/lib/api";
import {
  dimensionMeaning,
  dimensionTone,
  evidenceMeaning,
  evidenceTone,
  isUnjudged,
  stateMeaning,
  stateTone,
} from "@/lib/behavior";

const SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
const STATES: AnomalyState[] = [
  "INSUFFICIENT_DATA",
  "NORMAL",
  "CANDIDATE",
  "OPEN",
  "RECOVERING",
  "RESOLVED",
  "SUPPRESSED",
];
const EVIDENCE: EvidenceStatus[] = [
  "NOT_REQUESTED",
  "PENDING",
  "COMPLETE",
  "PARTIAL",
  "UNAVAILABLE",
  "FAILED",
];

describe("severity is ordered and never silent", () => {
  it("gives every severity a tone", () => {
    for (const severity of SEVERITIES) {
      expect(sevTone(severity), severity).toBeTruthy();
    }
  });

  it("escalates rather than treating every severity alike", () => {
    // Non-vacuous: if `sevTone` collapsed to one class the badge would carry
    // no visual grading at all, and the four values would be indistinguishable
    // to a reader skimming colour.
    expect(new Set(SEVERITIES.map(sevTone)).size).toBeGreaterThan(1);
    expect(sevTone("CRITICAL")).toBe(sevTone("HIGH"));
    expect(sevTone("MEDIUM")).not.toBe(sevTone("HIGH"));
  });

  it("does not invent a tone for an unrecognised severity", () => {
    // An unknown value must not fall through to the most alarming class.
    expect(sevTone("NOT_A_SEVERITY")).not.toBe(sevTone("CRITICAL"));
  });
});

describe("lifecycle state reads without colour", () => {
  it("explains every state in words", () => {
    for (const state of STATES) {
      expect(stateMeaning(state).length, state).toBeGreaterThan(0);
    }
  });

  it("says something different about each state", () => {
    // Two states sharing a sentence would make them indistinguishable to
    // anyone reading the explanation rather than the badge.
    const meanings = STATES.map(stateMeaning);
    expect(new Set(meanings).size).toBe(STATES.length);
  });

  it("never paints an unjudged source as good news", () => {
    // The invariant the overview is built around: no baseline means no
    // detector has judged the source, which is a gap, not a clean result.
    expect(isUnjudged("INSUFFICIENT_DATA")).toBe(true);
    expect(stateTone("INSUFFICIENT_DATA")).not.toBe("ok");
    // The sentence must say the source is *not* being judged. Searching for
    // the absence of the word "healthy" would be the wrong test: the current
    // wording earns its use of the word by denying it ("not the same as being
    // healthy"), and a test that failed on that would be pushing the copy in
    // the wrong direction.
    expect(stateMeaning("INSUFFICIENT_DATA")).toMatch(/\bnot\b/i);
    expect(stateMeaning("INSUFFICIENT_DATA")).toMatch(/judg|baseline/i);
  });

  it("distinguishes a real all-clear from an absence of judgement", () => {
    expect(stateTone("NORMAL")).not.toBe(stateTone("INSUFFICIENT_DATA"));
  });
});

describe("evidence outcome reads without colour", () => {
  it("explains every evidence status in words", () => {
    for (const status of EVIDENCE) {
      expect(evidenceMeaning(status).length, status).toBeGreaterThan(0);
    }
  });

  it("calls only a complete collection good news", () => {
    expect(evidenceTone("COMPLETE")).toBe("ok");
    for (const status of EVIDENCE.filter((s) => s !== "COMPLETE")) {
      expect(evidenceTone(status), status).not.toBe("ok");
    }
  });

  it("tells UNAVAILABLE and FAILED apart", () => {
    // One is a property of the source, the other a transient collection
    // error. Rendering them alike would make an outage look like a
    // characteristic of the data.
    expect(evidenceMeaning("UNAVAILABLE")).not.toBe(evidenceMeaning("FAILED"));
    expect(evidenceTone("UNAVAILABLE")).not.toBe(evidenceTone("FAILED"));
  });

  it("does not present either as an empty but successful result", () => {
    for (const status of ["UNAVAILABLE", "FAILED"] as const) {
      expect(evidenceMeaning(status), status).not.toBe(evidenceMeaning("COMPLETE"));
      expect(evidenceMeaning(status), status).not.toMatch(/\bno (?:contributors|results|change)\b/i);
    }
  });
});

describe("dimension availability reads without colour", () => {
  const AVAILABILITY = ["AVAILABLE", "TRUNCATED", "UNAVAILABLE", "FAILED"] as const;

  it("explains every availability in words", () => {
    for (const availability of AVAILABILITY) {
      expect(dimensionMeaning(availability).length, availability).toBeGreaterThan(0);
    }
  });

  it("says something different about each", () => {
    expect(new Set(AVAILABILITY.map(dimensionMeaning)).size).toBe(AVAILABILITY.length);
  });

  it("calls only a fully available dimension good news", () => {
    expect(dimensionTone("AVAILABLE")).toBe("ok");
    for (const availability of ["TRUNCATED", "UNAVAILABLE", "FAILED"] as const) {
      expect(dimensionTone(availability), availability).not.toBe("ok");
    }
  });

  it("says a truncated dimension is a prefix rather than the whole list", () => {
    // Silence here would let a capped list read as the complete one.
    expect(dimensionMeaning("TRUNCATED")).toMatch(/cap|truncat|limit|partial|top|first/i);
  });
});
