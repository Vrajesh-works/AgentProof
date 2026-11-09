import { describe, expect, it } from "vitest";
import { compact, delta, score, usd } from "./format";
import { href, parseRoute } from "./route";
import type { Span } from "./types";
import { layoutWaterfall, spanSummary } from "./waterfall";

function span(id: string, parent: string | null, start: number, end: number, kind = "tool", attributes = {}): Span {
  return {
    span_id: id,
    trace_id: "t",
    parent_span_id: parent,
    name: id,
    kind,
    start_ns: start,
    end_ns: end,
    duration_ms: (end - start) / 1e6,
    status: "UNSET",
    attributes,
  };
}

describe("format", () => {
  it("formats scores, deltas and money", () => {
    expect(score(0.5)).toBe("0.500");
    expect(score(undefined)).toBe("-");
    expect(delta(1, 0.75)).toBe("-0.250");
    expect(delta(0.5, 0.5)).toBe("0");
    expect(delta(0.5, 0.75)).toBe("+0.250");
    expect(delta(null, 1)).toBe("");
    expect(usd(0)).toBe("$0");
    expect(usd(0.0042)).toBe("$0.0042");
    expect(usd(1.5)).toBe("$1.50");
    expect(compact(13572)).toBe("14k");
    expect(compact(1500)).toBe("1.5k");
  });
});

describe("route", () => {
  it("round-trips every route", () => {
    const routes = [
      { name: "runs" as const },
      { name: "run" as const, runId: "abc123" },
      { name: "trial" as const, runId: "abc", taskId: "cancel-pending-order", index: 2 },
      { name: "diff" as const, base: "a", head: "b" },
    ];
    for (const route of routes) expect(parseRoute(href(route))).toEqual(route);
  });

  it("falls back to the runs list for unknown paths", () => {
    expect(parseRoute("#/nowhere")).toEqual({ name: "runs" });
    expect(parseRoute("")).toEqual({ name: "runs" });
    expect(parseRoute("#/runs/x/tasks/t/trials/-1")).toEqual({ name: "run", runId: "x" });
  });
});

describe("waterfall", () => {
  it("orders spans depth-first and positions them on one axis", () => {
    const spans = [
      span("tool-2", "root", 60e6, 100e6),
      span("root", null, 0, 100e6, "trial"),
      span("llm-1", "root", 0, 50e6, "llm"),
    ];
    const { rows, totalMs } = layoutWaterfall(spans);
    expect(totalMs).toBe(100);
    expect(rows.map((r) => [r.span.span_id, r.depth])).toEqual([
      ["root", 0],
      ["llm-1", 1],
      ["tool-2", 1],
    ]);
    expect(rows[2].offsetPct).toBeCloseTo(60);
    expect(rows[2].widthPct).toBeCloseTo(40);
  });

  it("keeps zero-length spans visible and handles orphans", () => {
    const { rows } = layoutWaterfall([span("a", null, 0, 100), span("b", "missing", 100, 100)]);
    expect(rows).toHaveLength(2);
    expect(rows[1].depth).toBe(0);
    expect(rows[1].widthPct).toBeGreaterThan(0);
    expect(layoutWaterfall([]).rows).toEqual([]);
  });

  it("summarizes spans by kind", () => {
    expect(
      spanSummary(span("l", null, 0, 1, "llm", { "gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 2 })),
    ).toBe("10 in / 2 out tokens");
    expect(spanSummary(span("t", null, 0, 1, "tool", { "agentproof.tool.ok": false, "agentproof.tool.error_kind": "timeout" }))).toBe(
      "error: timeout",
    );
  });
});
