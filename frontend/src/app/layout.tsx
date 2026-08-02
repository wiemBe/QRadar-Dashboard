import "./globals.css";
import type { Metadata } from "next";
import { SECTIONS } from "@/lib/sections";

export const metadata: Metadata = {
  title: {
    default: "QRadar Behavioral Anomaly and Investigation Platform",
    template: "%s · QRadar Behavioral Analytics",
  },
  description:
    "Behavioral anomaly detection and investigation for on-premises QRadar: " +
    "seasonal volume baselines, an explicit anomaly lifecycle, and bounded " +
    "evidence answering what changed during an anomalous interval.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // Detail routes are reached from their parent list, not the nav.
  const nav = SECTIONS.filter((s) => !s.slug.includes("/"));
  return (
    <html lang="en">
      <body>
        <div className="layout">
          <aside className="sidebar">
            <h1>QRadar Behavioral Analytics</h1>
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
