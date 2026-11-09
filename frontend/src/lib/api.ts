import type { DiffReport, Run, RunListItem, Trial } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // keep statusText
    }
    throw new ApiError(response.status, detail || `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export const api = {
  runs: (suite?: string) => get<RunListItem[]>(suite ? `/api/runs?suite=${encodeURIComponent(suite)}` : "/api/runs"),
  run: (id: string) => get<Run>(`/api/runs/${encodeURIComponent(id)}`),
  trial: (runId: string, taskId: string, index: number) =>
    get<Trial>(
      `/api/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/trials/${index}`,
    ),
  diff: (base: string, head: string, tolerance = 0) =>
    get<DiffReport>(
      `/api/diff?base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}&tolerance=${tolerance}`,
    ),
};
