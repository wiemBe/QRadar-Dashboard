// The collapsible section's contract.
//
// The component is built on <details>/<summary> deliberately, so the contract
// under test is the native one: the disclosed region is genuinely not visible
// while collapsed, the trigger is focusable and operable from the keyboard,
// and the open state is carried by the element rather than by a class.
//
// A note on what is *not* asserted here: `<summary>` is exposed as a button
// with an expanded state by every current browser, but the accessibility-name
// implementation behind Testing Library's role queries does not map it, so
// `getByRole("button")` finds nothing. That is a limitation of the test
// environment, not of the component, and adding `role="button"` and a manual
// `aria-expanded` to satisfy it would override working native semantics with a
// hand-maintained copy. The state is asserted through `details.open`, which is
// what the browser derives its exposed expanded state from.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Disclosure } from "./Disclosure";

function setup(props: Partial<Parameters<typeof Disclosure>[0]> = {}) {
  const result = render(
    <Disclosure summary="Detection thresholds" {...props}>
      <p>robust z-score -10.4</p>
    </Disclosure>,
  );
  const details = result.container.querySelector("details")!;
  const trigger = result.container.querySelector("summary")!;
  return { ...result, details, trigger };
}

describe("Disclosure", () => {
  it("starts collapsed", () => {
    const { details } = setup();
    expect(details.open).toBe(false);
  });

  it("keeps the disclosed content out of view while collapsed", () => {
    setup();
    // Present in the DOM but not visible: the content is deferred, not
    // deleted, so it stays findable and printable.
    expect(screen.getByText("robust z-score -10.4")).not.toBeVisible();
  });

  it("opens on demand", async () => {
    const { details, trigger } = setup();

    await userEvent.setup().click(trigger);

    expect(details.open).toBe(true);
    expect(screen.getByText("robust z-score -10.4")).toBeVisible();
  });

  it("closes again", async () => {
    const user = userEvent.setup();
    const { details, trigger } = setup();

    await user.click(trigger);
    await user.click(trigger);

    expect(details.open).toBe(false);
    expect(screen.getByText("robust z-score -10.4")).not.toBeVisible();
  });

  it("honours an explicitly open section", () => {
    const { details } = setup({ defaultOpen: true });
    expect(details.open).toBe(true);
    expect(screen.getByText("robust z-score -10.4")).toBeVisible();
  });

  it("exposes the disclosed region as a group", () => {
    setup({ defaultOpen: true });
    expect(screen.getByRole("group")).toBeInTheDocument();
  });

  it("has a keyboard-reachable trigger", () => {
    const { trigger } = setup();
    trigger.focus();
    expect(document.activeElement).toBe(trigger);
  });

  it("names the section in its trigger", () => {
    const { trigger } = setup();
    expect(trigger).toHaveTextContent("Detection thresholds");
  });

  it("shows the note beside the label without hiding it behind the toggle", () => {
    // The note is a status or count, and it is the thing that tells the reader
    // whether opening the section is worth it — so it stays visible while the
    // body does not.
    setup({ note: "unavailable" });
    expect(screen.getByText("unavailable")).toBeVisible();
    expect(screen.getByText("robust z-score -10.4")).not.toBeVisible();
  });

  it("keeps each of several sections independently collapsed", async () => {
    const { container } = render(
      <>
        <Disclosure summary="Baseline history">
          <p>cells</p>
        </Disclosure>
        <Disclosure summary="Collection details">
          <p>buckets</p>
        </Disclosure>
      </>,
    );
    const [first, second] = Array.from(container.querySelectorAll("details"));

    await userEvent.setup().click(container.querySelectorAll("summary")[0]);

    // Opening one must not open the other, and must not require ids that
    // could collide between instances.
    expect(first.open).toBe(true);
    expect(second.open).toBe(false);
    expect(screen.getByText("cells")).toBeVisible();
    expect(screen.getByText("buckets")).not.toBeVisible();
  });
});
