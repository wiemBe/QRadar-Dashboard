"use client";

// Observed volume against its expected baseline band, with the anomaly
// interval and lifecycle transitions overlaid.
//
// ECharts is imported here and nowhere else on these routes: it touches
// `document` and `ResizeObserver` on construction, so it must stay out of any
// server component. The pages fetch their data server-side and pass it in.
//
// The chart's honesty rules, all of which have a test:
//   * A bucket that was not fully observed is a null, never its reported value.
//   * `connectNulls` is false, so an uncollected interval stays a visible gap
//     rather than a straight line asserting interpolatable traffic.
//   * Unobserved intervals are shaded so a gap cannot be mistaken for silence.

import { LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef } from "react";

import type { MetricBucket } from "@/lib/api";
import { buildSeries, kindMeaning, unobservedIntervals } from "@/lib/timeseries";

import type { EChartsOption } from "echarts";

// Modular registration rather than the full `echarts` bundle: this needs one
// chart type and five components, and the full build costs ~340 kB per route.
echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  CanvasRenderer,
]);

type MarkAreaPair = [
  { xAxis: number; itemStyle?: { color: string }; name?: string },
  { xAxis: number },
];

export interface AnomalyInterval {
  start: string;
  end: string | null;
  label?: string;
}

export interface TransitionMarker {
  at: string;
  label: string;
}

export function VolumeChart({
  buckets,
  expected,
  expectedLow,
  expectedHigh,
  anomalies = [],
  transitions = [],
  ariaLabel = "Observed volume against expected baseline",
  textSummary,
}: {
  buckets: MetricBucket[];
  expected: number | null;
  expectedLow: number | null;
  expectedHigh: number | null;
  anomalies?: AnomalyInterval[];
  transitions?: TransitionMarker[];
  ariaLabel?: string;
  /** A prose description of what the chart shows, for readers who cannot see
   *  it. An aria-label alone says only that a chart is present. */
  textSummary?: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  const points = useMemo(() => buildSeries(buckets), [buckets]);
  const unobserved = useMemo(() => unobservedIntervals(buckets), [buckets]);

  const option = useMemo<EChartsOption | null>(() => {
    if (points.length === 0) return null;

    const flat = (value: number | null) =>
      value == null ? [] : points.map((p) => [p.t, value] as [number, number]);

    // Shaded regions for anything not fully observed, plus the anomaly
    // interval itself. Rendered behind the line, in different colours.
    // ECharts types a 2D mark area as a pair, so the tuple shape is explicit.
    const areas: MarkAreaPair[] = [
      ...unobserved.map(
        (u): MarkAreaPair => [
          {
            xAxis: u.start,
            itemStyle: { color: "rgba(210, 153, 34, 0.14)" },
            name: u.kind,
          },
          { xAxis: u.end },
        ],
      ),
      ...anomalies.map(
        (a): MarkAreaPair => [
          {
            xAxis: Date.parse(a.start),
            itemStyle: { color: "rgba(248, 81, 73, 0.13)" },
            name: a.label ?? "anomaly",
          },
          // An anomaly with no end is still running; the shading therefore
          // extends to the last observed point rather than stopping short and
          // implying the incident closed.
          { xAxis: a.end ? Date.parse(a.end) : points[points.length - 1].t },
        ],
      ),
    ];

    // Honour the OS-level preference: the CSS guard cannot reach a canvas
    // renderer, so the chart has to be told directly.
    const reduceMotion =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    return {
      animation: !reduceMotion,
      grid: { left: 56, right: 24, top: 40, bottom: 40 },
      textStyle: { color: "#8b98b0" },
      legend: {
        top: 4,
        textStyle: { color: "#8b98b0" },
        data: ["Observed EPS", "Expected EPS", "Expected low", "Expected high"],
      },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#131a2a",
        borderColor: "#263048",
        textStyle: { color: "#e6edf7" },
        formatter: (params: unknown) => {
          const list = Array.isArray(params) ? params : [params];
          const first = list[0] as { dataIndex?: number } | undefined;
          const p = first?.dataIndex != null ? points[first.dataIndex] : undefined;
          if (!p) return "";
          const observed =
            p.observed != null
              ? `Observed: ${p.observed.toFixed(2)} EPS`
              : `Observed: not measured (${kindMeaning(p.kind)})`;
          const reported =
            p.observed == null && p.reported != null
              ? `Reported by the partial collection: ${p.reported.toFixed(2)} EPS`
              : "";
          const band =
            expectedLow != null && expectedHigh != null
              ? `Expected range: ${expectedLow.toFixed(2)} – ${expectedHigh.toFixed(2)} EPS`
              : "";
          // Whether this instant falls inside a known anomalous interval, so
          // the tooltip states the incident rather than only the number.
          const inAnomaly = anomalies.find((a) => {
            const start = Date.parse(a.start);
            const end = a.end ? Date.parse(a.end) : Number.POSITIVE_INFINITY;
            return p.t >= start && p.t <= end;
          });
          return [
            new Date(p.t).toLocaleString(),
            observed,
            reported,
            expected != null ? `Expected: ${expected.toFixed(2)} EPS` : "",
            band,
            `Collection: ${kindMeaning(p.kind)}`,
            inAnomaly ? `Anomalous interval: ${inAnomaly.label ?? "anomaly"}` : "",
          ]
            .filter(Boolean)
            .join("<br/>");
        },
      },
      xAxis: {
        type: "time",
        axisLine: { lineStyle: { color: "#263048" } },
      },
      yAxis: {
        type: "value",
        name: "EPS",
        splitLine: { lineStyle: { color: "#1a2336" } },
        axisLine: { lineStyle: { color: "#263048" } },
      },
      series: [
        {
          type: "line",
          name: "Observed EPS",
          data: points.map((p) => [p.t, p.observed] as [number, number | null]),
          // The single most important option on this chart. Bridging a null
          // would draw traffic that was never observed.
          connectNulls: false,
          showSymbol: true,
          symbolSize: 5,
          lineStyle: { color: "#4f8cff", width: 2 },
          itemStyle: { color: "#4f8cff" },
          markArea: areas.length
            ? {
                silent: true,
                // Labels off. With several shaded intervals in one window they
                // overprint each other into an illegible smear; the shading is
                // explained in the note below the chart instead.
                label: { show: false },
                data: areas,
              }
            : undefined,
          markLine: transitions.length
            ? {
                silent: true,
                symbol: "none",
                // The markers stay; their labels do not. Four transitions
                // minutes apart render their text on top of one another, and
                // the Lifecycle tab states the same sequence legibly.
                label: { show: false },
                lineStyle: { color: "#8b98b0", type: "dashed" },
                data: transitions.map((t) => ({
                  xAxis: Date.parse(t.at),
                  name: t.label,
                })),
              }
            : undefined,
        },
        {
          type: "line",
          name: "Expected EPS",
          data: flat(expected),
          showSymbol: false,
          lineStyle: { color: "#3fb950", type: "dashed", width: 1.5 },
          itemStyle: { color: "#3fb950" },
        },
        {
          type: "line",
          name: "Expected low",
          data: flat(expectedLow),
          showSymbol: false,
          lineStyle: { color: "#3fb950", type: "dotted", width: 1, opacity: 0.7 },
          itemStyle: { color: "#3fb950" },
        },
        {
          type: "line",
          name: "Expected high",
          data: flat(expectedHigh),
          showSymbol: false,
          lineStyle: { color: "#3fb950", type: "dotted", width: 1, opacity: 0.7 },
          itemStyle: { color: "#3fb950" },
        },
      ],
    };
  }, [points, unobserved, expected, expectedLow, expectedHigh, anomalies, transitions]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !option) return;

    const chart = echarts.init(el);
    chart.setOption(option);

    // ECharts does not track element size on its own; without this the chart
    // keeps its first-paint width when the layout changes.
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);

    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [option]);

  if (points.length === 0) {
    return (
      <div className="notice">
        No metric buckets were collected for this window. This is an absence of
        collection, not an observation of zero traffic.
      </div>
    );
  }

  return (
    <>
      <div ref={containerRef} className="chart" role="img" aria-label={ariaLabel} />
      {/* The chart's content as prose. A canvas is opaque to a screen reader,
          so without this the series, the band and the gaps are unreachable. */}
      {textSummary && <p className="sr-only">{textSummary}</p>}
      <p className="subtitle chart-note">
        Gaps in the observed line are intervals that were not fully collected —
        shaded amber. They are not zero traffic. Red shading marks the anomalous
        interval, and dashed vertical rules mark lifecycle transitions.
      </p>
    </>
  );
}
