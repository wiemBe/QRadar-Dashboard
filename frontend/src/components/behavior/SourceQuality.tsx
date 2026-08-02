// Baseline quality and collection health, side by side.
//
// Two questions an analyst must be able to answer before trusting anything
// above: is the expectation this source is judged against any good, and is the
// data behind the chart complete? Both are interpretive — a status word and a
// sentence — rather than a dump of the figures they were derived from. The
// figures stay available in the advanced disclosures below.
//
// Neither section presents an absence of collection as source silence, and
// neither presents a zero MAD as an error.

import type { BaselineQuality, CollectionHealth } from "@/lib/sourceDetail";
import { formatMetric } from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

function lagText(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes}m ago`;
  return `${Math.round(minutes / 60)}h ago`;
}

export function SourceQuality({
  baseline,
  collection,
  expectedLow,
  expectedHigh,
  lastBucketAt,
  qradarStatus,
}: {
  baseline: BaselineQuality;
  collection: CollectionHealth;
  expectedLow: number | null;
  expectedHigh: number | null;
  lastBucketAt: string | null;
  /** The log source's QRadar-reported status, when the inventory call worked. */
  qradarStatus: string | null;
}) {
  return (
    <div className="quality-grid">
      <section className="panel">
        <div className="row dimension-panel-head">
          <h3>Baseline quality</h3>
          <span className={`pill ${baseline.tone}`}>{baseline.label}</span>
        </div>
        <p className="subtitle">{baseline.explanation}</p>

        <dl className="dimension-meta">
          {baseline.cell && (
            <>
              <div>
                <dt>Samples</dt>
                <dd className="num">{baseline.cell.sample_count}</dd>
              </div>
              <div>
                <dt>Seasonal cell</dt>
                <dd className="num">
                  Weekday {baseline.cell.weekday}, hour {baseline.cell.hour}
                </dd>
              </div>
              <div>
                <dt>Cell completeness</dt>
                <dd className="num">
                  {(baseline.cell.completeness * 100).toFixed(0)}%
                </dd>
              </div>
              <div>
                <dt>MAD</dt>
                <dd className="num">{formatMetric(baseline.cell.mad, 3)}</dd>
              </div>
            </>
          )}
          <div>
            <dt>Expected range</dt>
            <dd className="num">
              {/* No band exists without a baseline. An em dash says so; a
                  "0.00 – 0.00" would be an expectation we never formed. */}
              {expectedLow == null || expectedHigh == null ? (
                <span className="muted">—</span>
              ) : (
                `${formatMetric(expectedLow)} – ${formatMetric(expectedHigh)}`
              )}
            </dd>
          </div>
        </dl>
      </section>

      <section className="panel">
        <div className="row dimension-panel-head">
          <h3>Collection health</h3>
          <span className={`pill ${collection.tone}`}>{collection.label}</span>
        </div>
        <p className="subtitle">{collection.explanation}</p>

        <dl className="dimension-meta">
          <div>
            <dt>Latest collected interval</dt>
            <dd className="num">
              {lastBucketAt ? (
                <time dateTime={lastBucketAt}>{formatDateTime(lastBucketAt)}</time>
              ) : (
                <span className="muted">—</span>
              )}
            </dd>
          </div>
          <div>
            <dt>Collection lag</dt>
            <dd className="num">{lagText(collection.lagSeconds)}</dd>
          </div>
          <div>
            <dt>Intervals in window</dt>
            <dd className="num">
              {collection.total - collection.incomplete} of {collection.expected} fully
              observed
            </dd>
          </div>
          <div>
            <dt>QRadar log source status</dt>
            {/* Reported by QRadar about the log source. Not a statement about
                whether our collector ran, which the API does not expose. */}
            <dd>{qradarStatus ?? <span className="muted">unavailable</span>}</dd>
          </div>
        </dl>
      </section>
    </div>
  );
}
