// The application's navigation model.
//
// Two groups, not one flat list. Behavioral analytics is what the platform is
// for — detecting that a log source changed, and explaining what changed — so
// those destinations lead and carry full visual weight. Offenses, rules,
// coverage and inventory are supporting capabilities: reachable, but not
// competing with the product's purpose for the analyst's attention.
//
// Only routes that exist appear here. A navigation entry is a promise that
// something is on the other side of it, and a disabled or placeholder item
// spends the analyst's attention to deliver nothing.

export interface NavItem {
  href: string;
  label: string;
}

export interface NavGroup {
  /** Rendered as a group heading. Null for the primary group, which needs no
   *  label: it is the top of the list. */
  label: string | null;
  items: NavItem[];
}

export const NAV: NavGroup[] = [
  {
    label: null,
    items: [
      { href: "/behavior", label: "Overview" },
      { href: "/anomalies", label: "Anomalies" },
    ],
  },
  {
    label: "Platform",
    items: [
      { href: "/offenses", label: "Offenses" },
      { href: "/rules", label: "Rules" },
      { href: "/coverage", label: "Coverage" },
      { href: "/log-sources", label: "Log Sources" },
      { href: "/searches", label: "Searches" },
      { href: "/alerts", label: "Alerts" },
      { href: "/", label: "SOC Overview" },
    ],
  },
];

/**
 * Whether a path falls under a nav entry.
 *
 * A detail route belongs to the section that owns it, so `/anomalies/{id}`
 * matches `/anomalies`. The root is matched exactly and never by prefix —
 * otherwise "/" would claim every route on the site.
 */
export function navItemMatches(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * The single nav entry that should be marked current for a path.
 *
 * Longest match wins, which is what makes nested sections work: with both
 * `/behavior` and `/behavior/sources` in the nav, the path
 * `/behavior/sources/{id}` resolves to `/behavior/sources` rather than
 * lighting up both. Returns null for a path no entry owns, so nothing is
 * marked current rather than something arbitrary being marked.
 */
export function activeNavHref(
  pathname: string,
  groups: NavGroup[] = NAV,
): string | null {
  let best: string | null = null;
  for (const group of groups) {
    for (const item of group.items) {
      if (!navItemMatches(pathname, item.href)) continue;
      if (best === null || item.href.length > best.length) best = item.href;
    }
  }
  return best;
}
