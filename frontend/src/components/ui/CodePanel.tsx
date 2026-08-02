"use client";

// A collapsed code panel with a copy button.
//
// Used for the AQL behind each evidence query. Collapsed by default because an
// analyst wants the answer first and the query only when checking it; the
// point of showing the query at all is that a contributor claim should be
// auditable rather than taken on faith.
//
// The code is rendered as text and never as markup. It comes from the backend,
// which writes query structure only — no SEC token, no request header, no
// credential — and React escapes it again on the way into the DOM.

import { useRef, useState } from "react";

export function CodePanel({
  label,
  code,
  meta,
}: {
  /** Names the query, e.g. "Destination port · anomaly window". */
  label: string;
  code: string;
  /** Short context shown beside the label, e.g. row count. */
  meta?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
    } catch {
      // A denied clipboard permission is not an error worth a dialog; the
      // query is selectable on the page either way.
      setCopied(false);
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 2000);
  };

  return (
    <details className="disclosure code-panel">
      <summary className="disclosure-summary">
        <span className="disclosure-label">{label}</span>
        {meta && <span className="disclosure-note">{meta}</span>}
      </summary>
      <div className="disclosure-body">
        <div className="code-actions">
          <button
            type="button"
            className="action secondary"
            onClick={copy}
            aria-label={`Copy the ${label} query to the clipboard`}
          >
            Copy query
          </button>
          {/* Announced when it changes, so the copy is confirmed to a screen
              reader rather than only to the eye. */}
          <span role="status" aria-live="polite" className="muted">
            {copied ? "Query copied to clipboard" : ""}
          </span>
        </div>
        {/* Scrolls inside itself. A long single-line AQL statement would
            otherwise widen the page. */}
        <pre className="code-block">
          <code>{code}</code>
        </pre>
      </div>
    </details>
  );
}
