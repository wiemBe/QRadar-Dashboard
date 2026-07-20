// Renders structured anomaly/alert evidence, not just a label. The backend
// attaches observed vs expected, the baseline band, deviation, threshold,
// sample count and a human reason to every anomaly; this surfaces them.

export function Evidence({ data }: { data: Record<string, unknown> }) {
  if (!data || Object.keys(data).length === 0) {
    return <span className="subtitle">no evidence captured</span>;
  }
  const num = (k: string) => (data[k] == null ? "—" : String(data[k]));
  const rows: [string, string][] = [
    ["Observed", num("observed_value")],
    ["Expected", num("expected_value")],
    ["Baseline low", num("baseline_low")],
    ["Baseline high", num("baseline_high")],
    ["Deviation (z)", num("deviation_score")],
    ["Threshold", num("threshold")],
    ["Samples", num("sample_count")],
    ["Confidence", num("confidence")],
  ];
  return (
    <div>
      {typeof data.reason === "string" && (
        <p style={{ margin: "0 0 10px" }}>{data.reason}</p>
      )}
      <table style={{ maxWidth: 480 }}>
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <td style={{ color: "var(--muted)", width: 140 }}>{k}</td>
              <td>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
