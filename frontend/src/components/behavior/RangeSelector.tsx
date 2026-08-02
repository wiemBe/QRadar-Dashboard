// The chart's time-range control.
//
// Plain links rather than a control that fetches: the range belongs in the URL
// so the view is shareable, and links are keyboard-operable and announced with
// no script at all. `aria-current` marks the active range, so the selection is
// conveyed by more than the highlight.
//
// Every option is bounded. The metrics endpoint caps `limit` at 5000 and 24
// hours of 60-second buckets is 1440 rows, so no option here can produce an
// unbounded request.

import Link from "next/link";

import { RANGE_HOURS, rangeLabel, type RangeHours } from "@/lib/sourceDetail";

export function RangeSelector({
  sourceId,
  active,
}: {
  sourceId: string;
  active: RangeHours;
}) {
  return (
    <nav className="range-selector" aria-label="Chart time range">
      <span className="muted">Range</span>
      {RANGE_HOURS.map((hours) => (
        <Link
          key={hours}
          href={`/behavior/sources/${sourceId}?range=${hours}`}
          className={`range-option${hours === active ? " range-option-active" : ""}`}
          aria-current={hours === active ? "page" : undefined}
        >
          {rangeLabel(hours)}
        </Link>
      ))}
    </nav>
  );
}
