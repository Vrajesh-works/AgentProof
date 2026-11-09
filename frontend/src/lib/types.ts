// Shapes returned by the AgentProof API (mirrors agentproof.results).

export interface AgentInfo {
  name: string;
  version: string;
  [key: string]: unknown;
}

export interface RunSummary {
  tasks: number;
  trials: number;
  pass_at_1: number;
  pass_at_k: number;
  pass_hat_k: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  mean_latency_ms: number;
}

export interface RunListItem {
  id: string;
  suite: string;
  environment: string;
  agent: AgentInfo;
  k: number;
  n: number;
  seed: number;
  created_at: string;
  git_sha: string | null;
  label: string | null;
  summary: RunSummary;
}

export interface Check {
  name: string;
  passed: boolean;
  detail: string;
}

export interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  output: unknown;
  error: string | null;
  error_kind: string | null;
  latency_ms: number;
}

export interface Span {
  span_id: string;
  trace_id: string;
  parent_span_id: string | null;
  name: string;
  kind: string;
  start_ns: number;
  end_ns: number;
  duration_ms: number;
  status: string;
  attributes: Record<string, unknown>;
}

export interface Trial {
  index: number;
  passed: boolean;
  stop_reason: string;
  error: string | null;
  checks: Check[];
  final_answer: string;
  tool_calls: ToolCall[];
  usage: Record<string, number>;
  latency_ms: number;
  spans: Span[];
}

export interface TaskResult {
  task_id: string;
  description: string;
  tags: string[];
  n: number;
  successes: number;
  pass_at_1: number;
  pass_at_k: number;
  pass_hat_k: number;
  trials: Trial[];
}

export interface Run extends Omit<RunListItem, "summary"> {
  summary: RunSummary;
  tasks: TaskResult[];
}

export type MetricValues = { pass_hat_k: number; pass_at_k: number; pass_at_1: number };

export interface DiffTask {
  task_id: string;
  status: "regressed" | "improved" | "unchanged" | "new" | "missing";
  base: MetricValues | null;
  head: MetricValues | null;
  regressions: string[];
  improvements: string[];
}

export interface DiffReport {
  failed: boolean;
  tolerance: number;
  metrics: string[];
  base: { id: string; agent: AgentInfo; summary: RunSummary };
  head: { id: string; agent: AgentInfo; summary: RunSummary };
  summary_regressions: string[];
  tasks: DiffTask[];
  markdown: string;
}
