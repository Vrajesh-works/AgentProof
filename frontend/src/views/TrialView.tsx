import { useState } from "react";
import { Badge, Loadable, Stat } from "../components/common";
import { api } from "../lib/api";
import { compact, ms, usd } from "../lib/format";
import { href } from "../lib/route";
import type { Span, Trial } from "../lib/types";
import { useAsync } from "../lib/useAsync";
import { layoutWaterfall, spanSummary } from "../lib/waterfall";

export function TrialView({ runId, taskId, index }: { runId: string; taskId: string; index: number }) {
  const state = useAsync(() => api.trial(runId, taskId, index), `${runId}/${taskId}/${index}`);
  return (
    <section>
      <nav className="crumbs" aria-label="Breadcrumb">
        <a href={href({ name: "runs" })}>Runs</a> / <a href={href({ name: "run", runId })}>{runId}</a> /{" "}
        <span className="mono">
          {taskId} #{index}
        </span>
      </nav>
      <Loadable state={state}>{(trial) => <TrialDetail trial={trial} taskId={taskId} />}</Loadable>
    </section>
  );
}

export function TrialDetail({ trial, taskId }: { trial: Trial; taskId: string }) {
  const u = trial.usage;
  return (
    <>
      <header className="page-head">
        <h1>
          <span className="mono">{taskId}</span> trial #{trial.index}{" "}
          <Badge status={trial.passed ? "passed" : "failed"}>{trial.passed ? "passed" : "failed"}</Badge>
        </h1>
        <p className="muted">
          stop reason: {trial.stop_reason}
          {trial.error ? ` - ${trial.error}` : ""}
        </p>
      </header>
      <div className="stats">
        <Stat label="Model steps" value={u.steps ?? 0} />
        <Stat label="Tool calls" value={u.tool_calls ?? 0} />
        <Stat label="Tokens" value={compact((u.input_tokens ?? 0) + (u.output_tokens ?? 0))} />
        <Stat label="Cost" value={usd(u.cost_usd ?? 0)} />
        <Stat label="Latency" value={ms(trial.latency_ms)} />
      </div>
      <div className="grid-2">
        <div className="card">
          <h2>Checks</h2>
          <ul className="checks">
            {trial.checks.map((c) => (
              <li key={c.name}>
                <Badge status={c.passed ? "passed" : "failed"}>{c.passed ? "pass" : "fail"}</Badge>{" "}
                <span className="mono">{c.name}</span>
                {c.detail && <div className="muted small detail">{c.detail}</div>}
              </li>
            ))}
          </ul>
        </div>
        <div className="card">
          <h2>Final answer</h2>
          <pre className="answer">{trial.final_answer || "(empty)"}</pre>
        </div>
      </div>
      <div className="card">
        <h2>Trace</h2>
        <Waterfall spans={trial.spans} />
      </div>
    </>
  );
}

export function Waterfall({ spans }: { spans: Span[] }) {
  const { rows, totalMs } = layoutWaterfall(spans);
  const [selected, setSelected] = useState<string | null>(null);
  if (rows.length === 0) return <p className="muted">No spans recorded.</p>;
  const current = rows.find((r) => r.span.span_id === selected)?.span;
  return (
    <div className="waterfall">
      <div className="wf-axis muted small">
        <span>0</span>
        <span>{ms(totalMs)}</span>
      </div>
      <ol className="wf-rows">
        {rows.map(({ span, depth, offsetPct, widthPct }) => (
          <li key={span.span_id}>
            <button
              type="button"
              className={`wf-row ${selected === span.span_id ? "active" : ""}`}
              onClick={() => setSelected(selected === span.span_id ? null : span.span_id)}
              aria-expanded={selected === span.span_id}
            >
              <span className="wf-label" style={{ paddingInlineStart: `${depth * 14}px` }}>
                <span className={`kind kind-${span.kind}`}>{span.kind}</span>
                <span className="mono wf-name">{span.name}</span>
                <span className="muted small">{spanSummary(span)}</span>
              </span>
              <span className="wf-track" aria-hidden="true">
                <span
                  className={`wf-bar kind-bg-${span.kind} ${span.status === "ERROR" ? "wf-error" : ""}`}
                  style={{ insetInlineStart: `${offsetPct}%`, inlineSize: `${widthPct}%` }}
                />
              </span>
              <span className="wf-dur num small">{ms(span.duration_ms)}</span>
            </button>
          </li>
        ))}
      </ol>
      {current && (
        <div className="span-detail">
          <h3 className="mono">{current.name}</h3>
          <table className="attrs">
            <tbody>
              {Object.entries(current.attributes).map(([key, value]) => (
                <tr key={key}>
                  <th scope="row" className="mono small">
                    {key}
                  </th>
                  <td className="mono small">{typeof value === "string" ? value : JSON.stringify(value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
