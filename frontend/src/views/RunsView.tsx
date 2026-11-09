import { useMemo, useState } from "react";
import { Loadable, ScoreBar, TableRegion } from "../components/common";
import { api } from "../lib/api";
import { agentLabel, usd, when } from "../lib/format";
import { href } from "../lib/route";
import type { RunListItem } from "../lib/types";
import { useAsync } from "../lib/useAsync";

export function RunsView() {
  const state = useAsync(() => api.runs(), "runs");
  return (
    <section>
      <header className="page-head">
        <h1>Runs</h1>
        <p className="muted">
          Every suite run, newest first. pass@k counts a task if any of k trials succeeded; pass^k only if all of them
          did. A gap between the two is flakiness.
        </p>
      </header>
      <Loadable state={state}>{(runs) => <RunsTable runs={runs} />}</Loadable>
    </section>
  );
}

export function RunsTable({ runs }: { runs: RunListItem[] }) {
  const [selected, setSelected] = useState<string[]>([]);
  const bySuite = useMemo(() => new Map(runs.map((r) => [r.id, r.suite])), [runs]);

  if (runs.length === 0) {
    return (
      <div className="empty">
        <p>No runs yet.</p>
        <p className="muted">
          Run <code>agentproof run suites/retail.yaml --store</code> or start the stack with{" "}
          <code>AGENTPROOF_SEED_DEMO=1</code>.
        </p>
      </div>
    );
  }

  const toggle = (id: string) =>
    setSelected((current) => {
      if (current.includes(id)) return current.filter((x) => x !== id);
      const next = [...current, id].slice(-2);
      return next;
    });
  const [base, head] = selected;
  const sameSuite = base && head && bySuite.get(base) === bySuite.get(head);

  return (
    <>
      <div className="toolbar">
        <span className="muted">
          {selected.length === 2
            ? sameSuite
              ? "Compare the two selected runs (first selected is the base)."
              : "Pick two runs of the same suite to compare."
            : "Select two runs to compare them."}
        </span>
        <a
          className={`button ${sameSuite ? "" : "disabled"}`}
          aria-disabled={!sameSuite}
          href={sameSuite ? href({ name: "diff", base, head }) : undefined}
        >
          Compare
        </a>
      </div>
      <TableRegion label="Runs">
        <table>
          <thead>
            <tr>
              <th scope="col">
                <span className="visually-hidden">Select</span>
              </th>
              <th scope="col">Run</th>
              <th scope="col">Suite</th>
              <th scope="col">Agent</th>
              <th scope="col">Label</th>
              <th scope="col" className="num">pass@1</th>
              <th scope="col">pass@k</th>
              <th scope="col">pass^k</th>
              <th scope="col" className="num">Cost</th>
              <th scope="col">When</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} className={selected.includes(run.id) ? "selected" : undefined}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.includes(run.id)}
                    onChange={() => toggle(run.id)}
                    aria-label={`Select run ${run.id}`}
                  />
                </td>
                <th scope="row">
                  <a href={href({ name: "run", runId: run.id })} className="mono">
                    {run.id}
                  </a>
                </th>
                <td>{run.suite}</td>
                <td className="mono">{agentLabel(run.agent)}</td>
                <td>{run.label ?? <span className="muted">-</span>}</td>
                <td className="num">{run.summary.pass_at_1.toFixed(3)}</td>
                <td>
                  <ScoreBar value={run.summary.pass_at_k} />
                </td>
                <td>
                  <ScoreBar value={run.summary.pass_hat_k} />
                </td>
                <td className="num">{usd(run.summary.cost_usd)}</td>
                <td className="nowrap">{when(run.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableRegion>
    </>
  );
}
