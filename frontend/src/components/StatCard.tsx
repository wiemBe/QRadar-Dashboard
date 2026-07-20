export function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone?: "ok" | "warn" | "crit";
}) {
  return (
    <div className="card">
      <div className="k">{label}</div>
      <div className={`v ${tone ?? ""}`}>{value}</div>
    </div>
  );
}
