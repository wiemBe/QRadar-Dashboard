// The 11 platform sections. Phase 1 ships Overview and Log Sources live; the
// rest are scaffolded routes filled in across Phases 2-3.
export interface Section {
  slug: string;
  label: string;
  phase: 1 | 2 | 3;
  live: boolean;
}

export const SECTIONS: Section[] = [
  { slug: "", label: "SOC Overview", phase: 1, live: true },
  { slug: "log-sources", label: "Log Sources", phase: 1, live: true },
  { slug: "log-sources/detail", label: "Log Source Detail", phase: 1, live: true },
  { slug: "searches", label: "Search Catalog", phase: 2, live: true },
  { slug: "searches/trend", label: "Search Detail & Trend", phase: 2, live: true },
  { slug: "anomalies", label: "Anomalies", phase: 2, live: true },
  { slug: "alerts", label: "Alerts", phase: 2, live: true },
  { slug: "offenses", label: "Offenses", phase: 3, live: false },
  { slug: "rules", label: "Rule Health", phase: 3, live: false },
  { slug: "coverage", label: "Detection Coverage", phase: 3, live: false },
  { slug: "config-changes", label: "Configuration Changes", phase: 3, live: false },
  { slug: "admin", label: "Administration", phase: 4, live: false },
];
