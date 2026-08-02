// Filters for the source-behavior inventory.
//
// A plain GET form, matching the anomaly filters: no client JavaScript, and the
// resulting URL is the shareable description of the view. Submitting drops
// `offset` simply by not carrying it, which resets paging — an offset into the
// previous result set means nothing in the new one.
//
// Every control has a real <label> associated by `htmlFor`. They are visually
// hidden rather than absent, so the form stays compact without the controls
// becoming unlabelled to a screen reader.

import Link from "next/link";

import { isFiltered, type SourceQuery } from "@/lib/sourceInventory";

const STATES = [
  "OPEN",
  "CANDIDATE",
  "RECOVERING",
  "NORMAL",
  "RESOLVED",
  "INSUFFICIENT_DATA",
  "SUPPRESSED",
];

export function SourceFilters({ query }: { query: SourceQuery }) {
  return (
    <form className="filters" method="get" action="/behavior/sources">
      <label className="sr-only" htmlFor="source-q">
        Search by source name
      </label>
      <input
        id="source-q"
        type="search"
        name="q"
        defaultValue={query.q}
        placeholder="Search source name…"
      />

      <label className="sr-only" htmlFor="source-state">
        Behavior state
      </label>
      <select id="source-state" name="state" defaultValue={query.state}>
        <option value="">All states</option>
        {STATES.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>

      <label className="sr-only" htmlFor="source-anomaly">
        Anomaly status
      </label>
      <select id="source-anomaly" name="anomaly" defaultValue={query.anomaly}>
        <option value="">Any anomaly status</option>
        <option value="active">Has active anomaly</option>
        <option value="none">No active anomaly</option>
      </select>

      <label className="sr-only" htmlFor="source-data">
        Baseline adequacy
      </label>
      <select id="source-data" name="data" defaultValue={query.data}>
        <option value="">Any baseline</option>
        <option value="insufficient">Insufficient data only</option>
        <option value="adequate">Baselined only</option>
      </select>

      {/* Carried so that filtering does not silently discard the chosen
          column order. */}
      <input type="hidden" name="sort" value={query.sort} />
      <input type="hidden" name="dir" value={query.dir} />

      <button type="submit">Filter</button>
      {isFiltered(query) && <Link href="/behavior/sources">Clear</Link>}
    </form>
  );
}
