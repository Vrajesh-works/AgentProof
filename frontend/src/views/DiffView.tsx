import { Badge, Loadable, TableRegion } from "../components/common";
import { api } from "../lib/api";
import { agentLabel, delta, score, usd, when } from "../lib/format";
import { href } from "../lib/route";
import type { DiffReport, RunListItem } from "../lib/types";
import { useAsync } from "../lib/useAsync";

const METRICS: [keyof DiffReport["base"]["summary"], string][] = [
  ["pass_hat_k", "pass^k"],
  ["pass_at_k", "pass@k"],
  ["pass_at_1", "pass@1"],
];

export function DiffView({ base, head }: { base?: string; head?: string }) {
  const runs = useAsync(() => api.runs(), "runs");
  return (
    <section>
      <header className="page-head">
        <h1>Version diff</h1>
        <p className="muted">
          Compare two runs of the same suite task by task. This is the same comparison the CI gate makes.
        </p>
      </header>
      <Loadable state={runs}>{(list) => <Picker runs={list} base={base} head={head} />}</Loadable>
      {base && head ? <DiffResult base={base} head={head} /> : null}
    </section>
  );
}

function Picker({ runs, base, head }: { runs: RunListItem[]; base?: string; head?: string }) {
  const go = (next: { base?: string; head?: string }) => {
    window.location.hash = href({ name: "diff", base, head, ...next });
  };
  const option = (r: RunListItem) => (
    <option key={r.id} value={r.id}>
      {r.id} - {r.suite} - {agentLabel(r.agent)} - {when(r.created_at)}
    </option>
  );
  return (
    <form className="picker" onSubmit={(e) => e.preventDefault()}>
      <label>
        Base
        <select value={base ?? ""} onChange={(e) => go({ base: e.target.value })}>
          <option value="">Choose a run</option>
          {runs.map(option)}
        </select>
      </label>
      <label>
        Head
        <select value={head ?? ""} onChange={(e) => go({ head: e.target.value })}>
          <option value="">Choose a run</option>
          {runs.map(option)}
        </select>
      </label>
    </form>
  );
}

function DiffResult({ base, head }: { base: string; head: string }) {
  const state = useAsync(() => api.diff(base, head), `${base}..${head}`);
  return <Loadable state={state}>{(report) => <DiffReportView report={report} />}</Loadable>;
}

export function DiffReportView({ report }: { report: DiffReport }) {
  const order = { regressed: 0, missing: 1, new: 2, improved: 3, unchanged: 4 };
  const tasks = [...report.tasks].sort((a, b) => order[a.status] - order[b.status] || a.task_id.localeCompare(b.task_id));
  return (
    <>
      <div className={`verdict ${report.failed ? "verdict-bad" : "verdict-ok"}`} role="status">
        <strong>{report.failed ? "Gate would fail" : "Gate would pass"}</strong>{" "}
        <span>
          {agentLabel(report.base.agent)} to {agentLabel(report.head.agent)}, tolerance {report.tolerance}
        </span>
      </div>
      <TableRegion label="Suite metrics">
        <table>
          <thead>
            <tr>
              <th scope="col">Metric</th>
              <th scope="col" className="num">Base</th>
              <th scope="col" className="num">Head</th>
              <th scope="col" className="num">Delta</th>
            </tr>
          </thead>
          <tbody>
            {METRICS.map(([key, label]) => {
              const b = report.base.summary[key];
              const h = report.head.summary[key];
              return (
                <tr key={key}>
                  <th scope="row">{label}</th>
                  <td className="num">{score(b)}</td>
                  <td className="num">{score(h)}</td>
                  <td className={`num ${h < b ? "neg" : h > b ? "pos" : ""}`}>{delta(b, h)}</td>
                </tr>
              );
            })}
            <tr>
              <th scope="row">cost</th>
              <td className="num">{usd(report.base.summary.cost_usd)}</td>
              <td className="num">{usd(report.head.summary.cost_usd)}</td>
              <td />
            </tr>
          </tbody>
        </table>
      </TableRegion>
      <TableRegion label="Task changes">
        <table>
          <thead>
            <tr>
              <th scope="col">Task</th>
              <th scope="col">Status</th>
              <th scope="col" className="num">pass^k base</th>
              <th scope="col" className="num">pass^k head</th>
              <th scope="col" className="num">pass@k base</th>
              <th scope="col" className="num">pass@k head</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((t) => (
              <tr key={t.task_id}>
                <th scope="row" className="mono">
                  <a href={href({ name: "run", runId: report.head.id })}>{t.task_id}</a>
                </th>
                <td>
                  <Badge status={t.status} />
                </td>
                <td className="num">{score(t.base?.pass_hat_k)}</td>
                <td className={`num ${t.regressions.includes("pass_hat_k") ? "neg" : ""}`}>{score(t.head?.pass_hat_k)}</td>
                <td className="num">{score(t.base?.pass_at_k)}</td>
                <td className={`num ${t.regressions.includes("pass_at_k") ? "neg" : ""}`}>{score(t.head?.pass_at_k)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableRegion>
    </>
  );
}
