// Offset pagination control.
//
// A server component: paging is a navigation, so it is plain links. That keeps
// each page shareable and bookmarkable, and needs no client JavaScript.

function href(
  basePath: string,
  offset: number,
  params: Record<string, string | undefined>,
): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v) qs.set(k, v);
  }
  if (offset > 0) qs.set("offset", String(offset));
  const s = qs.toString();
  return s ? `${basePath}?${s}` : basePath;
}

export function Pagination({
  total,
  limit,
  offset,
  basePath,
  params = {},
}: {
  total: number;
  limit: number;
  offset: number;
  basePath: string;
  params?: Record<string, string | undefined>;
}) {
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  const hasPrev = offset > 0;
  const hasNext = offset + limit < total;

  return (
    <nav className="pagination" aria-label="Pagination">
      <span className="muted">
        {from}–{to} of {total}
      </span>
      <span className="pagination-links">
        {hasPrev ? (
          <a href={href(basePath, Math.max(0, offset - limit), params)}>Previous</a>
        ) : (
          <span className="muted">Previous</span>
        )}
        {hasNext ? (
          <a href={href(basePath, offset + limit, params)}>Next</a>
        ) : (
          <span className="muted">Next</span>
        )}
      </span>
    </nav>
  );
}
