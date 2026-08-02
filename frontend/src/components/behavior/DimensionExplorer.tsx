"use client";

// The evidence tab's dimension selector.
//
// Replaces ten contributor tables rendered one after another — 26 tables and
// 125 column headers on the live Phase A anomaly — with one table at a time
// and a list showing what every dimension's status was.
//
// The list is the coverage statement, and it must be complete. A dimension
// that was never collected stays visible with its status, because reading down
// the list is how an analyst learns which fields the conclusion actually rests
// on. A hidden UNAVAILABLE dimension would read as having been checked and
// found clean.

import { useState } from "react";

import { ContributorTable } from "@/components/behavior/ContributorTable";
import type { ExplanationDimension } from "@/lib/api";
import { dimensionLabel, dimensionTone } from "@/lib/behavior";
import { defaultDimension, dimensionPriority } from "@/lib/contributors";

export function DimensionExplorer({
  dimensions,
}: {
  dimensions: ExplanationDimension[];
}) {
  const ordered = [...dimensions].sort(
    (a, b) => dimensionPriority(a.dimension) - dimensionPriority(b.dimension),
  );
  const initial = defaultDimension(ordered);
  const [selected, setSelected] = useState<string | null>(initial?.dimension ?? null);

  if (ordered.length === 0) {
    return (
      <div className="notice">
        No dimension analysis is stored for this anomaly. No field has been
        examined, so nothing here has been ruled out.
      </div>
    );
  }

  const active =
    ordered.find((d) => d.dimension === selected) ?? initial ?? ordered[0];

  const unchecked = ordered.filter(
    (d) => d.availability === "UNAVAILABLE" || d.availability === "FAILED",
  );

  return (
    <div className="dimension-explorer">
      <div className="dimension-list">
        <h4 className="sr-only">Dimensions</h4>
        <ul>
          {ordered.map((d) => {
            const current = d.dimension === active.dimension;
            return (
              <li key={d.dimension}>
                <button
                  type="button"
                  className={`dimension-button${current ? " dimension-button-active" : ""}`}
                  // The selected dimension is a current item within the list,
                  // announced rather than only highlighted.
                  aria-current={current ? "true" : undefined}
                  onClick={() => setSelected(d.dimension)}
                >
                  <span className="dimension-button-label">
                    {dimensionLabel(d.dimension)}
                  </span>
                  <span className={`pill pill-quiet ${dimensionTone(d.availability)}`}>
                    {d.availability}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>

        {unchecked.length > 0 && (
          <p className="metric-note">
            {unchecked.length} dimension
            {unchecked.length === 1 ? " was" : "s were"} not checked:{" "}
            {unchecked.map((d) => dimensionLabel(d.dimension)).join(", ")}. No
            conclusion about {unchecked.length === 1 ? "it" : "them"} follows
            from this investigation.
          </p>
        )}
      </div>

      <div className="dimension-detail">
        <ContributorTable key={active.dimension} dimension={active} />
      </div>
    </div>
  );
}
