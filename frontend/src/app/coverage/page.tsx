import { api, type CoverageSummary, type Page, type TechniqueCoverage } from "@/lib/api";
import { StatCard } from "@/components/StatCard";
import { Pagination } from "@/components/Pagination";
import { formatDateTime, healthMeaning, healthTone } from "@/lib/health";

const PAGE_SIZE = 50;

// The statuses we always show a counter for, so a zero is visibly zero rather
// than silently absent.
const STATUSES = ["COVERED", "PARTIAL", "DEGRADED", "MISSING", "NOT_EVALUATED"];

export default async function CoveragePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const offset = Math.max(0, Number(params.offset ?? 0) || 0);

  let summary: CoverageSummary | null = null;
  let page: Page<TechniqueCoverage> | null = null;
  let error = false;

  try {
    [summary, page] = await Promise.all([
      api.coverageSummary(),
      api.coverageTechniques({ limit: PAGE_SIZE, offset }),
    ]);
  } catch {
    error = true;
  }

  if (error) {
    return (
      <>
        <h2>Detection Coverage</h2>
        <div className="notice">
          Backend unreachable. Start the stack, then run{" "}
          <code>python -m app.cli.sync coverage</code>.
        </div>
      </>
    );
  }

  const items = page?.items ?? [];
  const nothingMapped = (summary?.total_techniques ?? 0) === 0;

  return (
    <>
      <h2>Detection Coverage</h2>
      <p className="subtitle">
        MITRE ATT&amp;CK technique coverage, derived from mapped rules and their
        observed health. Coverage is never inferred from a rule merely existing.
      </p>

      {nothingMapped ? (
        // The honest empty state. Showing 0% covered here would be a claim we
        // cannot support; showing 100% would be worse.
        <div className="notice">
          <strong>Coverage has not been evaluated.</strong>
          <p>
            No ATT&amp;CK technique mappings exist yet, so there is nothing to
            evaluate. This is <em>not</em> a finding of zero coverage — it means
            the question has not been asked. Rules and building blocks have been
            collected; what is missing is the technique-to-rule mapping that
            turns them into a coverage claim.
          </p>
          <p>
            Add mappings via <code>POST /api/v1/coverage/mappings</code>, then run{" "}
            <code>python -m app.cli.sync coverage</code>.
          </p>
        </div>
      ) : (
        <>
          <div className="cards">
            {STATUSES.map((s) => (
              <StatCard
                key={s}
                label={s.replace(/_/g, " ")}
                value={summary?.by_status?.[s] ?? 0}
                tone={healthTone(s) || undefined}
              />
            ))}
          </div>

          <p className="muted">
            {summary?.total_techniques} techniques evaluated ·{" "}
            {Math.round((summary?.covered_ratio ?? 0) * 100)}% covered ·{" "}
            {summary?.mapping_provenance.explicit ?? 0} explicit /{" "}
            {summary?.mapping_provenance.inferred ?? 0} inferred mappings ·
            generated {formatDateTime(summary?.generated_at)}
          </p>

          {items.length === 0 ? (
            <div className="notice">No techniques on this page.</div>
          ) : (
            <>
              <table>
                <thead>
                  <tr>
                    <th>Technique</th>
                    <th>Name</th>
                    <th>Tactic</th>
                    <th>Status</th>
                    <th>Rules</th>
                    <th>Score</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((t) => (
                    <tr key={t.technique_id}>
                      <td>{t.technique_id}</td>
                      <td>{t.technique_name ?? "—"}</td>
                      <td>{t.tactic ?? "—"}</td>
                      <td>
                        <span className={`pill ${healthTone(t.status)}`}>
                          {t.status.replace(/_/g, " ")}
                        </span>
                      </td>
                      <td>{t.rule_count}</td>
                      <td>
                        {t.coverage_score == null
                          ? "—"
                          : Math.round(t.coverage_score * 100) + "%"}
                      </td>
                      <td className="muted">{t.reason ?? healthMeaning(t.status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <Pagination
                total={page?.total ?? 0}
                limit={PAGE_SIZE}
                offset={offset}
                basePath="/coverage"
              />
            </>
          )}
        </>
      )}
    </>
  );
}
