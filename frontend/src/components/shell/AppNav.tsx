"use client";

// The shell's navigation.
//
// A client component for one reason: the active route is only knowable from
// the current path, and `usePathname` is a client hook. It renders no data of
// its own, so nothing else about the shell needs to leave the server.
//
// The current entry is marked with `aria-current="page"` as well as with the
// active styling, so the position in the site is announced rather than only
// shown.

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV, activeNavHref, type NavGroup } from "@/lib/nav";

export function AppNav({ groups = NAV }: { groups?: NavGroup[] }) {
  const pathname = usePathname() ?? "/";
  const active = activeNavHref(pathname, groups);

  return (
    <nav aria-label="Primary">
      {groups.map((group, i) => (
        <div
          key={group.label ?? `primary-${i}`}
          className={`nav-group${group.label ? " nav-group-supporting" : ""}`}
        >
          {/* Not a heading element. The shell sits before the page's <h1> in
              document order, so a heading here would put the document's first
              heading in the sidebar and break the page's outline. The label is
              still announced, via `aria-labelledby` on the list below. */}
          {group.label && (
            <div className="nav-group-label" id={`nav-group-${i}`}>
              {group.label}
            </div>
          )}
          <ul
            className="nav-list"
            aria-labelledby={group.label ? `nav-group-${i}` : undefined}
          >
            {group.items.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className="nav-link plain"
                  aria-current={item.href === active ? "page" : undefined}
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}
