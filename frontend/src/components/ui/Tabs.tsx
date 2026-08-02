"use client";

// An accessible tab set.
//
// Implements the WAI-ARIA tabs pattern rather than a set of buttons that swap
// content: arrow keys move between tabs, Home and End jump to the ends, and
// only the active tab is in the page's tab order, so a keyboard user reaches
// the panel in one Tab press instead of stepping through every tab first.
//
// Panels are unmounted rather than hidden. On this page a panel holds a
// contributor table and a provenance list, and rendering all three would put
// the technical detail back in the document that the tabs exist to defer.

import { useId, useRef, useState, type ReactNode } from "react";

export interface TabSpec {
  id: string;
  label: string;
  /** Rendered lazily: only the active panel's node is created. */
  content: () => ReactNode;
}

export function Tabs({
  tabs,
  label,
  initialId,
}: {
  tabs: TabSpec[];
  /** Names the tab list for assistive technology. */
  label: string;
  initialId?: string;
}) {
  const baseId = useId();
  const [activeId, setActiveId] = useState(initialId ?? tabs[0]?.id);
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  if (tabs.length === 0) return null;
  const activeIndex = Math.max(
    0,
    tabs.findIndex((t) => t.id === activeId),
  );
  const active = tabs[activeIndex];

  const focusTab = (index: number) => {
    const next = tabs[(index + tabs.length) % tabs.length];
    setActiveId(next.id);
    refs.current[next.id]?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    switch (event.key) {
      case "ArrowRight":
        event.preventDefault();
        focusTab(activeIndex + 1);
        break;
      case "ArrowLeft":
        event.preventDefault();
        focusTab(activeIndex - 1);
        break;
      case "Home":
        event.preventDefault();
        focusTab(0);
        break;
      case "End":
        event.preventDefault();
        focusTab(tabs.length - 1);
        break;
      default:
        break;
    }
  };

  return (
    <div className="tabs">
      <div role="tablist" aria-label={label} className="tablist">
        {tabs.map((tab) => {
          const selected = tab.id === active.id;
          return (
            <button
              key={tab.id}
              ref={(el) => {
                refs.current[tab.id] = el;
              }}
              type="button"
              role="tab"
              id={`${baseId}-tab-${tab.id}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel-${tab.id}`}
              // Only the active tab is reachable by Tab; the rest are reached
              // with the arrow keys, per the ARIA tabs pattern.
              tabIndex={selected ? 0 : -1}
              className={`tab${selected ? " tab-active" : ""}`}
              onClick={() => setActiveId(tab.id)}
              onKeyDown={onKeyDown}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={`${baseId}-panel-${active.id}`}
        aria-labelledby={`${baseId}-tab-${active.id}`}
        className="tabpanel"
        tabIndex={0}
      >
        {active.content()}
      </div>
    </div>
  );
}
