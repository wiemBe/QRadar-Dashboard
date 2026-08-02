// The platform's sections.
//
// Ordering reflects what the product is for. Behavioral analytics leads because
// detecting and investigating a change in log-source behavior is the reason the
// platform exists; offenses, rules and coverage remain supporting inventory
// capabilities rather than the headline. Detail routes are listed so they are
// enumerable, but are reached from their parent list rather than from the nav.
export interface Section {
  slug: string;
  label: string;
  // Administration is Phase 4; the other scaffolded sections are Phase 3.
  phase: 1 | 2 | 3 | 4;
  live: boolean;
}

export const SECTIONS: Section[] = [
  { slug: "behavior", label: "Behavioral Overview", phase: 3, live: true },
  { slug: "behavior/sources/detail", label: "Source Behavior", phase: 3, live: true },
  { slug: "anomalies", label: "Anomalies", phase: 3, live: true },
  { slug: "anomalies/detail", label: "Anomaly Investigation", phase: 3, live: true },
  { slug: "", label: "SOC Overview", phase: 1, live: true },
  { slug: "log-sources", label: "Log Sources", phase: 1, live: true },
  { slug: "log-sources/detail", label: "Log Source Detail", phase: 1, live: true },
  { slug: "offenses", label: "Offenses", phase: 3, live: true },
  { slug: "rules", label: "Rule Health", phase: 3, live: true },
  { slug: "coverage", label: "Detection Coverage", phase: 3, live: true },
  { slug: "searches", label: "Search Catalog", phase: 2, live: true },
  { slug: "searches/trend", label: "Search Detail & Trend", phase: 2, live: true },
  { slug: "alerts", label: "Alerts", phase: 2, live: true },
  { slug: "config-changes", label: "Configuration Changes", phase: 3, live: false },
  { slug: "admin", label: "Administration", phase: 4, live: false },
];
