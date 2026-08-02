import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomaliesPage from "./page";
import {
  ApiError,
  api,
  type AnomalySummary,
  type Page,
  type SourceBehavior,
} from "@/lib/api";

const SOURCE_ID = "11111111-1111-1111-1111-111111111111";

function anomaly(over: Partial<AnomalySummary> = {}): AnomalySummary {
  return {
    id: "a-1",
    log_source_id: SOURCE_ID,
    log_source_name: "LAB Firewall",
    anomaly_type: "VOLUME_SPIKE",
    state: "OPEN",
    severity: "HIGH",
    observed_value: 6,
    expected_value: 2,
    deviation_ratio: 3,
    robust_z: 13.5,
    absolute_delta: 1200,
    consecutive_buckets: 2,
    confidence: 0.82,
    detected_at: "2026-07-20T10:05:00Z",
    opened_at: "2026-07-20T10:05:00Z",
    anomaly_start: "2026-07-20T10:00:00Z",
    anomaly_end: "2026-07-20T10:05:00Z",
    resolved_at: null,
    duration_seconds: 300,
    evidence_status: "PARTIAL",
    suppressed: false,
    explanation: null,
    ...over,
  };
}

function page(items: AnomalySummary[], total = items.length): Page<AnomalySummary> {
  return { items, total, limit: 25, offset: 0 };
}

const SOURCES: SourceBehavior[] = [
  {
    log_source_id: SOURCE_ID,
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
    last_bucket_at: null,
    last_event_at: null,
    open_anomaly_count: 1,
  },
];

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderPage(
  params: Record<string, string | undefined> = {},
  items: AnomalySummary[] = [anomaly()],
  total = items.length,
) {
  vi.spyOn(api, "anomalies").mockResolvedValue(page(items, total));
  vi.spyOn(api, "sourceBehaviors").mockResolvedValue(SOURCES);
  return render(await AnomaliesPage({ searchParams: Promise.resolve(params) }));
}

describe("default columns", () => {
  it("shows nine triage columns rather than eleven", () => {
    // At 1024 px the eleven-column table pushed Started, Duration, Evidence and
    // the detail link past the viewport.
    return renderPage().then(() => {
      const headers = screen
        .getAllByRole("columnheader")
        .map((h) => h.textContent?.trim());
      expect(headers).toEqual([
        "Source",
        "Detector",
        "State",
        "Observed → Expected",
        "Deviation",
        "Severity",
        "Started",
        "Evidence",
        "Actions",
      ]);
    });
  });

  it("keeps observed and expected together in one column", async () => {
    await renderPage();
    const row = screen.getByRole("row", { name: /LAB Firewall/ });
    const cells = within(row).getAllByRole("cell");
    expect(cells[3]).toHaveTextContent("6.00 → 2.00");
  });

  it("does not expose version stamps in the default row", async () => {
    await renderPage();
    expect(screen.queryByText(/policy version/i)).toBeNull();
    expect(screen.queryByText(/baseline version/i)).toBeNull();
  });

  it("does not expose duration or confidence in the default row", async () => {
    await renderPage();
    const row = screen.getByRole("row", { name: /LAB Firewall/ });
    expect(within(row).queryByText("5m")).toBeNull();
    expect(within(row).queryByText("0.82")).toBeNull();
  });
});

describe("status priority", () => {
  it("gives the lifecycle state the strong badge and the others quiet ones", async () => {
    // State decides whether this needs work now; severity and evidence are
    // real but subordinate, and were previously equal in weight.
    await renderPage();
    const row = screen.getByRole("row", { name: /LAB Firewall/ });

    expect(within(row).getByText("OPEN").className).toContain("pill-strong");
    expect(within(row).getByText("HIGH").className).toContain("pill-quiet");
    expect(within(row).getByText("PARTIAL").className).toContain("pill-quiet");
  });

  it("states each status as text, so none depends on colour", async () => {
    await renderPage();
    const row = screen.getByRole("row", { name: /LAB Firewall/ });
    expect(within(row).getByText("OPEN")).toBeInTheDocument();
    expect(within(row).getByText("HIGH")).toBeInTheDocument();
    expect(within(row).getByText("PARTIAL")).toBeInTheDocument();
  });
});

describe("row disclosure", () => {
  it("reveals the moved fields on demand", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByRole("button", { name: /Show technical detail/ }));

    expect(screen.getByText("Absolute delta")).toBeInTheDocument();
    expect(screen.getByText("Duration")).toBeInTheDocument();
    expect(screen.getByText("Confidence")).toBeInTheDocument();
    expect(screen.getByText("Anomaly end")).toBeInTheDocument();
    expect(screen.getByText("Resolved")).toBeInTheDocument();
  });

  it("reports its expanded state", async () => {
    const user = userEvent.setup();
    await renderPage();

    const toggle = screen.getByRole("button", { name: /Show technical detail/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(
      screen.getByRole("button", { name: /Hide technical detail/ }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("shows a still-running anomaly as running, not as a 0s duration", async () => {
    const user = userEvent.setup();
    await renderPage({}, [anomaly({ duration_seconds: null, anomaly_end: null })]);

    await user.click(screen.getByRole("button", { name: /Show technical detail/ }));

    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByText("still running")).toBeInTheDocument();
  });
});

describe("filters", () => {
  it("shows four filters by default", async () => {
    await renderPage();
    expect(screen.getByLabelText("Lifecycle state")).toBeInTheDocument();
    expect(screen.getByLabelText("Detector type")).toBeInTheDocument();
    expect(screen.getByLabelText("Severity")).toBeInTheDocument();
    expect(screen.getByLabelText("Start time")).toBeInTheDocument();
    expect(screen.getByLabelText("End time")).toBeInTheDocument();
  });

  it("keeps source, instance and evidence behind a disclosure", async () => {
    const { container } = await renderPage();
    const more = container.querySelector("details.filter-more");
    expect(more).not.toBeNull();
    expect(more).not.toHaveAttribute("open");
    expect(within(more as HTMLElement).getByLabelText("Log source")).toBeInTheDocument();
    expect(
      within(more as HTMLElement).getByLabelText("Evidence status"),
    ).toBeInTheDocument();
  });

  it("opens the disclosure when one of its filters is active", async () => {
    // A filtered view must never hide the control responsible for it.
    const { container } = await renderPage({ evidence_status: "FAILED" });
    expect(container.querySelector("details.filter-more")).toHaveAttribute("open");
  });

  it("labels every control programmatically", async () => {
    await renderPage();
    for (const label of [
      "Lifecycle state",
      "Detector type",
      "Severity",
      "Start time",
      "End time",
      "Log source",
      "QRadar instance",
      "Evidence status",
    ]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
  });
});

describe("active filter chips", () => {
  it("renders no chips when nothing is filtered", async () => {
    const { container } = await renderPage();
    expect(container.querySelector(".filter-chips")).toBeNull();
  });

  it("names each active filter", async () => {
    await renderPage({ state: "OPEN", severity: "HIGH" });
    expect(screen.getByText(/State: OPEN/)).toBeInTheDocument();
    expect(screen.getByText(/Severity: HIGH/)).toBeInTheDocument();
  });

  it("resolves a source id to its name", async () => {
    await renderPage({ log_source_id: SOURCE_ID });
    expect(screen.getByText(/Source: LAB Firewall/)).toBeInTheDocument();
  });

  it("lets each chip remove only itself", async () => {
    await renderPage({ state: "OPEN", severity: "HIGH" });
    const chip = screen.getByRole("link", { name: /State: OPEN/ });
    const href = chip.getAttribute("href") ?? "";
    expect(href).toContain("severity=HIGH");
    expect(href).not.toContain("state=OPEN");
  });

  it("offers a clear-all that drops every filter", async () => {
    await renderPage({ state: "OPEN", severity: "HIGH" });
    expect(screen.getByRole("link", { name: "Clear all" })).toHaveAttribute(
      "href",
      "/anomalies",
    );
  });
});

describe("query parsing", () => {
  it("ignores a filter value outside the allow-list", async () => {
    // The value arrives from a URL a third party may have written.
    const { container } = await renderPage({ state: "<script>alert(1)</script>" });
    expect(container.querySelector(".filter-chips")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });

  it("ignores a malformed source id", async () => {
    const { container } = await renderPage({ log_source_id: "not-a-uuid" });
    expect(container.querySelector(".filter-chips")).toBeNull();
  });

  it("treats a nonsense offset as the first page", async () => {
    await renderPage({ offset: "abc" }, [anomaly()], 1);
    expect(screen.getByText("1–1 of 1")).toBeInTheDocument();
  });
});

describe("pagination", () => {
  it("carries the active filters into the paging links", async () => {
    await renderPage({ state: "OPEN" }, [anomaly()], 60);
    const next = screen.getByRole("link", { name: "Next" });
    expect(next.getAttribute("href")).toContain("state=OPEN");
    expect(next.getAttribute("href")).toContain("offset=25");
  });
});

describe("empty and error states", () => {
  it("distinguishes a filtered empty result from an empty fleet", async () => {
    await renderPage({ state: "OPEN" }, []);
    expect(
      screen.getByText(/filtered view, not a statement about the fleet/i),
    ).toBeInTheDocument();
  });

  it("points an unfiltered empty result at the unbaselined count", async () => {
    await renderPage({}, []);
    expect(
      screen.getByText(/Sources without an adequate baseline are not being judged/i),
    ).toBeInTheDocument();
  });

  it("reports an unreachable backend as a failure", async () => {
    vi.spyOn(api, "anomalies").mockRejectedValue(new Error("ECONNREFUSED"));
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue(SOURCES);
    render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByRole("alert")).toHaveTextContent(/backend may be unreachable/i);
  });

  it("reports a forbidden response as a permission problem", async () => {
    vi.spyOn(api, "anomalies").mockRejectedValue(new ApiError(403, "forbidden"));
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue(SOURCES);
    render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByRole("alert")).toHaveTextContent(/do not have permission/i);
  });

  it("still renders the table when only the source list fails", async () => {
    vi.spyOn(api, "anomalies").mockResolvedValue(page([anomaly()]));
    vi.spyOn(api, "sourceBehaviors").mockRejectedValue(new Error("ECONNREFUSED"));
    render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("row", { name: /LAB Firewall/ })).toBeInTheDocument();
  });
});

describe("accessibility", () => {
  it("has one page heading", async () => {
    await renderPage();
    expect(screen.getByRole("heading", { level: 1, name: "Anomalies" })).toBeInTheDocument();
  });

  it("contains the table in a labelled scroll region", async () => {
    const { container } = await renderPage();
    const region = container.querySelector(".table-scroll");
    expect(region).toHaveAttribute("role", "region");
    expect(region).toHaveAttribute("aria-label", "Detected anomalies");
    expect(region).toHaveAttribute("tabindex", "0");
  });

  it("marks every header as a column header", async () => {
    await renderPage();
    for (const th of screen.getAllByRole("columnheader")) {
      expect(th).toHaveAttribute("scope", "col");
    }
  });

  it("gives the table an accessible caption", async () => {
    await renderPage();
    expect(
      screen.getByText(/Detected anomalies with lifecycle state/i),
    ).toBeInTheDocument();
  });
});

describe("links", () => {
  it("links a row to its investigation and its source", async () => {
    await renderPage();
    const row = screen.getByRole("row", { name: /LAB Firewall/ });
    expect(within(row).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "/anomalies/a-1",
    );
    expect(within(row).getByRole("link", { name: "LAB Firewall" })).toHaveAttribute(
      "href",
      `/behavior/sources/${SOURCE_ID}`,
    );
  });

  it("falls back to the source id when the name is absent", async () => {
    await renderPage({}, [anomaly({ log_source_name: null })]);
    expect(screen.getByRole("link", { name: SOURCE_ID })).toBeInTheDocument();
  });
});
