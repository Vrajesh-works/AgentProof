import type { Span } from "./types";

export interface WaterfallRow {
  span: Span;
  depth: number;
  /** Offset from the start of the root span, as a percentage of the total width. */
  offsetPct: number;
  /** Width as a percentage of the total, with a minimum so tiny spans stay visible. */
  widthPct: number;
}

const MIN_WIDTH_PCT = 0.6;

/**
 * Order spans depth-first under their parents and place them on a shared time axis.
 * Spans whose parent is missing are treated as roots.
 */
export function layoutWaterfall(spans: Span[]): { rows: WaterfallRow[]; totalMs: number } {
  if (spans.length === 0) return { rows: [], totalMs: 0 };
  const ids = new Set(spans.map((s) => s.span_id));
  const children = new Map<string | null, Span[]>();
  for (const span of spans) {
    const parent = span.parent_span_id && ids.has(span.parent_span_id) ? span.parent_span_id : null;
    const list = children.get(parent) ?? [];
    list.push(span);
    children.set(parent, list);
  }
  for (const list of children.values()) list.sort((a, b) => a.start_ns - b.start_ns || a.name.localeCompare(b.name));

  const start = Math.min(...spans.map((s) => s.start_ns));
  const end = Math.max(...spans.map((s) => s.end_ns));
  const total = Math.max(end - start, 1);
  const rows: WaterfallRow[] = [];

  const visit = (parent: string | null, depth: number) => {
    for (const span of children.get(parent) ?? []) {
      const widthPct = Math.min(Math.max(((span.end_ns - span.start_ns) / total) * 100, MIN_WIDTH_PCT), 100);
      // Pull late spans left instead of shrinking them, so a span ending the trace stays visible.
      const offsetPct = Math.min(((span.start_ns - start) / total) * 100, 100 - widthPct);
      rows.push({ span, depth, offsetPct, widthPct });
      visit(span.span_id, depth + 1);
    }
  };
  visit(null, 0);
  return { rows, totalMs: total / 1e6 };
}

/** One-line description of what a span cost or did, for the waterfall label. */
export function spanSummary(span: Span): string {
  const a = span.attributes;
  if (span.kind === "llm") {
    const input = Number(a["gen_ai.usage.input_tokens"] ?? 0);
    const output = Number(a["gen_ai.usage.output_tokens"] ?? 0);
    const cost = Number(a["agentproof.cost_usd"] ?? 0);
    return `${input} in / ${output} out tokens${cost ? `, $${cost.toFixed(4)}` : ""}`;
  }
  if (span.kind === "tool") {
    if (a["agentproof.tool.ok"] === false) return `error: ${String(a["agentproof.tool.error_kind"] ?? "")}`;
    return "ok";
  }
  if (span.kind === "trial") {
    return a["agentproof.passed"] ? "passed" : `failed (${String(a["agentproof.stop_reason"] ?? "")})`;
  }
  return "";
}
