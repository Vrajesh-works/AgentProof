export function score(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return value.toFixed(3);
}

export function delta(base: number | null | undefined, head: number | null | undefined): string {
  if (base === null || base === undefined || head === null || head === undefined) return "";
  const d = head - base;
  if (Math.abs(d) < 5e-7) return "0";
  return (d > 0 ? "+" : "") + d.toFixed(3);
}

export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

export function ms(value: number): string {
  if (value < 1) return `${value.toFixed(2)} ms`;
  if (value < 1000) return `${value.toFixed(1)} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}

export function compact(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 10_000) return `${Math.round(value / 1000)}k`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
}

export function agentLabel(agent: { name?: unknown; version?: unknown }): string {
  return `${String(agent.name ?? "?")}@${String(agent.version ?? "?")}`;
}

export function when(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
