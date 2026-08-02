// A table's local scroll container.
//
// Two jobs, both structural. It keeps a wide table's overflow inside its own
// section so the table can never widen the page — the mechanism behind the
// horizontal page overflow this replaces. And it is a focusable labelled
// region, so the content that scrolls can be reached and scrolled with the
// keyboard rather than only by dragging.

import type { ReactNode } from "react";

export function TableScroll({
  label,
  children,
}: {
  /** Names the region. Should describe the table, e.g. "Sources". */
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="table-scroll" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}
