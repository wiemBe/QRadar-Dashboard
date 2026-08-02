import { describe, expect, it } from "vitest";

import { NAV, activeNavHref, navItemMatches } from "./nav";

describe("NAV", () => {
  it("leads with the three behavioral destinations", () => {
    expect(NAV[0].label).toBeNull();
    expect(NAV[0].items.map((i) => i.label)).toEqual([
      "Overview",
      "Anomalies",
      "Sources",
    ]);
  });

  it("separates supporting capabilities into their own group", () => {
    expect(NAV[1].label).toBe("Platform");
    expect(NAV[1].items.map((i) => i.label)).toEqual([
      "Offenses",
      "Rules",
      "Coverage",
      "Log Sources",
      "Searches",
      "Alerts",
      "SOC Overview",
    ]);
  });

  // A nav entry is a promise that something is on the other side of it.
  it("links to no route that does not exist", () => {
    const hrefs = NAV.flatMap((g) => g.items.map((i) => i.href));
    expect(hrefs).not.toContain("/providers");
    expect(hrefs).not.toContain("/audit");
    expect(hrefs).not.toContain("/config-changes");
    expect(hrefs).not.toContain("/admin");
  });
});

describe("navItemMatches", () => {
  it("matches a section and the detail routes beneath it", () => {
    expect(navItemMatches("/anomalies", "/anomalies")).toBe(true);
    expect(navItemMatches("/anomalies/abc-123", "/anomalies")).toBe(true);
  });

  it("does not match a sibling whose path merely starts the same way", () => {
    expect(navItemMatches("/anomalies-archive", "/anomalies")).toBe(false);
  });

  it("matches the root only exactly, so it cannot claim every route", () => {
    expect(navItemMatches("/", "/")).toBe(true);
    expect(navItemMatches("/offenses", "/")).toBe(false);
  });
});

describe("activeNavHref", () => {
  it("activates Overview on the behavioral overview", () => {
    expect(activeNavHref("/behavior")).toBe("/behavior");
  });

  it("activates Sources on the source index", () => {
    expect(activeNavHref("/behavior/sources")).toBe("/behavior/sources");
  });

  // The nested case that a first-match implementation gets wrong: /behavior is
  // a prefix of /behavior/sources, so ordering alone would light up Overview.
  it("activates Sources on a source detail route, not Overview", () => {
    expect(activeNavHref("/behavior/sources/abc-123")).toBe("/behavior/sources");
  });

  it("activates Anomalies on the list and on an investigation", () => {
    expect(activeNavHref("/anomalies")).toBe("/anomalies");
    expect(activeNavHref("/anomalies/abc-123")).toBe("/anomalies");
  });

  it("activates supporting sections and their detail routes", () => {
    expect(activeNavHref("/offenses")).toBe("/offenses");
    expect(activeNavHref("/rules/42")).toBe("/rules");
    expect(activeNavHref("/log-sources/abc")).toBe("/log-sources");
    expect(activeNavHref("/")).toBe("/");
  });

  it("marks nothing rather than something arbitrary on an unowned path", () => {
    expect(activeNavHref("/nowhere")).toBeNull();
  });
});
