// One dimension's contributor analysis: which values drove the change.
//
// The critical behavior here is what happens when a dimension is *not*
// AVAILABLE. The section is still rendered, with an explicit statement that the
// field was never checked. Hiding it would leave an analyst to conclude from
// its absence that the dimension was examined and found clean — which is the
// exact inversion of the truth.

import type { ExplanationDimension } from "@/lib/api";
import {
  dimensionLabel,
  dimensionMeaning,
  dimensionTone,
  formatCount,
  formatDelta,
  formatPercentDelta,
  formatRatio,
  formatShare,
} from "@/lib/behavior";

export function ContributorTable({ dimension }: { dimension: ExplanationDimension }) {
  const label = dimensionLabel(dimension.dimension);
  const checked = dimension.availability === "AVAILABLE" || dimension.availability === "TRUNCATED";

  return (
    <section aria-label={`${label} contributors`} style={{ marginTop: 24 }}>
      <h4>
        {label}{" "}
        <span className={`pill ${dimensionTone(dimension.availability)}`}>
          {dimension.availability}
        </span>
      </h4>
      <p className="subtitle">{dimensionMeaning(dimension.availability)}</p>

      {dimension.detail && (
        <p className="subtitle">
          <strong>Reason:</strong> {dimension.detail}
        </p>
      )}

      {/* Cardinality and concentration for this dimension. Rendered even when
          unavailable, where every figure is an em dash rather than a zero. */}
      <table style={{ maxWidth: 720 }}>
        <tbody>
          <tr>
            <td className="muted">Baseline cardinality</td>
            <td>{formatCount(dimension.baseline_distinct_count)}</td>
            <td className="muted">Anomaly cardinality</td>
            <td>{formatCount(dimension.anomaly_distinct_count)}</td>
          </tr>
          <tr>
            <td className="muted">Cardinality ratio</td>
            <td>{formatRatio(dimension.cardinality_ratio)}</td>
            <td className="muted">Concentration change</td>
            <td>
              {formatShare(dimension.baseline_top_share)} →{" "}
              {formatShare(dimension.anomaly_top_share)}
            </td>
          </tr>
          <tr>
            <td className="muted">New values</td>
            <td>{checked ? formatCount(dimension.new_value_count) : "—"}</td>
            <td className="muted">Disappeared values</td>
            <td>{checked ? formatCount(dimension.disappeared_value_count) : "—"}</td>
          </tr>
        </tbody>
      </table>

      {!checked ? null : dimension.contributors.length === 0 ? (
        <div className="notice">
          This dimension was collected and no value stood out as a contributor.
        </div>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>Value</th>
                <th>Baseline</th>
                <th>Anomaly</th>
                <th>Delta</th>
                <th>% delta</th>
                <th>Baseline share</th>
                <th>Anomaly share</th>
                <th>Contribution</th>
                <th>Rank (base → anom)</th>
                <th>Flags</th>
              </tr>
            </thead>
            <tbody>
              {dimension.contributors.map((c) => (
                <tr key={`${c.dimension}:${c.value}`}>
                  <td>
                    {/* Values come from QRadar. Rendered as text, never markup. */}
                    {c.label ? (
                      <>
                        {c.label} <span className="muted">({c.value})</span>
                      </>
                    ) : (
                      c.value
                    )}
                  </td>
                  <td>{formatCount(c.baseline_count)}</td>
                  <td>{formatCount(c.anomaly_count)}</td>
                  <td>{formatDelta(c.absolute_delta)}</td>
                  {/* null for a new value: there is no baseline to be a
                      percentage of, and "0%" would assert no change. */}
                  <td>{formatPercentDelta(c.percent_delta)}</td>
                  <td>{formatShare(c.baseline_share)}</td>
                  <td>{formatShare(c.anomaly_share)}</td>
                  <td>{formatShare(c.contribution_share)}</td>
                  <td>
                    {c.baseline_rank ?? "—"} → {c.anomaly_rank ?? "—"}
                  </td>
                  <td>
                    {c.is_new && <span className="pill warn">new</span>}
                    {c.is_disappeared && (
                      <span className="pill warn">disappeared</span>
                    )}
                    {!c.is_new && !c.is_disappeared && <span className="muted">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {dimension.truncated && (
            <p className="subtitle">
              Truncated at the configured value cap — this is the top of the list,
              not the whole of it.
            </p>
          )}
        </>
      )}
    </section>
  );
}
