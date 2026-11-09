import type { ReactNode } from "react";
import { href } from "../lib/route";
import type { Trial } from "../lib/types";
import type { AsyncState } from "../lib/useAsync";

export function Loadable<T>({ state, children }: { state: AsyncState<T>; children: (data: T) => ReactNode }) {
  if (state.status === "loading") return <p className="muted" role="status">Loading...</p>;
  if (state.status === "error") return <p className="error" role="alert">Could not load: {state.error}</p>;
  return <>{children(state.data)}</>;
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

const STATUS_CLASS: Record<string, string> = {
  passed: "ok",
  improved: "ok",
  unchanged: "neutral",
  new: "info",
  failed: "bad",
  regressed: "bad",
  missing: "warn",
};

export function Badge({ status, children }: { status: string; children?: ReactNode }) {
  return <span className={`badge badge-${STATUS_CLASS[status] ?? "neutral"}`}>{children ?? status}</span>;
}

/** A bar showing a 0..1 score, plus the number. */
export function ScoreBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const tone = value >= 0.999 ? "ok" : value >= 0.5 ? "warn" : "bad";
  return (
    <span className="score">
      <span className="score-track" aria-hidden="true">
        <span className={`score-fill score-${tone}`} style={{ inlineSize: `${pct}%` }} />
      </span>
      <span className="num">{value.toFixed(3)}</span>
    </span>
  );
}

export function TrialDots({ runId, taskId, trials }: { runId: string; taskId: string; trials: Trial[] }) {
  return (
    <span className="dots">
      {trials.map((t) => (
        <a
          key={t.index}
          href={href({ name: "trial", runId, taskId, index: t.index })}
          className={`dot ${t.passed ? "dot-ok" : "dot-bad"}`}
          title={`trial #${t.index}: ${t.passed ? "passed" : `failed (${t.stop_reason})`}`}
          aria-label={`Trial ${t.index} ${t.passed ? "passed" : "failed"}`}
        />
      ))}
    </span>
  );
}

export function TableRegion({ label, children }: { label: string; children: ReactNode }) {
  // Scrollable, keyboard-focusable region so wide tables never cause page-level horizontal scroll.
  return (
    <div className="table-wrapper" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}
