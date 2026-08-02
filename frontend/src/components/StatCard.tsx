// A primary metric.
//
// The optional note exists so a counter can carry its own caveat where it is
// read. "Insufficient data: 12" invites the reading "12 sources are fine";
// the note is where "not being judged" is said, next to the number rather than
// in a paragraph further down the page that the reader may never reach.

export function StatCard({
  label,
  value,
  tone,
  note,
}: {
  label: string;
  value: string | number;
  tone?: "ok" | "warn" | "crit";
  note?: string;
}) {
  return (
    <div className="card">
      <div className="k">{label}</div>
      <div className={`v ${tone ?? ""}`}>{value}</div>
      {note && <p className="metric-note">{note}</p>}
    </div>
  );
}
