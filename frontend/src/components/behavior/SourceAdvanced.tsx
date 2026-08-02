// The source page's technical detail, all of it collapsed.
//
// Four disclosures replacing what used to be a long scroll of tables. At most
// one is open at a time in practice, and none is open on load: an analyst
// asking "is this source healthy?" should never have to scroll past the
// baseline cell history to reach the answer.
//
// Nothing is removed. Every figure the previous page rendered is still here,
// one interaction away.

import { Disclosure } from "@/components/ui/Disclosure";
import { TableScroll } from "@/components/ui/TableScroll";
import type {
  BaselineCell,
  LogSourceDetail,
  MetricBucket,
  SourceBehavior,
} from "@/lib/api";
import { completenessMeaning, formatMetric } from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

/** Buckets shown in the table. The chart covers the whole window. */
const BUCKET_ROWS = 60;

export function SourceAdvanced({
  behavior,
  meta,
  buckets,
  cells,
  cellsFailed,
}: {
  behavior: SourceBehavior;
  meta: LogSourceDetail | null;
  buckets: MetricBucket[];
  cells: BaselineCell[];
  cellsFailed: boolean;
}) {
  const recent = [...buckets]
    .sort((a, b) => Date.parse(b.bucket_start) - Date.parse(a.bucket_start))
    .slice(0, BUCKET_ROWS);

  return (
    <div className="stack-6">
      <Disclosure summary="Baseline history" note={`${cells.length} cells`}>
        {cellsFailed ? (
          <div className="notice" role="alert">
            Baseline history could not be loaded. This is a failed request, not
            an absence of baselines.
          </div>
        ) : cells.length === 0 ? (
          <div className="notice">
            No baseline cells have been computed for this source yet. It is not
            being judged against any expectation.
          </div>
        ) : (
          <TableScroll label="Baseline cells">
            <table>
              <caption className="sr-only">
                Seasonal baseline cells for this source, one per weekday and
                hour, with median, variability and sample count.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Weekday</th>
                  <th scope="col">Hour</th>
                  <th scope="col">Median</th>
                  <th scope="col">MAD</th>
                  <th scope="col">p05 – p95</th>
                  <th scope="col">Samples</th>
                  <th scope="col">Completeness</th>
                  <th scope="col">Reliable</th>
                  <th scope="col">Version</th>
                </tr>
              </thead>
              <tbody>
                {cells.map((c) => (
                  <tr key={`${c.metric_name}-${c.weekday}-${c.hour}`}>
                    <td className="num">{c.weekday}</td>
                    <td className="num">{c.hour}</td>
                    <td className="num">{formatMetric(c.median)}</td>
                    <td className="num">{formatMetric(c.mad, 3)}</td>
                    <td className="num">
                      {formatMetric(c.p05)} – {formatMetric(c.p95)}
                    </td>
                    <td className="num">{c.sample_count}</td>
                    <td className="num">{(c.completeness * 100).toFixed(0)}%</td>
                    <td>
                      {/* Stated as a word, not only as a colour. */}
                      <span className={`pill pill-quiet ${c.is_reliable ? "ok" : "warn"}`}>
                        {c.is_reliable ? "yes" : "no"}
                      </span>
                    </td>
                    <td className="num">v{c.baseline_version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
      </Disclosure>

      <Disclosure
        summary="Metric buckets"
        note={`${Math.min(recent.length, BUCKET_ROWS)} most recent`}
      >
        {recent.length === 0 ? (
          <div className="notice">
            No metric buckets were collected in this window. This is an absence
            of collection, not an observation of zero traffic.
          </div>
        ) : (
          <TableScroll label="Metric buckets">
            <table>
              <caption className="sr-only">
                The most recently collected intervals, with observed volume and
                how completely each was collected.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Interval</th>
                  <th scope="col">Events</th>
                  <th scope="col">Average EPS</th>
                  <th scope="col">Peak EPS</th>
                  <th scope="col">Completeness</th>
                  <th scope="col">Last event</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((b) => (
                  <tr key={b.bucket_start}>
                    <td className="num">
                      <time dateTime={b.bucket_start}>
                        {formatDateTime(b.bucket_start)}
                      </time>
                    </td>
                    <td className="num">{b.event_count.toLocaleString()}</td>
                    {/* A COMPLETE zero is a measurement and renders as 0.00.
                        Only an unobserved interval renders as an em dash. */}
                    <td className="num">
                      {b.completeness === "COMPLETE" ? (
                        formatMetric(b.average_eps)
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="num">{formatMetric(b.peak_eps)}</td>
                    <td>
                      <span
                        className={`pill pill-quiet ${b.completeness === "COMPLETE" ? "ok" : "warn"}`}
                        title={completenessMeaning(b.completeness)}
                      >
                        {b.completeness}
                      </span>
                    </td>
                    <td className="num">{formatDateTime(b.last_event_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
      </Disclosure>

      <Disclosure summary="Collection details">
        <dl className="dimension-meta">
          <div>
            <dt>Last collected bucket</dt>
            <dd className="num">{formatDateTime(behavior.last_bucket_at)}</dd>
          </div>
          <div>
            <dt>Last event</dt>
            <dd className="num">{formatDateTime(behavior.last_event_at)}</dd>
          </div>
          <div>
            <dt>Baseline samples</dt>
            <dd className="num">{behavior.baseline_sample_count}</dd>
          </div>
          <div>
            <dt>Baseline completeness</dt>
            <dd className="num">
              {(behavior.baseline_completeness * 100).toFixed(0)}%
            </dd>
          </div>
          <div>
            <dt>Collection source</dt>
            <dd>QRadar Ariel, via the backend collector</dd>
          </div>
        </dl>
      </Disclosure>

      <Disclosure summary="Source metadata">
        {meta ? (
          <dl className="dimension-meta">
            <div>
              <dt>QRadar ID</dt>
              <dd className="num">{meta.qradar_id}</dd>
            </div>
            <div>
              <dt>Type</dt>
              <dd>{meta.type_name ?? <span className="muted">—</span>}</dd>
            </div>
            <div>
              <dt>Owner</dt>
              <dd>{meta.owner ?? <span className="muted">—</span>}</dd>
            </div>
            <div>
              <dt>Enabled</dt>
              <dd>{meta.enabled ? "yes" : "no"}</dd>
            </div>
            <div>
              <dt>Monitoring</dt>
              <dd>{meta.monitoring_enabled ? "enabled" : "disabled"}</dd>
            </div>
            <div>
              <dt>Maintenance mode</dt>
              <dd>{meta.maintenance_mode ? "active" : "inactive"}</dd>
            </div>
            <div>
              <dt>QRadar status</dt>
              <dd>{meta.qradar_status ?? <span className="muted">—</span>}</dd>
            </div>
            <div>
              <dt>Timezone</dt>
              <dd>{meta.timezone_name ?? <span className="muted">—</span>}</dd>
            </div>
          </dl>
        ) : (
          <div className="notice">
            Inventory metadata could not be loaded for this source.
          </div>
        )}
      </Disclosure>
    </div>
  );
}
