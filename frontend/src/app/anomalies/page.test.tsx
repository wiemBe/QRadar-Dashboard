import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

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

let listSpy: MockInstance<typeof api.anomalies>;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "sourceBehaviors").mockResolvedValue(SOURCES);
});

async function renderPage(params: Record<string, string | undefined> = {}) {
  render(await AnomaliesPage({ searchParams: Promise.resolve(params) }));
}

describe("the table", () => {
  it("renders every required column for a row", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockResolvedValue(page([anomaly()]));
    await renderPage();

    const row = screen.getByRole("row", { name: /LAB Firewall/ });
    expect(row).toHaveTextContent("VOLUME_SPIKE");
    expect(row).toHaveTextContent("OPEN");
    expect(row).toHaveTextContent("HIGH");
    expect(row).toHaveTextContent("6.00");
    expect(row).toHaveTextContent("2.00");
    expect(row).toHaveTextContent("3.00x");
    expect(row).toHaveTextContent("5m");
    expect(row).toHaveTextContent("PARTIAL");
    expect(within(row).getByRole("link", { name: "Investigate" })).toHaveAttribute(
      "href",
      "/anomalies/a-1",
    );
  });

  it("shows a still-running anomaly as running, not as a 0s duration", async () => {
    listSpy = vi
      .spyOn(api, "anomalies")
      .mockResolvedValue(page([anomaly({ duration_seconds: null, anomaly_end: null })]));
    await renderPage();

    expect(screen.getByText("running")).toBeInTheDocument();
  });

  it("falls back to the id when a source name is missing", async () => {
    listSpy = vi
      .spyOn(api, "anomalies")
      .mockResolvedValue(page([anomaly({ log_source_name: null })]));
    await renderPage();

    expect(screen.getByRole("link", { name: SOURCE_ID })).toBeInTheDocument();
  });

  it("renders every evidence status it is given", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockResolvedValue(
      page([
        anomaly({ id: "a-1", evidence_status: "COMPLETE" }),
        anomaly({ id: "a-2", evidence_status: "PENDING" }),
        anomaly({ id: "a-3", evidence_status: "UNAVAILABLE" }),
        anomaly({ id: "a-4", evidence_status: "FAILED" }),
      ]),
    );
    await renderPage();

    // Scoped to the table: the filter dropdown lists the same labels.
    const table = screen.getByRole("table");
    for (const status of ["COMPLETE", "PENDING", "UNAVAILABLE", "FAILED"]) {
      expect(within(table).getByText(status)).toBeInTheDocument();
    }
  });
});

describe("filters", () => {
  it("forwards recognized filters to the API", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    await renderPage({
      log_source_id: SOURCE_ID,
      anomaly_type: "VOLUME_DROP",
      state: "OPEN",
      severity: "HIGH",
      evidence_status: "FAILED",
    });

    expect(listSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        log_source_id: SOURCE_ID,
        anomaly_type: "VOLUME_DROP",
        state: "OPEN",
        severity: "HIGH",
        evidence_status: "FAILED",
      }),
    );
  });

  it("does not forward an unrecognized filter value", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    await renderPage({ state: "'; DROP TABLE--", log_source_id: "not-a-uuid" });

    expect(listSpy).toHaveBeenCalledWith(
      expect.objectContaining({ state: undefined, log_source_id: undefined }),
    );
  });

  it("says an empty result is filtered rather than fleet-wide", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    await renderPage({ state: "OPEN" });

    expect(screen.getByText(/filtered view, not a statement about the fleet/i)).toBeInTheDocument();
  });

  it("populates the source dropdown from the behavior API", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    await renderPage();

    expect(
      within(screen.getByLabelText("Log source")).getByRole("option", { name: "LAB Firewall" }),
    ).toBeInTheDocument();
  });

  it("still renders the table when the source dropdown cannot be loaded", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockResolvedValue(page([anomaly()]));
    vi.spyOn(api, "sourceBehaviors").mockRejectedValue(new Error("down"));
    await renderPage();

    expect(screen.getByRole("row", { name: /LAB Firewall/ })).toBeInTheDocument();
  });
});

describe("time range", () => {
  it("forwards a valid range", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    await renderPage({ since: "2026-07-19T10:00", until: "2026-07-20T10:00" });

    const call = listSpy.mock.calls[0][0] as { since?: string; until?: string };
    expect(Date.parse(call.since!)).toBeLessThan(Date.parse(call.until!));
  });

  it("clamps an over-wide range and says so", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    await renderPage({ since: "1990-01-01T00:00", until: "2026-07-20T10:00" });

    expect(screen.getByText(/clamped/i)).toBeInTheDocument();
  });
});

describe("pagination", () => {
  it("requests the offset it was given", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockResolvedValue({
      items: [anomaly()],
      total: 100,
      limit: 25,
      offset: 25,
    });
    await renderPage({ offset: "25" });

    expect(listSpy).toHaveBeenCalledWith(expect.objectContaining({ offset: 25 }));
  });

  it("preserves the active filters in the next-page link", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockResolvedValue({
      items: [anomaly()],
      total: 100,
      limit: 25,
      offset: 0,
    });
    await renderPage({ state: "OPEN", severity: "HIGH" });

    const next = screen.getByRole("link", { name: "Next" });
    expect(next).toHaveAttribute("href", expect.stringContaining("state=OPEN"));
    expect(next).toHaveAttribute("href", expect.stringContaining("severity=HIGH"));
    expect(next).toHaveAttribute("href", expect.stringContaining("offset=25"));
  });

  it("reports the total from the server, not the page length", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockResolvedValue({
      items: [anomaly()],
      total: 100,
      limit: 25,
      offset: 0,
    });
    await renderPage();

    expect(screen.getByText(/of 100/)).toBeInTheDocument();
  });
});

describe("empty and error states", () => {
  it("distinguishes an empty fleet from a filtered empty result", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    await renderPage();

    expect(screen.getByText(/No anomalies have been detected/i)).toBeInTheDocument();
    // And points at the reason a zero might be misleading.
    expect(screen.getByText(/without an adequate baseline are not being judged/i)).toBeInTheDocument();
  });

  it("reports a failed request as a failure, not as zero anomalies", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockRejectedValue(new Error("ECONNREFUSED"));
    await renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent(/could not be loaded/i);
    expect(screen.queryByText(/No anomalies have been detected/i)).toBeNull();
  });

  it("reports a forbidden response as a permission problem", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockRejectedValue(new ApiError(403, "forbidden"));
    await renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent(/do not have permission/i);
  });

  it("renders no anomaly data to an unauthenticated caller", async () => {
    listSpy = vi.spyOn(api, "anomalies").mockRejectedValue(new ApiError(401, "unauthorized"));
    await renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent(/session has expired/i);
    expect(screen.queryByRole("row", { name: /LAB Firewall/ })).toBeNull();
  });
});
