import { describe, expect, it } from "vitest";

import type { SourceBehavior } from "./api";
import {
  SOURCE_PAGE_SIZE,
  ariaSort,
  defaultDirection,
  filterSources,
  isFiltered,
  parseSourceQuery,
  sliceSources,
  sortHref,
  sortSources,
  sourceQueryParams,
} from "./sourceInventory";

function source(over: Partial<SourceBehavior> = {}): SourceBehavior {
  return {
    log_source_id: "s-1",
    name: "LAB Firewall",
    criticality: "HIGH",
    observed_eps: 6,
    expected_eps: 2,
    expected_low: 1.7,
    expected_high: 2.3,
    deviation_ratio: 3,
    state: "NORMAL",
    baseline_sample_count: 30,
    baseline_completeness: 0.9,
    last_bucket_at: "2026-07-20T10:00:00Z",
    last_event_at: "2026-07-20T10:04:00Z",
    open_anomaly_count: 0,
    ...over,
  };
}

describe("parseSourceQuery", () => {
  it("defaults to the worst deviation first", () => {
    const q = parseSourceQuery({});
    expect(q.sort).toBe("deviation");
    expect(q.dir).toBe("desc");
    expect(q.offset).toBe(0);
  });

  it("reads names ascending by default but deviation descending", () => {
    expect(defaultDirection("name")).toBe("asc");
    expect(defaultDirection("deviation")).toBe("desc");
    expect(defaultDirection("last_event")).toBe("desc");
    expect(parseSourceQuery({ sort: "name" }).dir).toBe("asc");
  });

  // These values arrive from a URL a third party may have written, so an
  // unrecognised one is discarded rather than carried into the page.
  it("discards a state outside the allow-list", () => {
    expect(parseSourceQuery({ state: "OPEN" }).state).toBe("OPEN");
    expect(parseSourceQuery({ state: "<script>" }).state).toBe("");
    expect(parseSourceQuery({ state: "MADE_UP" }).state).toBe("");
  });

  it("discards an unrecognised sort, filter or direction", () => {
    expect(parseSourceQuery({ sort: "; DROP" }).sort).toBe("deviation");
    expect(parseSourceQuery({ dir: "sideways" }).dir).toBe("desc");
    expect(parseSourceQuery({ anomaly: "maybe" }).anomaly).toBe("");
    expect(parseSourceQuery({ data: "whatever" }).data).toBe("");
  });

  it("bounds the search term rather than echoing an unbounded one", () => {
    expect(parseSourceQuery({ q: "  fw  " }).q).toBe("fw");
    expect(parseSourceQuery({ q: "x".repeat(500) }).q).toHaveLength(120);
  });

  it("snaps a hand-edited offset to a page boundary", () => {
    // An arbitrary offset produces a window no Next link can reach or leave.
    expect(parseSourceQuery({ offset: "37" }).offset).toBe(25);
    expect(parseSourceQuery({ offset: "-5" }).offset).toBe(0);
    expect(parseSourceQuery({ offset: "abc" }).offset).toBe(0);
  });

  it("knows when the view is narrowed", () => {
    expect(isFiltered(parseSourceQuery({}))).toBe(false);
    expect(isFiltered(parseSourceQuery({ sort: "name" }))).toBe(false);
    expect(isFiltered(parseSourceQuery({ q: "fw" }))).toBe(true);
    expect(isFiltered(parseSourceQuery({ data: "insufficient" }))).toBe(true);
  });
});

describe("filterSources", () => {
  const fleet = [
    source({ log_source_id: "a", name: "LAB Firewall", state: "OPEN", open_anomaly_count: 2 }),
    source({ log_source_id: "b", name: "DC-01 Windows", state: "NORMAL" }),
    source({ log_source_id: "c", name: "Edge Proxy", state: "INSUFFICIENT_DATA" }),
  ];

  it("searches source names case-insensitively", () => {
    expect(filterSources(fleet, parseSourceQuery({ q: "firewall" }))).toHaveLength(1);
    expect(filterSources(fleet, parseSourceQuery({ q: "LAB" }))).toHaveLength(1);
    expect(filterSources(fleet, parseSourceQuery({ q: "zzz" }))).toHaveLength(0);
  });

  it("filters by behavior state", () => {
    const open = filterSources(fleet, parseSourceQuery({ state: "OPEN" }));
    expect(open.map((s) => s.log_source_id)).toEqual(["a"]);
  });

  it("filters by active anomaly presence in both directions", () => {
    expect(
      filterSources(fleet, parseSourceQuery({ anomaly: "active" })).map((s) => s.log_source_id),
    ).toEqual(["a"]);
    expect(
      filterSources(fleet, parseSourceQuery({ anomaly: "none" })).map((s) => s.log_source_id),
    ).toEqual(["b", "c"]);
  });

  it("filters unbaselined sources apart from baselined ones", () => {
    expect(
      filterSources(fleet, parseSourceQuery({ data: "insufficient" })).map(
        (s) => s.log_source_id,
      ),
    ).toEqual(["c"]);
    expect(
      filterSources(fleet, parseSourceQuery({ data: "adequate" })).map((s) => s.log_source_id),
    ).toEqual(["a", "b"]);
  });

  it("combines filters conjunctively", () => {
    const q = parseSourceQuery({ q: "lab", state: "NORMAL" });
    expect(filterSources(fleet, q)).toHaveLength(0);
  });
});

describe("sortSources", () => {
  it("sorts by deviation, worst first", () => {
    const sorted = sortSources(
      [
        source({ name: "mild", deviation_ratio: 1.2 }),
        source({ name: "spike", deviation_ratio: 5 }),
        source({ name: "drop", deviation_ratio: 0.1 }),
      ],
      "deviation",
      "desc",
    );
    // A drop to a tenth is further from normal than a 5x spike.
    expect(sorted.map((s) => s.name)).toEqual(["drop", "spike", "mild"]);
  });

  it("holds unmeasurable sources at the end in both directions", () => {
    // A source with no usable ratio is not "the least deviating source"; it is
    // one we cannot rank. Reversing the order must not promote it to the top
    // of a list the analyst reads as "highest deviation".
    const fleet = [
      source({ name: "unbaselined", state: "INSUFFICIENT_DATA" }),
      source({ name: "measured", deviation_ratio: 3 }),
    ];
    expect(sortSources(fleet, "deviation", "desc").map((s) => s.name)).toEqual([
      "measured",
      "unbaselined",
    ]);
    expect(sortSources(fleet, "deviation", "asc").map((s) => s.name)).toEqual([
      "measured",
      "unbaselined",
    ]);
  });

  it("sorts by last event, and holds sources that never reported at the end", () => {
    const fleet = [
      source({ name: "never", last_event_at: null }),
      source({ name: "old", last_event_at: "2026-07-01T00:00:00Z" }),
      source({ name: "recent", last_event_at: "2026-07-20T00:00:00Z" }),
    ];
    expect(sortSources(fleet, "last_event", "desc").map((s) => s.name)).toEqual([
      "recent",
      "old",
      "never",
    ]);
    expect(sortSources(fleet, "last_event", "asc").map((s) => s.name)).toEqual([
      "old",
      "recent",
      "never",
    ]);
  });

  it("sorts by name in both directions", () => {
    const fleet = [source({ name: "Beta" }), source({ name: "Alpha" })];
    expect(sortSources(fleet, "name", "asc").map((s) => s.name)).toEqual(["Alpha", "Beta"]);
    expect(sortSources(fleet, "name", "desc").map((s) => s.name)).toEqual(["Beta", "Alpha"]);
  });

  it("is a total order, so identical data never reshuffles between renders", () => {
    const fleet = [
      source({ name: "Charlie", deviation_ratio: 3 }),
      source({ name: "Alpha", deviation_ratio: 3 }),
      source({ name: "Bravo", deviation_ratio: 3 }),
    ];
    expect(sortSources(fleet, "deviation", "desc").map((s) => s.name)).toEqual([
      "Alpha",
      "Bravo",
      "Charlie",
    ]);
  });

  it("does not mutate its input", () => {
    const fleet = [source({ name: "B" }), source({ name: "A" })];
    sortSources(fleet, "name", "asc");
    expect(fleet.map((s) => s.name)).toEqual(["B", "A"]);
  });
});

describe("sliceSources", () => {
  const fleet = Array.from({ length: 60 }, (_, i) =>
    source({ log_source_id: `s-${i}`, name: `Source ${i}` }),
  );

  it("returns one page at a time", () => {
    expect(sliceSources(fleet, 0)).toHaveLength(SOURCE_PAGE_SIZE);
    expect(sliceSources(fleet, 0)[0].name).toBe("Source 0");
    expect(sliceSources(fleet, 25)[0].name).toBe("Source 25");
  });

  it("returns a short final page rather than padding it", () => {
    expect(sliceSources(fleet, 50)).toHaveLength(10);
  });

  it("returns nothing past the end", () => {
    expect(sliceSources(fleet, 500)).toHaveLength(0);
  });
});

describe("sortHref and ariaSort", () => {
  it("flips direction when the active column is clicked again", () => {
    const q = parseSourceQuery({ sort: "deviation", dir: "desc" });
    expect(sortHref(q, "deviation")).toContain("dir=asc");
  });

  it("switches to another column at that column's natural direction", () => {
    const q = parseSourceQuery({ sort: "deviation", dir: "asc" });
    expect(sortHref(q, "name")).toContain("dir=asc");
    expect(sortHref(q, "last_event")).toContain("dir=desc");
  });

  it("carries the active filters so sorting does not clear them", () => {
    const q = parseSourceQuery({ q: "fw", state: "OPEN", anomaly: "active" });
    const href = sortHref(q, "name");
    expect(href).toContain("q=fw");
    expect(href).toContain("state=OPEN");
    expect(href).toContain("anomaly=active");
  });

  it("drops the offset, because a position in the old order means nothing", () => {
    const q = parseSourceQuery({ offset: "50" });
    expect(sortHref(q, "name")).not.toContain("offset");
  });

  it("announces the current sort only on the active column", () => {
    const q = parseSourceQuery({ sort: "deviation", dir: "desc" });
    expect(ariaSort(q, "deviation")).toBe("descending");
    expect(ariaSort(q, "name")).toBe("none");
    expect(ariaSort(parseSourceQuery({ sort: "name" }), "name")).toBe("ascending");
  });

  it("carries filters and sort into paging links", () => {
    const q = parseSourceQuery({ q: "fw", state: "OPEN", sort: "name", dir: "asc" });
    expect(sourceQueryParams(q)).toEqual({
      q: "fw",
      state: "OPEN",
      anomaly: undefined,
      data: undefined,
      sort: "name",
      dir: "asc",
    });
  });
});
