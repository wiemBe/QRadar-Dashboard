// The shell navigation's accessibility contract.
//
// `lib/nav.test.ts` proves which href should be current for a path. This file
// proves the rendered result: that the answer reaches assistive technology as
// `aria-current`, that the group label is actually associated with its list
// rather than merely sitting above it, and that no two destinations share an
// accessible name — a duplicate would make "go to Sources" ambiguous for
// anyone navigating by name rather than by position.

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppNav } from "./AppNav";
import { NAV } from "@/lib/nav";

const pathname = vi.hoisted(() => ({ current: "/behavior" }));
vi.mock("next/navigation", () => ({ usePathname: () => pathname.current }));

function renderAt(path: string) {
  pathname.current = path;
  return render(<AppNav />);
}

/** Every nav link on the page, by accessible name. */
function links() {
  return screen.getAllByRole("link");
}

describe("AppNav landmarks and names", () => {
  it("is a navigation landmark with its own name", () => {
    renderAt("/behavior");
    // Named, because a page may hold more than one navigation region and an
    // unnamed one cannot be told apart from the others.
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
  });

  it("associates the group label with the list it labels", () => {
    const { container } = renderAt("/behavior");

    // The supporting group is announced by name rather than being an
    // unattached line of text above a list.
    const list = screen.getByRole("list", { name: "Platform" });
    const labelledby = list.getAttribute("aria-labelledby")!;
    expect(labelledby).toBeTruthy();
    // The reference resolves to a real element that carries the text.
    const label = container.querySelector(`#${CSS.escape(labelledby)}`);
    expect(label).not.toBeNull();
    expect(label).toHaveTextContent("Platform");
  });

  it("uses no heading element in the shell", () => {
    // The shell precedes the page's <h1> in document order, so a heading here
    // would put the document's first heading in the sidebar.
    renderAt("/behavior");
    expect(screen.queryAllByRole("heading")).toHaveLength(0);
  });

  it("gives every destination a distinct accessible name", () => {
    renderAt("/behavior");
    const names = links().map((l) => l.textContent?.trim());
    expect(names).toHaveLength(NAV.flatMap((g) => g.items).length);
    // Non-vacuous: there is more than one link, and no name repeats.
    expect(names.length).toBeGreaterThan(1);
    expect(new Set(names).size).toBe(names.length);
  });
});

describe("AppNav current-page state", () => {
  it("marks the active destination and only that one", () => {
    renderAt("/behavior");

    const current = links().filter((l) => l.getAttribute("aria-current") === "page");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("Overview");
  });

  it("leaves every inactive destination unmarked", () => {
    renderAt("/behavior");

    for (const link of links()) {
      if (link.textContent?.trim() === "Overview") continue;
      // Absent, not "false": `aria-current="false"` on six links is noise.
      expect(link).not.toHaveAttribute("aria-current");
    }
  });

  it("marks the owning section on a detail route, not its path prefix", () => {
    // /behavior/sources/{id} belongs to Sources even though /behavior is a
    // prefix of it. Both must not light up.
    renderAt("/behavior/sources/11111111-1111-1111-1111-111111111111");

    const current = links().filter((l) => l.getAttribute("aria-current") === "page");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("Sources");
  });

  it("marks a supporting destination from its own detail route", () => {
    renderAt("/offenses/4812");

    const platform = screen.getByRole("list", { name: "Platform" });
    const current = within(platform)
      .getAllByRole("link")
      .filter((l) => l.getAttribute("aria-current") === "page");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("Offenses");
  });

  it("marks nothing rather than something arbitrary on an unowned path", () => {
    renderAt("/config-changes");
    expect(links().filter((l) => l.getAttribute("aria-current") === "page")).toHaveLength(0);
  });

  it("never marks the root entry from a nested route", () => {
    // "/" would otherwise claim every path on the site by prefix.
    renderAt("/rules/9001");

    const soc = screen.getByRole("link", { name: "SOC Overview" });
    expect(soc).not.toHaveAttribute("aria-current");
  });
});
