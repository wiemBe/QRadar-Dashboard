// Search + status filter for the offense list.
//
// A plain GET form rather than a controlled client component: it needs no
// JavaScript, the resulting URL is shareable, and submitting resets paging
// naturally because `offset` is simply absent from the new query string.

import Link from "next/link";

export function OffenseFilters({
  search,
  status,
}: {
  search: string;
  status: string;
}) {
  return (
    <form className="filters" method="get" action="/offenses">
      <input
        type="search"
        name="search"
        defaultValue={search}
        placeholder="Search description, source, assignee…"
        aria-label="Search offenses"
      />
      <select name="status" defaultValue={status} aria-label="Filter by status">
        <option value="">All statuses</option>
        <option value="OPEN">Open</option>
        <option value="HIDDEN">Hidden</option>
        <option value="CLOSED">Closed</option>
      </select>
      <button type="submit">Filter</button>
      {(search || status) && <Link href="/offenses">Clear</Link>}
    </form>
  );
}
