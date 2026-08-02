// Query handling for the source-behavior inventory.
//
// Filtering, sorting and paging all live in the URL so that a view an analyst
// arrived at is a view they can share, bookmark and come back to. Nothing here
// trusts the query string: every enum is checked against an allow-list and
// every number is bounded, because these values arrive from a URL a third
// party may have written.
//
// PAGINATION NOTE
// ---------------
// `GET /api/v1/behavior/sources` accepts no parameters and returns the whole
// collection as a bare array, so paging is applied here after the fetch rather
// than by the API. That is a deliberate limitation of this UX workstream, not
// an oversight: adding an offset/limit contract to the endpoint is a backend
// change, and the collection is currently 59 rows. If the monitored fleet
// grows to a size where transferring it whole is the wrong shape, the fix is
// server-side paging on that endpoint, and `sliceSources` is the only thing
// here that would change.

import type { SourceBehavior } from "@/lib/api";
import { deviationMagnitude, hasMeasurableDeviation } from "@/lib/behaviorOverview";

export const SOURCE_PAGE_SIZE = 25;

const STATES = new Set([
  "INSUFFICIENT_DATA",
  "NORMAL",
  "CANDIDATE",
  "OPEN",
  "RECOVERING",
  "RESOLVED",
  "SUPPRESSED",
]);

/** Presence of an active anomaly on the source. */
const ANOMALY_FILTERS = new Set(["active", "none"]);

/** Whether the source has an adequate baseline. */
const DATA_FILTERS = new Set(["insufficient", "adequate"]);

const SORTS = new Set(["deviation", "last_event", "name"]);
const DIRECTIONS = new Set(["asc", "desc"]);

/** Longest name we will echo back into a filter chip. */
const MAX_QUERY_LEN = 120;

export type SourceSort = "deviation" | "last_event" | "name";
export type SortDirection = "asc" | "desc";

export interface SourceQuery {
  q: string;
  state: string;
  anomaly: string;
  data: string;
  sort: SourceSort;
  dir: SortDirection;
  offset: number;
}

function pick(value: string | undefined, allowed: Set<string>): string {
  return value && allowed.has(value) ? value : "";
}

/** Default direction per sort: the reading an analyst wants first. */
export function defaultDirection(sort: SourceSort): SortDirection {
  // Worst deviation first, most recent event first, but names read A–Z.
  return sort === "name" ? "asc" : "desc";
}

export function parseSourceQuery(
  params: Record<string, string | undefined>,
): SourceQuery {
  const sort = (pick(params.sort, SORTS) || "deviation") as SourceSort;

  const rawOffset = Number.parseInt(params.offset ?? "", 10);
  const offset =
    Number.isFinite(rawOffset) && rawOffset > 0
      ? // Snapped to a page boundary so a hand-edited offset cannot produce a
        // window that no "next" link can ever reach or leave.
        Math.floor(rawOffset / SOURCE_PAGE_SIZE) * SOURCE_PAGE_SIZE
      : 0;

  return {
    q: (params.q ?? "").trim().slice(0, MAX_QUERY_LEN),
    state: pick(params.state, STATES),
    anomaly: pick(params.anomaly, ANOMALY_FILTERS),
    data: pick(params.data, DATA_FILTERS),
    sort,
    dir: (pick(params.dir, DIRECTIONS) || defaultDirection(sort)) as SortDirection,
    offset,
  };
}

/** True when any filter narrows the list — used to word the empty state. */
export function isFiltered(query: SourceQuery): boolean {
  return Boolean(query.q || query.state || query.anomaly || query.data);
}

/** The query as link parameters, minus paging. */
export function sourceQueryParams(
  query: SourceQuery,
): Record<string, string | undefined> {
  return {
    q: query.q || undefined,
    state: query.state || undefined,
    anomaly: query.anomaly || undefined,
    data: query.data || undefined,
    sort: query.sort,
    dir: query.dir,
  };
}

export function filterSources(
  sources: SourceBehavior[],
  query: SourceQuery,
): SourceBehavior[] {
  const needle = query.q.toLowerCase();
  return sources.filter((s) => {
    if (needle && !s.name.toLowerCase().includes(needle)) return false;
    if (query.state && s.state !== query.state) return false;
    if (query.anomaly === "active" && s.open_anomaly_count === 0) return false;
    if (query.anomaly === "none" && s.open_anomaly_count > 0) return false;
    if (query.data === "insufficient" && s.state !== "INSUFFICIENT_DATA") return false;
    if (query.data === "adequate" && s.state === "INSUFFICIENT_DATA") return false;
    return true;
  });
}

/**
 * Sort, without letting an unmeasured value masquerade as a small one.
 *
 * A source with no usable deviation ratio is not "the least deviating source";
 * it is a source we cannot rank on that axis. Such rows are held at the end of
 * a deviation sort in both directions, so reversing the order never promotes a
 * source with no measurement to the top of a list titled "highest deviation".
 * The same applies to a source that has never reported an event.
 */
export function sortSources(
  sources: SourceBehavior[],
  sort: SourceSort,
  dir: SortDirection,
): SourceBehavior[] {
  const sign = dir === "asc" ? 1 : -1;

  return [...sources].sort((a, b) => {
    if (sort === "name") {
      return sign * a.name.localeCompare(b.name);
    }

    if (sort === "deviation") {
      const aOk = hasMeasurableDeviation(a);
      const bOk = hasMeasurableDeviation(b);
      if (aOk !== bOk) return aOk ? -1 : 1;
      if (!aOk) return a.name.localeCompare(b.name);
      const delta =
        deviationMagnitude(a.deviation_ratio) - deviationMagnitude(b.deviation_ratio);
      if (delta !== 0 && Number.isFinite(delta)) return sign * delta;
      if (delta !== 0) return sign * (delta > 0 ? 1 : -1);
      return a.name.localeCompare(b.name);
    }

    const aAt = a.last_event_at ? Date.parse(a.last_event_at) : null;
    const bAt = b.last_event_at ? Date.parse(b.last_event_at) : null;
    if (aAt == null || bAt == null) {
      if (aAt !== bAt) return aAt == null ? 1 : -1;
      return a.name.localeCompare(b.name);
    }
    if (aAt !== bAt) return sign * (aAt - bAt);
    return a.name.localeCompare(b.name);
  });
}

/** The page's window over the filtered, sorted collection. */
export function sliceSources(
  sources: SourceBehavior[],
  offset: number,
  limit: number = SOURCE_PAGE_SIZE,
): SourceBehavior[] {
  return sources.slice(offset, offset + limit);
}

/**
 * The href a column header links to.
 *
 * Clicking the active column flips its direction; clicking any other column
 * switches to it at that column's natural direction. Paging resets, because an
 * offset into the previous ordering means nothing in the new one.
 */
export function sortHref(
  query: SourceQuery,
  column: SourceSort,
  basePath = "/behavior/sources",
): string {
  const dir: SortDirection =
    query.sort === column
      ? query.dir === "asc"
        ? "desc"
        : "asc"
      : defaultDirection(column);

  const qs = new URLSearchParams();
  if (query.q) qs.set("q", query.q);
  if (query.state) qs.set("state", query.state);
  if (query.anomaly) qs.set("anomaly", query.anomaly);
  if (query.data) qs.set("data", query.data);
  qs.set("sort", column);
  qs.set("dir", dir);
  return `${basePath}?${qs.toString()}`;
}

/** `aria-sort` for a column header, so the current order is announced. */
export function ariaSort(
  query: SourceQuery,
  column: SourceSort,
): "ascending" | "descending" | "none" {
  if (query.sort !== column) return "none";
  return query.dir === "asc" ? "ascending" : "descending";
}
