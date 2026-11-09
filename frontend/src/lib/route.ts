// Minimal hash router: the viewer is a static bundle, so hash routes work behind any server.

export type Route =
  | { name: "runs" }
  | { name: "run"; runId: string }
  | { name: "trial"; runId: string; taskId: string; index: number }
  | { name: "diff"; base?: string; head?: string };

export function parseRoute(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#/, "").split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const params = new URLSearchParams(query);
  if (parts[0] === "runs" && parts[1]) {
    if (parts[2] === "tasks" && parts[3] && parts[4] === "trials" && parts[5] !== undefined) {
      const index = Number(parts[5]);
      if (Number.isInteger(index) && index >= 0) return { name: "trial", runId: parts[1], taskId: parts[3], index };
    }
    return { name: "run", runId: parts[1] };
  }
  if (parts[0] === "diff") {
    return { name: "diff", base: params.get("base") ?? undefined, head: params.get("head") ?? undefined };
  }
  return { name: "runs" };
}

export function href(route: Route): string {
  switch (route.name) {
    case "runs":
      return "#/";
    case "run":
      return `#/runs/${encodeURIComponent(route.runId)}`;
    case "trial":
      return `#/runs/${encodeURIComponent(route.runId)}/tasks/${encodeURIComponent(route.taskId)}/trials/${route.index}`;
    case "diff": {
      const params = new URLSearchParams();
      if (route.base) params.set("base", route.base);
      if (route.head) params.set("head", route.head);
      const qs = params.toString();
      return `#/diff${qs ? `?${qs}` : ""}`;
    }
  }
}
