import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RunSearchButton } from "./RunSearchButton";
import { ApiError, api, type SearchExecution } from "@/lib/api";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

function execution(over: Partial<SearchExecution> = {}): SearchExecution {
  return {
    id: "e-1", search_id: "s-1", query_version: 1, status: "COMPLETED",
    trigger: "MANUAL", triggered_by: "analyst", ariel_search_id: "ariel-1",
    ariel_status: "COMPLETED", started_at: null, completed_at: null,
    duration_ms: 900, result_count: 42, truncated: false, error_type: null,
    error_message: null, retry_count: 0, threshold_breached: false, ...over,
  };
}

beforeEach(() => {
  refresh.mockClear();
});

describe("RunSearchButton", () => {
  it("runs the stored search and reports the outcome", async () => {
    const run = vi.spyOn(api, "runSearch").mockResolvedValue(execution());
    const user = userEvent.setup();
    render(<RunSearchButton searchId="s-1" />);

    await user.click(screen.getByRole("button", { name: "Run now" }));

    await waitFor(() => expect(run).toHaveBeenCalledWith("s-1"));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("COMPLETED");
    expect(status).toHaveTextContent("42 results");
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("shows a failed run without claiming success", async () => {
    vi.spyOn(api, "runSearch").mockResolvedValue(
      execution({ status: "FAILED", result_count: null, error_type: "ARIEL_TIMEOUT" }),
    );
    const user = userEvent.setup();
    render(<RunSearchButton searchId="s-1" />);

    await user.click(screen.getByRole("button", { name: "Run now" }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("FAILED");
    expect(status).not.toHaveTextContent("results");
  });

  it("disables the button while running so one click buys one Ariel query", async () => {
    const run = vi
      .spyOn(api, "runSearch")
      .mockImplementation(() => new Promise((res) => setTimeout(() => res(execution()), 50)));
    const user = userEvent.setup();
    render(<RunSearchButton searchId="s-1" />);

    const button = screen.getByRole("button", { name: "Run now" });
    await user.click(button);
    expect(await screen.findByRole("button", { name: "Running…" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Running…" }));
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeNull());
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("surfaces a 403 as a permissions message", async () => {
    vi.spyOn(api, "runSearch").mockRejectedValue(new ApiError(403, "search:execute required"));
    const user = userEvent.setup();
    render(<RunSearchButton searchId="s-1" />);

    await user.click(screen.getByRole("button", { name: "Run now" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You do not have permission to perform this action.",
    );
    expect(refresh).not.toHaveBeenCalled();
  });

  it("surfaces a 404 without exposing internals", async () => {
    vi.spyOn(api, "runSearch").mockRejectedValue(new ApiError(404, "search not found"));
    const user = userEvent.setup();
    render(<RunSearchButton searchId="s-1" />);

    await user.click(screen.getByRole("button", { name: "Run now" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This item no longer exists. Refresh the page.",
    );
  });

  it("re-enables the button after a failure so the operator can retry", async () => {
    vi.spyOn(api, "runSearch").mockRejectedValue(new ApiError(500, "boom"));
    const user = userEvent.setup();
    render(<RunSearchButton searchId="s-1" />);

    await user.click(screen.getByRole("button", { name: "Run now" }));

    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled();
  });
});
