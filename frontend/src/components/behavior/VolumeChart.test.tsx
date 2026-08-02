import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VolumeChart } from "./VolumeChart";
import type { MetricBucket } from "@/lib/api";

// jsdom has no layout engine or canvas, so ECharts is stubbed. What is under
// test is the contract with it: modular registration, one init, a resize
// observer, disposal on unmount, and — most importantly — the series data,
// where an unobserved interval must arrive as a null rather than a value.
//
// The stubs are hoisted because the component calls `echarts.use(...)` at
// module scope, which runs before a plain `const` in this file is initialized.
const { setOption, resize, dispose, init, use } = vi.hoisted(() => {
  const setOption = vi.fn();
  const resize = vi.fn();
  const dispose = vi.fn();
  return {
    setOption,
    resize,
    dispose,
    init: vi.fn(() => ({ setOption, resize, dispose })),
    use: vi.fn(),
  };
});

vi.mock("echarts/core", () => ({ init, use }));
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  LegendComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

// `echarts.use(...)` runs once, when the component module is imported — which
// is before any beforeEach clears the spies. The registration is captured here,
// at module scope, so the assertion below still has it.
const registered = (use.mock.calls[0]?.[0] ?? []) as unknown[];

let observerCallback: (() => void) | null = null;
const observe = vi.fn();
const disconnect = vi.fn();

class TestResizeObserver {
  constructor(cb: () => void) {
    observerCallback = cb;
  }
  observe = observe;
  unobserve = vi.fn();
  disconnect = disconnect;
}

function bucket(over: Partial<MetricBucket> = {}): MetricBucket {
  return {
    bucket_start: "2026-07-20T10:00:00Z",
    bucket_seconds: 300,
    event_count: 600,
    average_eps: 2,
    peak_eps: 2.4,
    completeness: "COMPLETE",
    last_event_at: null,
    ...over,
  };
}

/** The observed series ECharts was handed, as [timestamp, value] pairs. */
function observedSeries(): Array<[number, number | null]> {
  const option = setOption.mock.calls[0][0] as {
    series: Array<{ name: string; data: Array<[number, number | null]> }>;
  };
  return option.series.find((s) => s.name === "Observed EPS")!.data;
}

interface StubSeries {
  name: string;
  data: unknown[];
  connectNulls?: boolean;
  markArea?: unknown;
  markLine?: unknown;
}

function seriesNamed(name: string): StubSeries {
  const option = setOption.mock.calls[0][0] as { series: StubSeries[] };
  return option.series.find((s) => s.name === name)!;
}

beforeEach(() => {
  vi.clearAllMocks();
  observerCallback = null;
  globalThis.ResizeObserver = TestResizeObserver as unknown as typeof ResizeObserver;
});

describe("modular imports", () => {
  it("registers only the chart pieces it uses", () => {
    // The full echarts bundle costs ~340 kB per route; this asserts a modular
    // registration happened rather than a blanket `import * as echarts`.
    expect(registered).toHaveLength(7);
  });
});

describe("lifecycle", () => {
  it("initializes exactly one chart", () => {
    render(<VolumeChart buckets={[bucket()]} expected={2} expectedLow={null} expectedHigh={null} />);
    expect(init).toHaveBeenCalledTimes(1);
    expect(setOption).toHaveBeenCalledTimes(1);
  });

  it("observes its container for resize", () => {
    render(<VolumeChart buckets={[bucket()]} expected={2} expectedLow={null} expectedHigh={null} />);
    expect(observe).toHaveBeenCalled();
    observerCallback?.();
    expect(resize).toHaveBeenCalled();
  });

  it("disposes the chart and the observer on unmount", () => {
    const view = render(
      <VolumeChart buckets={[bucket()]} expected={2} expectedLow={null} expectedHigh={null} />,
    );
    view.unmount();
    expect(dispose).toHaveBeenCalledTimes(1);
    expect(disconnect).toHaveBeenCalledTimes(1);
  });

  it("does not initialize a chart when there is nothing to plot", () => {
    render(<VolumeChart buckets={[]} expected={2} expectedLow={null} expectedHigh={null} />);
    expect(init).not.toHaveBeenCalled();
  });
});

describe("missing and partial data", () => {
  it("plots a fully observed bucket's value", () => {
    render(
      <VolumeChart
        buckets={[bucket({ average_eps: 2.5 })]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
      />,
    );
    expect(observedSeries()[0][1]).toBe(2.5);
  });

  it("plots a partial bucket as null, not as its undercount", () => {
    render(
      <VolumeChart
        buckets={[bucket({ completeness: "PARTIAL", average_eps: 0.4 })]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
      />,
    );
    expect(observedSeries()[0][1]).toBeNull();
  });

  it("plots a missing bucket as null, not as zero", () => {
    render(
      <VolumeChart
        buckets={[bucket({ completeness: "MISSING", average_eps: 0 })]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
      />,
    );
    expect(observedSeries()[0][1]).toBeNull();
  });

  it("plots an observed zero as zero", () => {
    // A source seen sending nothing is a measurement and a finding; it must
    // not be erased along with the intervals nobody observed.
    render(
      <VolumeChart
        buckets={[bucket({ average_eps: 0, event_count: 0 })]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
      />,
    );
    expect(observedSeries()[0][1]).toBe(0);
  });

  it("never connects the line across a null", () => {
    render(<VolumeChart buckets={[bucket()]} expected={2} expectedLow={null} expectedHigh={null} />);
    expect(seriesNamed("Observed EPS").connectNulls).toBe(false);
  });

  it("shades intervals that were not fully observed", () => {
    render(
      <VolumeChart
        buckets={[bucket({ completeness: "PARTIAL" })]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
      />,
    );
    const area = seriesNamed("Observed EPS").markArea as { data: unknown[] };
    expect(area.data).toHaveLength(1);
  });

  it("tells the reader that a gap is not zero traffic", () => {
    render(<VolumeChart buckets={[bucket()]} expected={2} expectedLow={null} expectedHigh={null} />);
    expect(screen.getByText(/They are not zero traffic/i)).toBeInTheDocument();
  });

  it("calls an empty window an absence of collection, not zero traffic", () => {
    render(<VolumeChart buckets={[]} expected={2} expectedLow={null} expectedHigh={null} />);
    expect(screen.getByText(/absence of collection, not an observation of zero/i)).toBeInTheDocument();
  });
});

describe("expected band", () => {
  it("draws the expected line and both bounds", () => {
    render(
      <VolumeChart buckets={[bucket()]} expected={2} expectedLow={1.7} expectedHigh={2.3} />,
    );
    expect(seriesNamed("Expected EPS").data).toHaveLength(1);
    expect(seriesNamed("Expected low").data).toHaveLength(1);
    expect(seriesNamed("Expected high").data).toHaveLength(1);
  });

  it("draws no expected line when there is no baseline to draw", () => {
    // An unbaselined source has no expectation. A flat line at 0 would invent
    // one and make any traffic at all look like a spike against it.
    render(
      <VolumeChart buckets={[bucket()]} expected={null} expectedLow={null} expectedHigh={null} />,
    );
    expect(seriesNamed("Expected EPS").data).toHaveLength(0);
  });
});

describe("overlays", () => {
  it("shades the anomalous interval", () => {
    render(
      <VolumeChart
        buckets={[bucket()]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
        anomalies={[{ start: "2026-07-20T10:00:00Z", end: "2026-07-20T10:05:00Z" }]}
      />,
    );
    const area = seriesNamed("Observed EPS").markArea as { data: unknown[] };
    expect(area.data).toHaveLength(1);
  });

  it("extends a still-running anomaly to the last observation", () => {
    render(
      <VolumeChart
        buckets={[bucket()]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
        anomalies={[{ start: "2026-07-20T10:00:00Z", end: null }]}
      />,
    );
    const area = seriesNamed("Observed EPS").markArea as {
      data: Array<[{ xAxis: number }, { xAxis: number }]>;
    };
    expect(area.data[0][1].xAxis).toBe(Date.parse("2026-07-20T10:00:00Z"));
  });

  it("marks lifecycle transitions on the timeline", () => {
    render(
      <VolumeChart
        buckets={[bucket()]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
        transitions={[
          { at: "2026-07-20T10:00:00Z", label: "OPEN" },
          { at: "2026-07-20T10:05:00Z", label: "RESOLVED" },
        ]}
      />,
    );
    const line = seriesNamed("Observed EPS").markLine as { data: unknown[] };
    expect(line.data).toHaveLength(2);
  });
});
