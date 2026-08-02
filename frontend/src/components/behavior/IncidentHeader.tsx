// The incident's identity, in one block.
//
// Everything an analyst needs to know they are looking at the right thing:
// which source, what kind of change, how it is currently classified, and when
// it ran. The source name is the <h1> and appears exactly once — the previous
// header repeated both it and the detector in the title, the badge row and the
// summary paragraph.

import Link from "next/link";

import type { AnomalyDetail } from "@/lib/api";
import { sevTone } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

/** Detector identifiers as an analyst would say them. */
const DETECTOR_LABELS: Record<string, string> = {
  VOLUME_SPIKE: "Volume spike",
  VOLUME_DROP: "Volume drop",
  NO_EVENTS: "No events",
};

export function detectorLabel(type: string): string {
  return DETECTOR_LABELS[type] ?? type;
}

/** The anomaly's interval, or an explicit statement that it has not ended. */
export function intervalText(anomaly: AnomalyDetail): string {
  if (!anomaly.anomaly_start) return "Interval not recorded";
  const start = formatDateTime(anomaly.anomaly_start);
  // Still running: no end exists, and inventing one would report a closed
  // incident that is still open.
  return anomaly.anomaly_end
    ? `${start} → ${formatDateTime(anomaly.anomaly_end)}`
    : `${start} → still running`;
}

export function IncidentHeader({ anomaly }: { anomaly: AnomalyDetail }) {
  return (
    <header className="page-header incident-header">
      <div>
        <p className="incident-back">
          <Link href="/anomalies">← All anomalies</Link>
        </p>

        <h1 className="page-title">
          {anomaly.log_source_name ?? "Unknown source"}
        </h1>

        <div className="row incident-meta">
          <span className="incident-detector">
            {detectorLabel(anomaly.anomaly_type)}
          </span>
          {/* Lifecycle state is the dominant status: it is the fact that
              decides whether this needs work now. */}
          <span className={`pill pill-strong ${stateTone(anomaly.state)}`}>
            {anomaly.state}
          </span>
          {/* Severity is real but secondary, so it is quiet by default. */}
          <span className={`pill pill-quiet ${sevTone(anomaly.severity)}`}>
            {anomaly.severity}
          </span>
          {anomaly.suppressed && (
            <span className="pill pill-quiet warn">Suppressed</span>
          )}
          <span className="muted">{intervalText(anomaly)}</span>
        </div>
      </div>

      <div className="page-header-actions">
        <Link
          href={`/behavior/sources/${anomaly.log_source_id}`}
          className="action secondary button-link"
        >
          View source behavior
        </Link>
      </div>
    </header>
  );
}
