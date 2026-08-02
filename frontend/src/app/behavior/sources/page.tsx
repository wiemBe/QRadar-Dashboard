// The source-behavior inventory.
//
// This route owns the full monitored-source list, which previously sat at the
// bottom of the behavioral overview. Moving it here is the point: on a fleet
// of 59 sources of which 55 are quiet and normal, that table filled the
// overview with rows requiring no action and pushed the ones requiring action
// off the screen. The overview is now a worklist; this is the inventory, and
// an analyst arrives here deliberately.
//
// Columns are kept to what identifies and ranks a source. Baseline versions,
// provenance and collection internals belong on the source's own page, where
// there is room to explain them.

import type { Metadata } from "next";

import { SourceFilters } from "@/components/behavior/SourceFilters";
import { Pagination } from "@/components/Pagination";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { ApiError, api, type SourceBehavior } from "@/lib/api";
import { formatMetric, formatRatio, isUnjudged, stateTone } from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";
import {
  SOURCE_PAGE_SIZE,
  ariaSort,
  filterSources,
  isFiltered,
  parseSourceQuery,
  sliceSources,
  sortHref,
  sortSources,
  sourceQueryParams,
} from "@/lib/sourceInventory";

export const metadata: Metadata = {
  title: "Sources",
};

/** A sortable column header: a link, so it is keyboard-operable for free. */
function SortHeader({
  label,
  column,
  query,
}: {
  label: string;
  column: "deviation" | "last_event" | "name";
  query: ReturnType<typeof parseSourceQuery>;
}) {
  const sort = ariaSort(query, column);
  const arrow = sort === "none" ? "" : sort === "ascending" ? " ↑" : " ↓";
  return (
    <th scope="col" aria-sort={sort}>
      <a href={sortHref(query, column)} className="sort-link">
        {label}
        <span aria-hidden="true">{arrow}</span>
      </a>
    </th>
  );
}

export default async function SourcesIndexPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const query = parseSourceQuery(await searchParams);

  let sources: SourceBehavior[] = [];
  let error: string | null = null;
  try {
    sources = await api.sourceBehaviors();
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 403
        ? "You do not have permission to view behavioral analytics."
        : err instanceof ApiError && err.status === 401
          ? "Your session has expired. Sign in again to continue."
          : "Source behavior could not be loaded. The backend may be unreachable.";
  }

  const matched = sortSources(
    filterSources(sources, query),
    query.sort,
    query.dir,
  );
  const visible = sliceSources(matched, query.offset);

  return (
    <>
      <PageHeader
        title="Sources"
        description={
          <>
            Every monitored log source and how its current volume compares with
            its seasonal baseline. Open a source for its timeline, baseline
            quality and collection health.
          </>
        }
      />

      <SourceFilters query={query} />

      {error ? (
        <div className="notice" role="alert">
          {error}
        </div>
      ) : matched.length === 0 ? (
        <div className="notice">
          {isFiltered(query)
            ? "No sources match these filters. This is a filtered view, not a statement about the fleet."
            : "No monitored log sources. Enable monitoring on a source to start collecting its volume baseline."}
        </div>
      ) : (
        <>
          <TableScroll label="Monitored log sources">
            <table>
              <caption className="sr-only">
                Monitored log sources with observed and expected events per
                second, deviation from baseline, and active anomaly count.
              </caption>
              <thead>
                <tr>
                  <SortHeader label="Source" column="name" query={query} />
                  <th scope="col">State</th>
                  <th scope="col">Observed</th>
                  <th scope="col">Expected</th>
                  <SortHeader label="Deviation" column="deviation" query={query} />
                  <SortHeader label="Last event" column="last_event" query={query} />
                  <th scope="col">Anomalies</th>
                  <th scope="col">
                    <span className="sr-only">Detail</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((s) => (
                  <tr key={s.log_source_id}>
                    <td>
                      <a href={`/behavior/sources/${s.log_source_id}`}>{s.name}</a>
                    </td>
                    <td>
                      <span className={`pill ${stateTone(s.state)}`}>{s.state}</span>
                    </td>
                    <td className="num">{formatMetric(s.observed_eps)}</td>
                    {/* An unbaselined source has no expectation at all.
                        Rendering a 0 here would invent one, and make the
                        observed value look like a spike against it. */}
                    <td className="num">
                      {isUnjudged(s.state) ? (
                        <span className="muted">still learning</span>
                      ) : (
                        formatMetric(s.expected_eps)
                      )}
                    </td>
                    <td className="num">{formatRatio(s.deviation_ratio)}</td>
                    <td className="num">{formatDateTime(s.last_event_at)}</td>
                    <td className="num">
                      {s.open_anomaly_count > 0 ? (
                        <a
                          href={`/anomalies?log_source_id=${s.log_source_id}&active_only=true`}
                        >
                          {s.open_anomaly_count}
                        </a>
                      ) : (
                        <span className="muted">0</span>
                      )}
                    </td>
                    <td>
                      <a href={`/behavior/sources/${s.log_source_id}`}>Detail</a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>

          <Pagination
            total={matched.length}
            limit={SOURCE_PAGE_SIZE}
            offset={query.offset}
            basePath="/behavior/sources"
            params={sourceQueryParams(query)}
          />
        </>
      )}
    </>
  );
}
