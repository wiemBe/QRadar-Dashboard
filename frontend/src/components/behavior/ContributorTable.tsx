"use client";

// One dimension's contributor analysis.
//
// Five columns by default — value, baseline, anomaly, delta, contribution —
// which are the ones that answer "what changed and by how much". The other
// six the API returns (percent delta, both shares, both ranks, cardinality
// ratio) are one keystroke away per row rather than permanently on screen:
// they matter when interrogating a specific value, not when scanning a list.
//
// The critical behaviour is what happens when a dimension is *not* AVAILABLE.
// The panel still renders, with an explicit statement that the field was never
// checked. Hiding it would leave an analyst to conclude from its absence that
// the dimension was examined and found clean — the exact inversion of the
// truth.

import { Fragment, useState } from "react";

import { TableScroll } from "@/components/ui/TableScroll";
import type { ExplanationDimension } from "@/lib/api";
import {
  contributorsAreShowable,
  dimensionLabel,
  dimensionMeaning,
  dimensionTone,
  formatCount,
  formatDelta,
  formatPercentDelta,
  formatRatio,
  formatShare,
  newAndDisappearedAreDeterminate,
} from "@/lib/behavior";

/** What a figure reads as when a cap prevents a defensible answer. */
const INDETERMINATE = "indeterminate";

export function ContributorTable({ dimension }: { dimension: ExplanationDimension }) {
  const [expanded, setExpanded] = useState<string | null>(null);

  const label = dimensionLabel(dimension.dimension);
  const checked = contributorsAreShowable(dimension.availability);
  // Truncation makes the counts an artifact of the cap rather than a finding,
  // so they are withheld even though the contributor rows are shown.
  const countsDeterminate = newAndDisappearedAreDeterminate(dimension.availability);

  const countCell = (value: number | null) =>
    countsDeterminate ? (
      formatCount(value)
    ) : (
      <span className="muted">{INDETERMINATE}</span>
    );

  return (
    <section aria-label={`${label} contributors`} className="dimension-panel">
      <div className="row dimension-panel-head">
        <h4>{label}</h4>
        <span className={`pill ${dimensionTone(dimension.availability)}`}>
          {dimension.availability}
        </span>
      </div>
      <p className="subtitle">{dimensionMeaning(dimension.availability)}</p>

      {dimension.detail && (
        <p className="subtitle">
          {/* Backend-sanitized: a reason, never a stack trace. */}
          <strong>Reason:</strong> {dimension.detail}
        </p>
      )}

      {/* Compact metadata. Under a cap the cardinality and new/disappeared
          figures are artifacts of the limit rather than observations, so they
          are named indeterminate rather than shown as the backend's zeroes. */}
      <dl className="dimension-meta">
        <div>
          <dt>Baseline cardinality</dt>
          <dd className="num">{countCell(dimension.baseline_distinct_count)}</dd>
        </div>
        <div>
          <dt>Anomaly cardinality</dt>
          <dd className="num">{countCell(dimension.anomaly_distinct_count)}</dd>
        </div>
        <div>
          <dt>Concentration</dt>
          <dd className="num">
            {formatShare(dimension.baseline_top_share)} →{" "}
            {formatShare(dimension.anomaly_top_share)}
          </dd>
        </div>
        <div>
          <dt>New values</dt>
          <dd className="num">{countCell(dimension.new_value_count)}</dd>
        </div>
        <div>
          <dt>Disappeared values</dt>
          <dd className="num">{countCell(dimension.disappeared_value_count)}</dd>
        </div>
      </dl>

      {!checked ? (
        // No empty table: an unavailable or failed dimension has nothing to
        // show, and an empty table would read as "checked, nothing found".
        <p className={`banner ${dimension.availability === "FAILED" ? "crit" : ""}`}>
          <span>
            <strong>{dimension.availability}.</strong>{" "}
            {dimension.availability === "FAILED"
              ? "The query for this dimension did not complete, so it has not been checked."
              : "This field was not populated by the QRadar parser for this log source, so it has not been checked. No conclusion follows from the absence of contributors here."}
          </span>
        </p>
      ) : dimension.contributors.length === 0 ? (
        <div className="notice">
          This dimension was collected and no value stood out as a contributor.
        </div>
      ) : (
        <>
          {dimension.truncated && (
            <p className="dimension-truncation">
              Truncated at the configured value cap — this is the top of the
              list, not the whole of it.
            </p>
          )}
          {/* Distinct from the section's own label: two nested regions with
              the same accessible name are indistinguishable when navigating
              landmarks. */}
          <TableScroll label={`${label} contributor rows`}>
            <table>
              <caption className="sr-only">
                {label} values ranked by their contribution to the change, with
                baseline and anomaly counts.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Value</th>
                  <th scope="col">Baseline</th>
                  <th scope="col">Anomaly</th>
                  <th scope="col">Delta</th>
                  <th scope="col">Contribution</th>
                  <th scope="col">
                    <span className="sr-only">Row detail</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {dimension.contributors.map((c) => {
                  const key = `${c.dimension}:${c.value}`;
                  const open = expanded === key;
                  const name = c.label ?? c.value;
                  return (
                    <Fragment key={key}>
                      <tr>
                        <td>
                          {/* Values come from QRadar. Rendered as text, never
                              as markup. */}
                          {c.label ? (
                            <>
                              {c.label} <span className="muted">({c.value})</span>
                            </>
                          ) : (
                            c.value
                          )}{" "}
                          {c.is_new && <span className="pill pill-quiet warn">new</span>}
                          {c.is_disappeared && (
                            <span className="pill pill-quiet warn">disappeared</span>
                          )}
                        </td>
                        <td className="num">{formatCount(c.baseline_count)}</td>
                        <td className="num">{formatCount(c.anomaly_count)}</td>
                        <td className="num">{formatDelta(c.absolute_delta)}</td>
                        <td className="num">
                          {/* Magnitude: the share is signed on a drop, and
                              "-95.9%" is not how a reduction is described. */}
                          {formatShare(
                            c.contribution_share == null
                              ? null
                              : Math.abs(c.contribution_share),
                          )}
                        </td>
                        <td>
                          <button
                            type="button"
                            className="row-toggle"
                            aria-expanded={open}
                            aria-label={`${open ? "Hide" : "Show"} detail for ${name}`}
                            onClick={() => setExpanded(open ? null : key)}
                          >
                            {open ? "Less" : "More"}
                          </button>
                        </td>
                      </tr>
                      {open && (
                        <tr className="row-detail">
                          <td colSpan={6}>
                            <dl className="dimension-meta">
                              <div>
                                <dt>Percent delta</dt>
                                {/* null for a new value: there is no baseline
                                    to be a percentage of, and "0%" would
                                    assert no change. */}
                                <dd className="num">
                                  {formatPercentDelta(c.percent_delta)}
                                </dd>
                              </div>
                              <div>
                                <dt>Baseline share</dt>
                                <dd className="num">{formatShare(c.baseline_share)}</dd>
                              </div>
                              <div>
                                <dt>Anomaly share</dt>
                                <dd className="num">{formatShare(c.anomaly_share)}</dd>
                              </div>
                              <div>
                                <dt>Rank, baseline to anomaly</dt>
                                <dd className="num">
                                  {c.baseline_rank ?? "—"} → {c.anomaly_rank ?? "—"}
                                </dd>
                              </div>
                              <div>
                                <dt>Cardinality ratio</dt>
                                <dd className="num">
                                  {formatRatio(dimension.cardinality_ratio)}
                                </dd>
                              </div>
                            </dl>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </TableScroll>
        </>
      )}
    </section>
  );
}
