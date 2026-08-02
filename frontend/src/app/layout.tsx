import "./globals.css";
import type { Metadata } from "next";

import { AppNav } from "@/components/shell/AppNav";

export const metadata: Metadata = {
  title: {
    default: "Behavior Analytics",
    template: "%s · Behavior Analytics",
  },
  description:
    "Behavioral anomaly detection and investigation for on-premises QRadar: " +
    "seasonal volume baselines, an explicit anomaly lifecycle, and bounded " +
    "evidence answering what changed during an anomalous interval.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="layout">
          <aside className="sidebar">
            {/* The brand is a link, not a heading: each page's own title is the
                document's <h1>, and the shell must not take that level. */}
            <a href="/behavior" className="brand plain">
              <span className="brand-name">Behavior Analytics</span>
              <span className="brand-sub">QRadar Anomaly &amp; Investigation</span>
            </a>
            <AppNav />
          </aside>
          <main className="main" id="content">
            <div className="page">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
