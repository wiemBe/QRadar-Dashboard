import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SourcesIndexPage from "./page";
import { ApiError, api, type SourceBehavior } from "@/lib/api";

function source(over: Partial<SourceBehavior> = {}): SourceBehavior {
  return {
    log_source_id: "11111111-1111-1111-1111-111111111111",
    name: "LAB Firewall",
    criticality: "HIGH",
    observed_eps: 6,
    expected_eps: 2,
    expected_low: 1.7,
    expected_high: 2.3,
    deviation_ratio: 3,
    state: "OPEN",
    baseline_sample_count: 30,
    baseline_completeness: 0.9,
    last_bucket_at: "2026-07-20T10:00:00Z",
    last_event_at: "2026-07-20T10:04:00Z",
    open_anomaly_count: 1,
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderPage(
  params: Record<string, string | undefined> = {},
  sources: SourceBehavior[] = [source()],
) {
  vi.spyOn(api, "sourceBehaviors").mockResolvedValue(sources);
  return render(await SourcesIndexPage({ searchParams: Promise.resolve(params) }));
}

describe("heading hierarchy", () => {
  it("has exactly one top-level heading", async () => {
    await renderPage({}, [source(), source({ log_source_id: "s-2", name: "Core Switch" })]);

    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveAccessibleName("Sources");
  });

  it("does not promote a table caption or filter label to a heading", async () => {
    // The inventory is one table with a filter row above it. Neither may
    // introduce a competing top-level heading.
    await renderPage();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });
});

describe("rendering", () => {
  it("renders the inventory the overview no longer carries", async () => {
    await renderPage();

    expect(screen.getByRole("heading", { level: 1, name: "Sources" })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /LAB Firewall/ })).toBeInTheDocument();
  });

  it("links each source to its behavior detail", async () => {
    await renderPage();

    const row = screen.getByRole("row", { name: /LAB Firewall/ });
    expect(within(row).getByRole("link", { name: "LAB Firewall" })).toHaveAttribute(
      "href",
      "/behavior/sources/11111111-1111-1111-1111-111111111111",
    );
    expect(within(row).getByRole("link", { name: "Detail" })).toHaveAttribute(
      "href",
      "/behavior/sources/11111111-1111-1111-1111-111111111111",
    );
  });

  it("links an active anomaly count to its filtered list", async () => {
    await renderPage();

    const row = screen.getByRole("row", { name: /LAB Firewall/ });
    expect(within(row).getByRole("link", { name: "1" })).toHaveAttribute(
      "href",
      "/anomalies?log_source_id=11111111-1111-1111-1111-111111111111&active_only=true",
    );
  });

  it("does not link a source with no active anomaly", async () => {
    await renderPage({}, [source({ open_anomaly_count: 0, state: "NORMAL" })]);

    const row = screen.getByRole("row", { name: /LAB Firewall/ });
    expect(within(row).queryByRole("link", { name: "0" })).toBeNull();
  });

  it("shows 'still learning' instead of an expected EPS of 0", async () => {
    // An unbaselined source has no expectation. A 0 would invent one and make
    // the observed value look like a spike against it.
    await renderPage({}, [
      source({ state: "INSUFFICIENT_DATA", expected_eps: null, deviation_ratio: null }),
    ]);

    const row = screen.getByRole("row", { name: /LAB Firewall/ });
    expect(within(row).getByText("still learning")).toBeInTheDocument();
  });

  it("distinguishes a measured zero from a value that was never measured", async () => {
    await renderPage({}, [
      source({ log_source_id: "stopped", name: "Stopped", observed_eps: 0, expected_eps: 5, deviation_ratio: 0 }),
      source({ log_source_id: "unknown", name: "Unknown", observed_eps: null, expected_eps: null, deviation_ratio: null }),
    ]);

    const stopped = screen.getByRole("row", { name: /Stopped/ });
    expect(within(stopped).getByText("0.00")).toBeInTheDocument();
    const unknown = screen.getByRole("row", { name: /Unknown/ });
    expect(within(unknown).getAllByText("—").length).toBeGreaterThan(0);
  });

  it("keeps collection internals off the inventory", async () => {
    // Baseline versions and provenance belong on the source's own page.
    await renderPage();

    expect(screen.queryByText(/Baseline samples/i)).toBeNull();
    expect(screen.queryByText(/Baseline completeness/i)).toBeNull();
    expect(screen.queryByText(/provenance/i)).toBeNull();
  });
});

describe("filters", () => {
  const fleet = [
    source({ log_source_id: "a", name: "LAB Firewall", state: "OPEN", open_anomaly_count: 2 }),
    source({ log_source_id: "b", name: "DC-01 Windows", state: "NORMAL", open_anomaly_count: 0 }),
    source({
      log_source_id: "c",
      name: "Edge Proxy",
      state: "INSUFFICIENT_DATA",
      open_anomaly_count: 0,
    }),
  ];

  it("searches by source name", async () => {
    await renderPage({ q: "firewall" }, fleet);

    expect(screen.getByText("LAB Firewall")).toBeInTheDocument();
    expect(screen.queryByText("DC-01 Windows")).toBeNull();
  });

  it("filters by behavior state", async () => {
    await renderPage({ state: "NORMAL" }, fleet);

    expect(screen.getByText("DC-01 Windows")).toBeInTheDocument();
    expect(screen.queryByText("LAB Firewall")).toBeNull();
  });

  it("filters by active anomaly", async () => {
    await renderPage({ anomaly: "active" }, fleet);

    expect(screen.getByText("LAB Firewall")).toBeInTheDocument();
    expect(screen.queryByText("Edge Proxy")).toBeNull();
  });

  it("filters to unbaselined sources", async () => {
    await renderPage({ data: "insufficient" }, fleet);

    expect(screen.getByText("Edge Proxy")).toBeInTheDocument();
    expect(screen.queryByText("LAB Firewall")).toBeNull();
  });

  it("says a filtered empty result is not a statement about the fleet", async () => {
    await renderPage({ q: "nothing-matches" }, fleet);

    expect(
      screen.getByText(/filtered view, not a statement about the fleet/i),
    ).toBeInTheDocument();
  });

  it("says so differently when nothing is monitored at all", async () => {
    await renderPage({}, []);

    expect(screen.getByText(/No monitored log sources/i)).toBeInTheDocument();
  });

  it("keeps the chosen order when filters are submitted", async () => {
    const { container } = await renderPage({ sort: "name", dir: "asc" }, fleet);

    const form = container.querySelector("form.filters")!;
    expect(form.querySelector('input[name="sort"]')).toHaveValue("name");
    expect(form.querySelector('input[name="dir"]')).toHaveValue("asc");
  });

  it("labels every filter control programmatically", async () => {
    await renderPage({}, fleet);

    expect(screen.getByLabelText(/Search by source name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Behavior state/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Anomaly status/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Baseline adequacy/i)).toBeInTheDocument();
  });
});

describe("sorting", () => {
  const fleet = [
    source({ log_source_id: "a", name: "Mild", deviation_ratio: 1.2, last_event_at: "2026-07-01T00:00:00Z" }),
    source({ log_source_id: "b", name: "Severe", deviation_ratio: 8, last_event_at: "2026-07-20T00:00:00Z" }),
  ];

  function names() {
    return screen
      .getAllByRole("row")
      .slice(1)
      .map((r) => r.textContent ?? "");
  }

  it("sorts by deviation, worst first, by default", async () => {
    await renderPage({}, fleet);

    expect(names()[0]).toContain("Severe");
  });

  it("sorts by last event", async () => {
    await renderPage({ sort: "last_event", dir: "asc" }, fleet);

    expect(names()[0]).toContain("Mild");
  });

  it("sorts by source name", async () => {
    await renderPage({ sort: "name", dir: "asc" }, fleet);

    expect(names()[0]).toContain("Mild");
  });

  it("exposes the current sort direction on the active column only", async () => {
    await renderPage({ sort: "name", dir: "asc" }, fleet);

    expect(screen.getByRole("columnheader", { name: /Source/ })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
    expect(screen.getByRole("columnheader", { name: /Deviation/ })).toHaveAttribute(
      "aria-sort",
      "none",
    );
  });

  it("offers sort controls as links, which are keyboard-operable", async () => {
    await renderPage({}, fleet);

    const header = screen.getByRole("columnheader", { name: /Deviation/ });
    expect(within(header).getByRole("link")).toHaveAttribute(
      "href",
      expect.stringContaining("sort=deviation"),
    );
  });

  it("carries active filters through a sort link", async () => {
    await renderPage({ q: "sev", state: "OPEN" }, fleet);

    const header = screen.getByRole("columnheader", { name: /Source/ });
    const href = within(header).getByRole("link").getAttribute("href") ?? "";
    expect(href).toContain("q=sev");
    expect(href).toContain("state=OPEN");
  });
});

describe("pagination", () => {
  const fleet = Array.from({ length: 60 }, (_, i) =>
    source({ log_source_id: `s-${i}`, name: `Source ${String(i).padStart(2, "0")}` }),
  );

  it("shows one page at a time", async () => {
    await renderPage({ sort: "name", dir: "asc" }, fleet);

    expect(screen.getAllByRole("row").slice(1)).toHaveLength(25);
    expect(screen.getByText("1–25 of 60")).toBeInTheDocument();
  });

  it("pages forward while preserving the filter and sort", async () => {
    await renderPage({ sort: "name", dir: "asc", offset: "25" }, fleet);

    expect(screen.getByText("26–50 of 60")).toBeInTheDocument();
    const next = screen.getByRole("link", { name: "Next" });
    expect(next.getAttribute("href")).toContain("sort=name");
    expect(next.getAttribute("href")).toContain("offset=50");
  });

  it("counts the filtered total, not the fleet", async () => {
    await renderPage({ q: "Source 0", sort: "name", dir: "asc" }, fleet);

    expect(screen.getByText("1–10 of 10")).toBeInTheDocument();
  });
});

describe("accessibility", () => {
  it("contains the table in a labelled scroll region", async () => {
    const { container } = await renderPage();

    const region = container.querySelector(".table-scroll");
    expect(region).not.toBeNull();
    expect(region).toHaveAttribute("role", "region");
    expect(region).toHaveAttribute("aria-label", "Monitored log sources");
    // Focusable, so the region can be scrolled from the keyboard.
    expect(region).toHaveAttribute("tabindex", "0");
  });

  it("marks every column header as a column header", async () => {
    await renderPage();

    const headers = screen.getAllByRole("columnheader");
    expect(headers.length).toBeGreaterThan(0);
    for (const th of headers) {
      expect(th).toHaveAttribute("scope", "col");
    }
  });

  it("gives the table an accessible description", async () => {
    await renderPage();

    expect(
      screen.getByText(/Monitored log sources with observed and expected events/i),
    ).toBeInTheDocument();
  });
});

describe("error states", () => {
  it("reports an unreachable backend rather than an empty fleet", async () => {
    vi.spyOn(api, "sourceBehaviors").mockRejectedValue(new Error("ECONNREFUSED"));
    render(await SourcesIndexPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByRole("alert")).toHaveTextContent(/backend may be unreachable/i);
  });

  it("reports a forbidden response as a permission problem", async () => {
    vi.spyOn(api, "sourceBehaviors").mockRejectedValue(new ApiError(403, "forbidden"));
    render(await SourcesIndexPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByRole("alert")).toHaveTextContent(/do not have permission/i);
  });

  it("reports an expired session on 401", async () => {
    vi.spyOn(api, "sourceBehaviors").mockRejectedValue(new ApiError(401, "unauthorized"));
    render(await SourcesIndexPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByRole("alert")).toHaveTextContent(/session has expired/i);
  });
});
