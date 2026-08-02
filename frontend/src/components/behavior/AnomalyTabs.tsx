"use client";

// The investigation's three secondary panels.
//
// A thin client wrapper so the page itself stays a server component: the
// panels are rendered on the server and passed in as props, and only the tab
// mechanics — selection, arrow-key movement, focus — need to run in the
// browser.

import { Tabs } from "@/components/ui/Tabs";
import type { ReactNode } from "react";

export function AnomalyTabs({
  evidence,
  lifecycle,
  technical,
}: {
  evidence: ReactNode;
  lifecycle: ReactNode;
  technical: ReactNode;
}) {
  return (
    <Tabs
      label="Investigation detail"
      tabs={[
        // Evidence first: "what changed" is the question the page exists to
        // answer, and the other two panels support that answer.
        { id: "evidence", label: "Evidence", content: () => evidence },
        { id: "lifecycle", label: "Lifecycle", content: () => lifecycle },
        { id: "technical", label: "Technical details", content: () => technical },
      ]}
    />
  );
}
