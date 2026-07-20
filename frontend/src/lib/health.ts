// Presentation rules for rule-health and coverage verdicts.
//
// The central honesty rule of this platform lives here: a verdict meaning "we
// have not established this" must never be rendered in the same visual language
// as a verdict meaning "this is broken", and neither may be rendered as if it
// were "this is fine". An unevaluated technique shown in green is a false
// assurance; shown in red it is a false alarm. Both get their own neutral tone.

export type Tone = "ok" | "warn" | "crit" | "";

/** Verdicts that mean "not enough evidence", not "no detection". */
const UNESTABLISHED = new Set([
  "INSUFFICIENT_DATA",
  "NOT_EVALUATED",
  "UNKNOWN",
]);

export function isUnestablished(status: string | null | undefined): boolean {
  return status != null && UNESTABLISHED.has(status);
}

export function healthTone(status: string | null | undefined): Tone {
  if (status == null) return "";
  if (isUnestablished(status)) return ""; // neutral: an absence of evidence
  switch (status) {
    case "HEALTHY":
    case "COVERED":
      return "ok";
    case "NOISY":
    case "INACTIVE":
    case "DEGRADED":
    case "PARTIAL":
    case "DEPENDENCY_DEGRADED":
      return "warn";
    case "NEVER_OBSERVED":
    case "MISSING":
    case "NOT_COVERED":
      return "crit";
    case "DISABLED":
      return "warn";
    default:
      return "";
  }
}

/** Operator-facing explanation of what a verdict actually asserts. */
export function healthMeaning(status: string | null | undefined): string {
  switch (status) {
    case "INSUFFICIENT_DATA":
      return "Not enough collected evidence to judge — this is not a finding.";
    case "NOT_EVALUATED":
      return "No evaluation has run for this technique yet.";
    case "HEALTHY":
      return "Firing within expected bounds.";
    case "DISABLED":
      return "Disabled in QRadar, so it cannot contribute detection.";
    case "NOISY":
      return "Firing far above its expected rate.";
    case "INACTIVE":
      return "Has fired before, but not recently.";
    case "NEVER_OBSERVED":
      return "Observed continuously and never seen to fire.";
    case "DEPENDENCY_DEGRADED":
      return "A building block or log source it depends on is unhealthy.";
    case "COVERED":
      return "At least one healthy enabled rule maps to this technique.";
    case "PARTIAL":
      return "Mapped, but the contributing detection is not fully healthy.";
    case "DEGRADED":
      return "Mapped rules exist but are impaired.";
    case "MISSING":
      return "No rule maps to this technique.";
    default:
      return "";
  }
}

export function magnitudeTone(magnitude: number | null | undefined): Tone {
  if (magnitude == null) return "";
  if (magnitude >= 7) return "crit";
  if (magnitude >= 4) return "warn";
  return "ok";
}

/** Compact humanised duration, e.g. "3d 4h". */
export function formatAge(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}
