import "./globals.css";
import type { Metadata } from "next";
import { SECTIONS } from "@/lib/sections";

export const metadata: Metadata = {
  title: "QRadar Observability",
  description: "On-premises QRadar SOC analytics and health monitoring",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // Detail routes are reached from their parent list, not the nav.
  const nav = SECTIONS.filter((s) => !s.slug.includes("/"));
  return (
    <html lang="en">
      <body>
        <div className="layout">
          <aside className="sidebar">
            <h1>QRadar Observability</h1>
            <nav>
              {nav.map((s) => (
                <a key={s.slug} href={`/${s.slug}`}>
                  <span>{s.label}</span>
                  {!s.live && <span className="badge">P{s.phase}</span>}
                </a>
              ))}
            </nav>
          </aside>
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
