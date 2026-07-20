import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Pagination } from "./Pagination";

describe("Pagination", () => {
  it("reports a human range, not a zero-based offset", () => {
    render(<Pagination total={100} limit={25} offset={0} basePath="/offenses" />);
    expect(screen.getByText("1–25 of 100")).toBeInTheDocument();
  });

  it("reports the final partial page correctly", () => {
    render(<Pagination total={7} limit={25} offset={0} basePath="/offenses" />);
    expect(screen.getByText("1–7 of 7")).toBeInTheDocument();
  });

  it("shows 0 of 0 when empty rather than 1-0", () => {
    render(<Pagination total={0} limit={25} offset={0} basePath="/offenses" />);
    expect(screen.getByText("0–0 of 0")).toBeInTheDocument();
  });

  it("disables Previous on the first page", () => {
    render(<Pagination total={100} limit={25} offset={0} basePath="/offenses" />);
    expect(screen.queryByRole("link", { name: "Previous" })).toBeNull();
    expect(screen.getByRole("link", { name: "Next" })).toBeInTheDocument();
  });

  it("disables Next on the last page", () => {
    render(<Pagination total={100} limit={25} offset={75} basePath="/offenses" />);
    expect(screen.queryByRole("link", { name: "Next" })).toBeNull();
    expect(screen.getByRole("link", { name: "Previous" })).toBeInTheDocument();
  });

  it("omits offset from the first page link so the URL stays clean", () => {
    render(<Pagination total={100} limit={25} offset={25} basePath="/offenses" />);
    expect(screen.getByRole("link", { name: "Previous" })).toHaveAttribute(
      "href",
      "/offenses",
    );
  });

  it("preserves active filters across pages", () => {
    render(
      <Pagination
        total={100}
        limit={25}
        offset={0}
        basePath="/offenses"
        params={{ search: "firewall", status: "OPEN" }}
      />,
    );
    const next = screen.getByRole("link", { name: "Next" });
    expect(next).toHaveAttribute("href", "/offenses?search=firewall&status=OPEN&offset=25");
  });

  it("drops empty filter values instead of emitting bare keys", () => {
    render(
      <Pagination
        total={100}
        limit={25}
        offset={0}
        basePath="/offenses"
        params={{ search: "", status: undefined }}
      />,
    );
    expect(screen.getByRole("link", { name: "Next" })).toHaveAttribute(
      "href",
      "/offenses?offset=25",
    );
  });

  it("encodes filter values", () => {
    render(
      <Pagination
        total={100}
        limit={25}
        offset={0}
        basePath="/offenses"
        params={{ search: "a b&c" }}
      />,
    );
    const href = screen.getByRole("link", { name: "Next" }).getAttribute("href");
    expect(href).toContain("search=a+b%26c");
  });
});
