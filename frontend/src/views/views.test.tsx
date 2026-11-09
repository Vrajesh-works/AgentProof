import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DiffReport, Run, RunListItem, Trial } from "../lib/types";
import { DiffReportView } from "./DiffView";
import { RunDetail } from "./RunView";
import { RunsTable, RunsView } from "./RunsView";
import { TrialDetail } from "./TrialView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const summary = {
  tasks: 2,
  trials: 8,
  pass_at_1: 0.875,
  pass_at_k: 1,
  pass_hat_k: 0.5,
  input_tokens: 1200,
  output_tokens: 300,
  cost_usd: 0,
  mean_latency_ms: 3.2,
};

const listItem = (id: string, suite = "retail-core"): RunListItem => ({
  id,
  suite,
  environment: "retail",
  agent: { name: "scripted", version: id },
  k: 4,
  n: 4,
  seed: 0,
  created_at: "2026-11-08T12:00:00+00:00",
  git_sha: null,
  label: null,
  summary,
});

const trial = (index: number, passed: boolean): Trial => ({
  index,
  passed,
  stop_reason: "done",
  error: null,
  checks: [
    { name: "completed", passed: true, detail: "" },
    { name: "state_diff", passed, detail: passed ? "" : "orders/W1/status: expected 'cancelled', unchanged" },
  ],
  final_answer: "Cancelled.",
  tool_calls: [],
  usage: { steps: 2, tool_calls: 1, input_tokens: 100, output_tokens: 20, cost_usd: 0 },
  latency_ms: 2.5,
  spans: [
    {
      span_id: "a",
      trace_id: "t",
      parent_span_id: null,
      name: "trial t #0",
      kind: "trial",
      start_ns: 0,
      end_ns: 2_000_000,
      duration_ms: 2,
      status: passed ? "UNSET" : "ERROR",
      attributes: { "agentproof.passed": passed },
    },
    {
      span_id: "b",
      trace_id: "t",
      parent_span_id: "a",
      name: "tool.call cancel_pending_order",
      kind: "tool",
      start_ns: 500_000,
      end_ns: 1_000_000,
      duration_ms: 0.5,
      status: "UNSET",
      attributes: { "agentproof.tool.ok": true, "agentproof.tool.args": '{"order_id": "W1"}' },
    },
  ],
});

describe("RunsTable", () => {
  it("enables compare only for two runs of the same suite", () => {
    render(<RunsTable runs={[listItem("r1"), listItem("r2"), listItem("r3", "airline-core")]} />);
    const compare = screen.getByText("Compare");
    expect(compare.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(screen.getByLabelText("Select run r1"));
    fireEvent.click(screen.getByLabelText("Select run r2"));
    expect(compare.getAttribute("aria-disabled")).toBe("false");
    expect(compare.getAttribute("href")).toBe("#/diff?base=r1&head=r2");
    fireEvent.click(screen.getByLabelText("Select run r3"));
    expect(compare.getAttribute("aria-disabled")).toBe("true");
  });

  it("shows an empty state", () => {
    render(<RunsTable runs={[]} />);
    expect(screen.getByText("No runs yet.")).toBeTruthy();
  });
});

describe("RunsView", () => {
  it("loads runs from the API and reports errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify([listItem("r9")])));
    render(<RunsView />);
    expect(await screen.findByText("r9")).toBeTruthy();
    cleanup();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "database down" }), { status: 500 }),
    );
    render(<RunsView />);
    expect((await screen.findByRole("alert")).textContent).toContain("database down");
  });
});

describe("RunDetail", () => {
  it("marks flaky tasks and links each trial", () => {
    const run: Run = {
      ...listItem("r1"),
      tasks: [
        {
          task_id: "cancel-pending-order",
          description: "",
          tags: [],
          n: 2,
          successes: 1,
          pass_at_1: 0.5,
          pass_at_k: 1,
          pass_hat_k: 0,
          trials: [trial(0, true), trial(1, false)],
        },
      ],
    };
    render(<RunDetail run={run} />);
    expect(screen.getByText("flaky")).toBeTruthy();
    const failed = screen.getByLabelText("Trial 1 failed");
    expect(failed.getAttribute("href")).toBe("#/runs/r1/tasks/cancel-pending-order/trials/1");
  });
});

describe("TrialDetail", () => {
  it("shows failing checks and expands span attributes", () => {
    render(<TrialDetail trial={trial(1, false)} taskId="cancel-pending-order" />);
    expect(screen.getByText(/expected 'cancelled'/)).toBeTruthy();
    fireEvent.click(screen.getByText("tool.call cancel_pending_order"));
    expect(screen.getByText('{"order_id": "W1"}')).toBeTruthy();
  });
});

describe("DiffReportView", () => {
  it("puts regressions first and shows the verdict", () => {
    const report: DiffReport = {
      failed: true,
      tolerance: 0,
      metrics: ["pass_hat_k"],
      base: { id: "b", agent: { name: "scripted", version: "v1" }, summary: { ...summary, pass_hat_k: 1 } },
      head: { id: "h", agent: { name: "scripted", version: "v2" }, summary },
      summary_regressions: ["pass^k 1.000 -> 0.500"],
      tasks: [
        { task_id: "a-same", status: "unchanged", base: { pass_hat_k: 1, pass_at_k: 1, pass_at_1: 1 }, head: { pass_hat_k: 1, pass_at_k: 1, pass_at_1: 1 }, regressions: [], improvements: [] },
        { task_id: "z-broke", status: "regressed", base: { pass_hat_k: 1, pass_at_k: 1, pass_at_1: 1 }, head: { pass_hat_k: 0, pass_at_k: 1, pass_at_1: 0.5 }, regressions: ["pass_hat_k"], improvements: [] },
      ],
      markdown: "",
    };
    render(<DiffReportView report={report} />);
    expect(screen.getByRole("status").textContent).toContain("Gate would fail");
    const rows = screen.getAllByRole("row").map((r) => r.textContent ?? "");
    const taskRows = rows.filter((r) => r.includes("z-broke") || r.includes("a-same"));
    expect(taskRows[0]).toContain("z-broke");
    expect(screen.getByText("-0.500")).toBeTruthy();
  });
});
