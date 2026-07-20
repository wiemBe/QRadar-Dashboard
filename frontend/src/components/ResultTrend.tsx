"use client";

// Result-trend chart for one scheduled search.
//
// ECharts is imported here and nowhere else: it touches `document` and
// `ResizeObserver` on construction, so it must never be pulled into a server
// component. The page stays a server component and passes only the search id.

import { LineChart } from "echarts/charts";
import {
  GridComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef, useState } from "react";

import { actionErrorMessage, api, type ResultMetricPoint, type SearchResultTrend } from "@/lib/api";

import type { EChartsOption } from "echarts";

// Modular registration rather than `import * as echarts from "echarts"`: the
// full bundle costs ~340 kB on this route, and the trend needs one chart type
// and four components.
echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  MarkLineComponent,
  MarkPointComponent,
  CanvasRenderer,
]);

const FAILED_STATUSES = new Set(["FAILED", "TIMEOUT", "CANCELLED"]);

function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms} ms`;
}

/** Indices where the query version changes — results either side of an AQL
 *  change are not comparable, so the chart must say where the break is. */
function versionBoundaries(points: ResultMetricPoint[]): ResultMetricPoint[] {
  return points.filter((p, i) => i > 0 && p.query_version !== points[i - 1].query_version);
}

export function ResultTrend({ searchId, metricKey = "total" }: { searchId: string; metricKey?: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  const [trend, setTrend] = useState<SearchResultTrend | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Guards against a late response from a previous searchId overwriting a
    // newer one, and against setState after unmount.
    let active = true;
    setLoading(true);
    setError(null);
    api
      .searchResults(searchId, metricKey)
      .then((data) => {
        if (active) setTrend(data);
      })
      .catch((err) => {
        if (active) setError(actionErrorMessage(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [searchId, metricKey]);

  const option = useMemo<EChartsOption | null>(() => {
    if (!trend || trend.points.length === 0) return null;
    const points = trend.points;

    // A failed run has no trustworthy count. Plotting it as 0 would invent a
    // cliff that never happened, so the line is broken with null and the run is
    // marked separately.
    const values = points.map((p) => (FAILED_STATUSES.has(p.execution_status) ? null : p.value));

    const failures = points
      .map((p, i) => (FAILED_STATUSES.has(p.execution_status) ? { coord: [i, 0], value: p.execution_status } : null))
      .filter((x): x is { coord: number[]; value: string } => x !== null);

    const boundaries = versionBoundaries(points);

    return {
      grid: { left: 56, right: 24, top: 32, bottom: 48 },
      textStyle: { color: "#8b98b0" },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#131a2a",
        borderColor: "#263048",
        textStyle: { color: "#e6edf7" },
        formatter: (params: unknown) => {
          const list = Array.isArray(params) ? params : [params];
          const first = list[0] as { dataIndex?: number } | undefined;
          const idx = first?.dataIndex;
          if (idx == null || !points[idx]) return "";
          const p = points[idx];
          const count = FAILED_STATUSES.has(p.execution_status)
            ? "no result (run failed)"
            : String(p.value);
          return [
            new Date(p.bucket_start).toLocaleString(),
            `Result count: ${count}`,
            `Status: ${p.execution_status}`,
            `Duration: ${formatDuration(p.duration_ms)}`,
            `Query version: v${p.query_version}`,
            p.threshold_breached ? "Threshold: breached" : "",
          ]
            .filter(Boolean)
            .join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: points.map((p) => new Date(p.bucket_start).toLocaleString()),
        axisLine: { lineStyle: { color: "#263048" } },
        axisLabel: { show: false },
      },
      yAxis: {
        type: "value",
        name: metricKey,
        splitLine: { lineStyle: { color: "#1a2336" } },
        axisLine: { lineStyle: { color: "#263048" } },
      },
      series: [
        {
          type: "line",
          name: metricKey,
          data: values,
          // Leave a visible gap rather than bridging a failed run.
          connectNulls: false,
          showSymbol: true,
          symbolSize: 6,
          lineStyle: { color: "#4f8cff", width: 2 },
          itemStyle: { color: "#4f8cff" },
          markLine: boundaries.length
            ? {
                silent: true,
                symbol: "none",
                label: {
                  formatter: (p: { name?: string }) => p.name ?? "",
                  color: "#d29922",
                  position: "insideEndTop",
                },
                lineStyle: { color: "#d29922", type: "dashed" },
                data: boundaries.map((b) => ({
                  xAxis: points.indexOf(b),
                  name: `v${b.query_version}`,
                })),
              }
            : undefined,
          markPoint: failures.length
            ? {
                symbol: "pin",
                symbolSize: 34,
                itemStyle: { color: "#f85149" },
                label: { color: "#ffffff", fontSize: 9, formatter: "fail" },
                data: failures.map((f) => ({ coord: f.coord, name: f.value })),
              }
            : undefined,
        },
        ...(trend.threshold_value != null
          ? [
              {
                type: "line" as const,
                name: "threshold",
                data: points.map(() => trend.threshold_value),
                showSymbol: false,
                lineStyle: { color: "#f85149", type: "dotted" as const, width: 1 },
                itemStyle: { color: "#f85149" },
                tooltip: { show: false },
              },
            ]
          : []),
      ],
    };
  }, [trend, metricKey]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !option) return;

    const chart = echarts.init(el);
    chartRef.current = chart;
    chart.setOption(option);

    // ECharts does not track element size on its own; without this the chart
    // keeps its first-paint width when the layout changes.
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);

    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [option]);

  if (loading) {
    return <div className="notice">Loading result trend…</div>;
  }
  if (error) {
    return (
      <div className="notice" role="alert">
        {error}
      </div>
    );
  }
  if (!trend || trend.points.length === 0) {
    return (
      <div className="notice">
        No result metrics recorded yet. The trend appears once this search has run.
      </div>
    );
  }

  return (
    <>
      <div ref={containerRef} className="chart" role="img" aria-label="Search result trend" />
      <p className="subtitle" style={{ marginTop: 8 }}>
        {trend.count} point{trend.count === 1 ? "" : "s"} · metric <code>{trend.metric_key}</code>
        {trend.threshold_value != null
          ? ` · threshold ${trend.threshold_operator} ${trend.threshold_value}`
          : ""}
        {versionBoundaries(trend.points).length > 0
          ? " · dashed lines mark query-version changes; results across versions are not comparable"
          : ""}
      </p>
    </>
  );
}
