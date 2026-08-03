// The local scroll container's two contracts.
//
// The markup contract is testable here. The containment contract is not: it is
// a single CSS declaration whose effect only exists under layout, which jsdom
// does not do. So the stylesheet itself is asserted, in the same spirit as the
// badge tests — guard the structure that lets the CSS work, and leave the
// measured proof to the CDP overflow check.
//
// The defect being guarded: `.sr-only` is `position: absolute`, and every
// table here carries one in its last header cell. With no positioned ancestor
// it resolves against the initial containing block and is laid out at its
// static position, which for a table wider than its scroll container is past
// the right edge of the viewport — so the document grew even though the table
// itself scrolled locally. Measured on /behavior/sources at 1024x768:
// documentElement.scrollWidth 1027 against clientWidth 1009. Tables marked
// `.sticky-actions` were accidentally immune, because their sticky last column
// already establishes a containing block; the plain tables were not.

import { readFileSync } from "node:fs";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TableScroll } from "@/components/ui/TableScroll";

const css = readFileSync("src/app/globals.css", "utf8");

/** The body of a single top-level rule, by selector. */
function rule(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start, `${selector} is missing from globals.css`).toBeGreaterThan(-1);
  return css.slice(start, css.indexOf("}", start));
}

describe("TableScroll", () => {
  it("is a labelled, keyboard-reachable region", () => {
    render(
      <TableScroll label="Monitored log sources">
        <table>
          <tbody>
            <tr>
              <td>row</td>
            </tr>
          </tbody>
        </table>
      </TableScroll>,
    );
    const region = screen.getByRole("region", { name: "Monitored log sources" });
    expect(region.className).toContain("table-scroll");
    // Scrollable by keyboard, not only by dragging.
    expect(region).toHaveAttribute("tabindex", "0");
  });

  it("keeps the table inside its own container", () => {
    const { container } = render(
      <TableScroll label="Sources">
        <table>
          <tbody>
            <tr>
              <td>row</td>
            </tr>
          </tbody>
        </table>
      </TableScroll>,
    );
    expect(container.querySelector(".table-scroll > table")).not.toBeNull();
  });
});

describe("the scroll container's stylesheet contract", () => {
  const body = rule(".table-scroll");

  it("scrolls horizontally rather than clipping", () => {
    expect(body).toContain("overflow-x: auto");
  });

  it("positions, so an absolutely positioned descendant cannot escape it", () => {
    expect(body).toContain("position: relative");
  });

  it("does not hide the overflow it is meant to contain", () => {
    expect(body).not.toContain("overflow-x: hidden");
  });
});

describe("page-level overflow is never masked", () => {
  // The fix for horizontal overflow is structural. If one of these reappears,
  // the CDP check would start passing for the wrong reason.
  it.each(["html", "body", ".layout", ".main", ".page"])(
    "%s does not set overflow-x: hidden",
    (selector) => {
      const start = css.indexOf(`${selector} {`);
      if (start === -1) return; // not every selector carries its own rule
      expect(css.slice(start, css.indexOf("}", start))).not.toContain("overflow-x: hidden");
    },
  );

  it("the shell column can shrink below its content", () => {
    // Without this the widest table widens the grid column, and the page.
    expect(rule(".main")).toContain("min-width: 0");
  });
});
