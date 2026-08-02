// One row per requested dimension: what was checked, and what was not.
//
// This table is the page's coverage statement. Reading down the availability
// column tells an analyst which fields the conclusion actually rests on, which
// is not answerable from the contributor sections alone — a dimension with no
// contributors and a dimension that was never queried both show no rows.

import type { ExplanationDimension } from "@/lib/api";
import {
  dimensionLabel,
  dimensionTone,
  formatCount,
  formatRatio,
  formatShare,
  newAndDisappearedAreDeterminate,
} from "@/lib/behavior";

export function DimensionSummary({
  dimensions,
}: {
  dimensions: ExplanationDimension[];
}) {
  const unchecked = dimensions.filter(
    (d) => d.availability === "UNAVAILABLE" || d.availability === "FAILED",
  );

  return (
    <section>
      <h3>Dimension summary</h3>
      <p className="subtitle">
        Every dimension the detection policy requested, including the ones that
        could not be collected. A dimension omitted from this table would read as
        having been checked and found clean.
      </p>

      <table>
        <thead>
          <tr>
            <th>Dimension</th>
            <th>Completeness</th>
            <th>Baseline cardinality</th>
            <th>Anomaly cardinality</th>
            <th>Cardinality ratio</th>
            <th>Concentration change</th>
            <th>New</th>
            <th>Disappeared</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>
          {dimensions.map((d) => {
            // Truncated counts are an artifact of the value cap, not a
            // finding, so they are withheld exactly like unchecked ones.
            const countsDeterminate = newAndDisappearedAreDeterminate(d.availability);
            return (
              <tr key={d.dimension}>
                <td>{dimensionLabel(d.dimension)}</td>
                <td>
                  <span className={`pill ${dimensionTone(d.availability)}`}>
                    {d.availability}
                  </span>
                </td>
                <td>{formatCount(d.baseline_distinct_count)}</td>
                <td>{formatCount(d.anomaly_distinct_count)}</td>
                <td>{formatRatio(d.cardinality_ratio)}</td>
                <td>
                  {formatShare(d.baseline_top_share)} →{" "}
                  {formatShare(d.anomaly_top_share)}
                </td>
                {/* A zero count is only meaningful if the dimension was
                    actually collected; otherwise it is an em dash. */}
                <td>{countsDeterminate ? formatCount(d.new_value_count) : "—"}</td>
                <td>
                  {countsDeterminate ? formatCount(d.disappeared_value_count) : "—"}
                </td>
                <td className="muted">{d.detail ?? (d.truncated ? "truncated" : "—")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {unchecked.length > 0 && (
        <div className="notice">
          <strong>
            {unchecked.length} dimension{unchecked.length === 1 ? " was" : "s were"} not
            checked:
          </strong>{" "}
          {unchecked.map((d) => dimensionLabel(d.dimension)).join(", ")}. No
          conclusion about {unchecked.length === 1 ? "it" : "them"} follows from
          this investigation.
        </div>
      )}
    </section>
  );
}
