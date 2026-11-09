import { Badge, Loadable, ScoreBar, Stat, TableRegion, TrialDots } from "../components/common";
import { api } from "../lib/api";
import { agentLabel, compact, ms, usd, when } from "../lib/format";
import { href } from "../lib/route";
import type { Run } from "../lib/types";
import { useAsync } from "../lib/useAsync";

export function RunView({ runId }: { runId: string }) {
  const state = useAsync(() => api.run(runId), runId);
  return <Loadable state={state}>{(run) => <RunDetail run={run} />}</Loadable>;
}

export function RunDetail({ run }: { run: Run }) {
  const s = run.summary;
  const flaky = run.tasks.filter((t) => t.successes > 0 && t.successes < t.n).length;
  return (
    <section>
      <nav className="crumbs" aria-label="Breadcrumb">
        <a href={href({ name: "runs" })}>Runs</a> / <span className="mono">{run.id}</span>
      </nav>
      <header className="page-head">
        <h1>
          {run.suite} <span className="muted">with</span> <span className="mono">{agentLabel(run.agent)}</span>
        </h1>
        <p className="muted">
          {when(run.created_at)} - environment {run.environment} - k={run.k}, {run.n} trials per task, seed {run.seed}
          {run.label ? ` - ${run.label}` : ""}
          {run.git_sha ? ` - ${run.git_sha.slice(0, 8)}` : ""}
        </p>
      </header>
      <div className="stats">
        <Stat label={`pass^${run.k}`} value={s.pass_hat_k.toFixed(3)} hint="all k trials pass" />
        <Stat label={`pass@${run.k}`} value={s.pass_at_k.toFixed(3)} hint="any trial passes" />
        <Stat label="pass@1" value={s.pass_at_1.toFixed(3)} hint="mean success rate" />
        <Stat label="Flaky tasks" value={flaky} hint="some trials pass, some fail" />
        <Stat label="Tokens" value={compact(s.input_tokens + s.output_tokens)} hint={`${compact(s.input_tokens)} in`} />
        <Stat label="Cost" value={usd(s.cost_usd)} hint={`mean trial ${ms(s.mean_latency_ms)}`} />
      </div>
      <TableRegion label="Tasks">
        <table>
          <thead>
            <tr>
              <th scope="col">Task</th>
              <th scope="col">Trials</th>
              <th scope="col" className="num">Passed</th>
              <th scope="col">pass@k</th>
              <th scope="col">pass^k</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {run.tasks.map((task) => {
              const status = task.successes === task.n ? "passed" : task.successes === 0 ? "failed" : "flaky";
              return (
                <tr key={task.task_id}>
                  <th scope="row">
                    <div className="mono">{task.task_id}</div>
                    {task.description && <div className="muted small">{task.description}</div>}
                  </th>
                  <td>
                    <TrialDots runId={run.id} taskId={task.task_id} trials={task.trials} />
                  </td>
                  <td className="num">
                    {task.successes}/{task.n}
                  </td>
                  <td>
                    <ScoreBar value={task.pass_at_k} />
                  </td>
                  <td>
                    <ScoreBar value={task.pass_hat_k} />
                  </td>
                  <td>
                    <Badge status={status === "flaky" ? "missing" : status}>{status}</Badge>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableRegion>
    </section>
  );
}
