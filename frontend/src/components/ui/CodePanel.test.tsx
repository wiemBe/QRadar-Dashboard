import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CodePanel } from "./CodePanel";

const AQL =
  'SELECT qid AS "value", COUNT(*) AS "count" FROM events WHERE logsourceid = 227 ' +
  "AND starttime >= 1785695700000 AND starttime < 1785695820000 GROUP BY qid " +
  "ORDER BY COUNT(*) DESC LIMIT 20 START 1785695700000 STOP 1785695820000";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("disclosure", () => {
  it("is collapsed by default", () => {
    const { container } = render(<CodePanel label="QID · anomaly" code={AQL} />);
    expect(container.querySelector("details")).not.toHaveAttribute("open");
  });

  it("names the query so it can be identified while collapsed", () => {
    render(<CodePanel label="QID · anomaly" code={AQL} meta="2 rows" />);
    expect(screen.getByText("QID · anomaly")).toBeInTheDocument();
    expect(screen.getByText("2 rows")).toBeInTheDocument();
  });

  it("uses a native details element, so it opens without JavaScript", () => {
    const { container } = render(<CodePanel label="QID · anomaly" code={AQL} />);
    expect(container.querySelector("details > summary")).not.toBeNull();
  });
});

describe("query text", () => {
  it("renders the statement as text", () => {
    render(<CodePanel label="QID · anomaly" code={AQL} />);
    expect(screen.getByText(/SELECT qid AS "value"/)).toBeInTheDocument();
  });

  it("keeps the START and STOP bounds visible in the statement", () => {
    // The scoping is the fix that made these searches correct; hiding it would
    // remove the evidence that the query was bounded.
    render(<CodePanel label="QID · anomaly" code={AQL} />);
    expect(screen.getByText(/START 1785695700000 STOP 1785695820000/)).toBeInTheDocument();
  });

  it("renders a statement containing markup as text, never as markup", () => {
    const { container } = render(
      <CodePanel label="Q" code={"<img src=x onerror=alert(1)>"} />,
    );
    expect(container.querySelector("img")).toBeNull();
  });

  it("scrolls inside its own box rather than widening the page", () => {
    const { container } = render(<CodePanel label="Q" code={AQL} />);
    expect(container.querySelector("pre")).toHaveClass("code-block");
  });
});

describe("copying", () => {
  // jsdom exposes navigator.clipboard as a getter, and userEvent installs its
  // own stub, so the property is redefined rather than assigned.
  function mockClipboard(impl = vi.fn().mockResolvedValue(undefined)) {
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: impl },
      configurable: true,
      writable: true,
    });
    return impl;
  }

  it("copies the statement", async () => {
    const user = userEvent.setup();
    const writeText = mockClipboard();
    render(<CodePanel label="QID · anomaly" code={AQL} />);

    await user.click(screen.getByRole("button", { name: /Copy the QID · anomaly query/ }));

    expect(writeText).toHaveBeenCalledWith(AQL);
  });

  it("labels the button with the query it copies", () => {
    mockClipboard();
    render(<CodePanel label="QID · anomaly" code={AQL} />);
    expect(
      screen.getByRole("button", { name: "Copy the QID · anomaly query to the clipboard" }),
    ).toBeInTheDocument();
  });

  it("confirms the copy in a live region, not only visually", async () => {
    const user = userEvent.setup();
    mockClipboard();
    render(<CodePanel label="QID · anomaly" code={AQL} />);

    const status = screen.getByRole("status");
    expect(status.textContent).toBe("");
    expect(status).toHaveAttribute("aria-live", "polite");

    await user.click(screen.getByRole("button", { name: /Copy the/ }));
    expect(status).toHaveTextContent("Query copied to clipboard");
  });

  it("stays quiet when the clipboard is denied rather than claiming success", async () => {
    const user = userEvent.setup();
    mockClipboard(vi.fn().mockRejectedValue(new Error("denied")));
    render(<CodePanel label="QID · anomaly" code={AQL} />);

    await user.click(screen.getByRole("button", { name: /Copy the/ }));

    expect(screen.getByRole("status").textContent).toBe("");
  });
});
