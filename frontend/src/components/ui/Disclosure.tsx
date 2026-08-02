"use client";

// A collapsible section.
//
// Built on <details>/<summary>, which is keyboard-operable, screen-reader
// announced and toggleable without JavaScript. A div with an onClick handler
// would need all of that reimplemented, and would lose it silently the first
// time someone edited the markup.
//
// The component is a client one only because callers pass an `onToggle`; the
// element itself works without hydration, so the content stays reachable even
// before the bundle loads.

import type { ReactNode } from "react";

export function Disclosure({
  summary,
  note,
  defaultOpen = false,
  children,
}: {
  summary: ReactNode;
  /** Short right-aligned context, e.g. a status or a count. */
  note?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  return (
    <details className="disclosure" open={defaultOpen}>
      <summary className="disclosure-summary">
        <span className="disclosure-label">{summary}</span>
        {note && <span className="disclosure-note">{note}</span>}
      </summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}
