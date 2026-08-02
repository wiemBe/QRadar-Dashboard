import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Tabs } from "./Tabs";

const tabs = [
  { id: "evidence", label: "Evidence", content: () => <p>evidence panel</p> },
  { id: "lifecycle", label: "Lifecycle", content: () => <p>lifecycle panel</p> },
  { id: "technical", label: "Technical details", content: () => <p>technical panel</p> },
];

describe("structure", () => {
  it("uses the ARIA tabs roles", () => {
    render(<Tabs tabs={tabs} label="Investigation detail" />);
    expect(screen.getByRole("tablist", { name: "Investigation detail" })).toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(3);
    expect(screen.getByRole("tabpanel")).toBeInTheDocument();
  });

  it("defaults to the first tab", () => {
    render(<Tabs tabs={tabs} label="Investigation detail" />);
    expect(screen.getByRole("tab", { name: "Evidence" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("evidence panel")).toBeInTheDocument();
  });

  it("renders only the active panel", () => {
    // Rendering all three would put the technical detail back into the
    // document that the tabs exist to defer.
    render(<Tabs tabs={tabs} label="Investigation detail" />);
    expect(screen.queryByText("lifecycle panel")).toBeNull();
    expect(screen.queryByText("technical panel")).toBeNull();
    expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
  });

  it("associates each panel with its tab", () => {
    render(<Tabs tabs={tabs} label="Investigation detail" />);
    const tab = screen.getByRole("tab", { name: "Evidence" });
    const panel = screen.getByRole("tabpanel");
    expect(tab).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", tab.id);
  });

  it("honours an explicit initial tab", () => {
    render(<Tabs tabs={tabs} label="Investigation detail" initialId="technical" />);
    expect(screen.getByText("technical panel")).toBeInTheDocument();
  });
});

describe("mouse interaction", () => {
  it("switches panels on click", async () => {
    const user = userEvent.setup();
    render(<Tabs tabs={tabs} label="Investigation detail" />);

    await user.click(screen.getByRole("tab", { name: "Lifecycle" }));

    expect(screen.getByText("lifecycle panel")).toBeInTheDocument();
    expect(screen.queryByText("evidence panel")).toBeNull();
  });
});

describe("keyboard interaction", () => {
  it("moves right and left with the arrow keys", async () => {
    const user = userEvent.setup();
    render(<Tabs tabs={tabs} label="Investigation detail" />);

    screen.getByRole("tab", { name: "Evidence" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByText("lifecycle panel")).toBeInTheDocument();

    await user.keyboard("{ArrowLeft}");
    expect(screen.getByText("evidence panel")).toBeInTheDocument();
  });

  it("wraps around at both ends", async () => {
    const user = userEvent.setup();
    render(<Tabs tabs={tabs} label="Investigation detail" />);

    screen.getByRole("tab", { name: "Evidence" }).focus();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByText("technical panel")).toBeInTheDocument();

    await user.keyboard("{ArrowRight}");
    expect(screen.getByText("evidence panel")).toBeInTheDocument();
  });

  it("jumps to the ends with Home and End", async () => {
    const user = userEvent.setup();
    render(<Tabs tabs={tabs} label="Investigation detail" />);

    screen.getByRole("tab", { name: "Evidence" }).focus();
    await user.keyboard("{End}");
    expect(screen.getByText("technical panel")).toBeInTheDocument();

    await user.keyboard("{Home}");
    expect(screen.getByText("evidence panel")).toBeInTheDocument();
  });

  it("moves focus with the selection, not only the panel", async () => {
    const user = userEvent.setup();
    render(<Tabs tabs={tabs} label="Investigation detail" />);

    screen.getByRole("tab", { name: "Evidence" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Lifecycle" })).toHaveFocus();
  });

  it("keeps only the active tab in the page tab order", async () => {
    // Roving tabindex: a keyboard user reaches the panel in one Tab press
    // rather than stepping through every tab first.
    const user = userEvent.setup();
    render(<Tabs tabs={tabs} label="Investigation detail" />);

    expect(screen.getByRole("tab", { name: "Evidence" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Lifecycle" })).toHaveAttribute("tabindex", "-1");

    await user.click(screen.getByRole("tab", { name: "Lifecycle" }));
    expect(screen.getByRole("tab", { name: "Lifecycle" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Evidence" })).toHaveAttribute("tabindex", "-1");
  });
});

describe("degenerate input", () => {
  it("renders nothing rather than crashing on an empty tab set", () => {
    const { container } = render(<Tabs tabs={[]} label="Empty" />);
    expect(container).toBeEmptyDOMElement();
  });
});
