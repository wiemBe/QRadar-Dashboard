"use client";

// The anomaly list's rows, with a per-row technical disclosure.
//
// Nine default columns instead of eleven, and the fields that were pushing the
// table past the viewport — absolute delta, duration, confidence, end and
// resolved times, baseline and policy versions — move into a row that opens on
// demand. None of it is lost; it simply stops competing with the columns an
// analyst triages on.
//
// Visual weight is deliberately unequal across the three status fields.
// Lifecycle state is the fact that decides whether this needs work now, so it
// carries the strong badge; severity and evidence are real but subordinate and
// render quietly. All three state their value as text, so none of them depends
// on colour to be understood.

import { Fragment, useState } from "react";

import { sevTone, type AnomalySummary } from "@/lib/api";
import {
  evidenceTone,
  formatDuration,
  formatMetric,
  formatRatio,
  stateTone,
} from "@/lib/behavior";
import { formatDateTime, formatDateTimeCompact } from "@/lib/health";

export function AnomalyRows({ items }: { items: AnomalySummary[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <tbody>
      {items.map((a) => {
        const open = expanded === a.id;
        return (
          <Fragment key={a.id}>
            <tr>
              <td>
                <a href={`/behavior/sources/${a.log_source_id}`}>
                  {a.log_source_name ?? a.log_source_id}
                </a>
              </td>
              <td>{a.anomaly_type}</td>
              <td>
                <span className={`pill pill-strong ${stateTone(a.state)}`}>
                  {a.state}
                </span>
              </td>
              <td className="num">
                {formatMetric(a.observed_value)}
                <span className="muted"> → </span>
                {formatMetric(a.expected_value)}
              </td>
              <td className="num">{formatRatio(a.deviation_ratio)}</td>
              <td>
                <span className={`pill pill-quiet ${sevTone(a.severity)}`}>
                  {a.severity}
                </span>
              </td>
              <td className="num">
                {formatDateTimeCompact(a.anomaly_start ?? a.detected_at)}
              </td>
              <td>
                <span className={`pill pill-quiet ${evidenceTone(a.evidence_status)}`}>
                  {a.evidence_status}
                </span>
              </td>
              {/* One actions cell rather than two columns: at 1024 px the
                  extra column was pushing the detail link out of view. */}
              <td className="row-actions">
                <div className="row-actions-inner">
                  <a href={`/anomalies/${a.id}`}>Open</a>
                  <button
                    type="button"
                    className="row-toggle"
                    aria-expanded={open}
                    aria-label={`${open ? "Hide" : "Show"} technical detail for the ${
                      a.anomaly_type
                    } on ${a.log_source_name ?? a.log_source_id}`}
                    onClick={() => setExpanded(open ? null : a.id)}
                  >
                    {open ? "Less" : "More"}
                  </button>
                </div>
              </td>
            </tr>

            {open && (
              <tr className="row-detail">
                <td colSpan={9}>
                  <dl className="dimension-meta">
                    <div>
                      <dt>Absolute delta</dt>
                      <dd className="num">{formatMetric(a.absolute_delta, 0)}</dd>
                    </div>
                    <div>
                      <dt>Duration</dt>
                      {/* Null while still running. Not "0s", and not "now
                          minus start", which would grow on every refresh. */}
                      <dd className="num">
                        {a.duration_seconds != null ? (
                          formatDuration(a.duration_seconds)
                        ) : (
                          <span className="muted">running</span>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>Confidence</dt>
                      <dd className="num">{formatMetric(a.confidence)}</dd>
                    </div>
                    <div>
                      <dt>Anomaly end</dt>
                      <dd className="num">
                        {a.anomaly_end ? (
                          formatDateTime(a.anomaly_end)
                        ) : (
                          <span className="muted">still running</span>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>Resolved</dt>
                      <dd className="num">{formatDateTime(a.resolved_at)}</dd>
                    </div>
                    <div>
                      <dt>Consecutive buckets</dt>
                      <dd className="num">{a.consecutive_buckets}</dd>
                    </div>
                  </dl>
                </td>
              </tr>
            )}
          </Fragment>
        );
      })}
    </tbody>
  );
}
