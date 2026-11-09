import { useEffect, useState } from "react";
import { href, parseRoute, type Route } from "./lib/route";
import { DiffView } from "./views/DiffView";
import { RunsView } from "./views/RunsView";
import { RunView } from "./views/RunView";
import { TrialView } from "./views/TrialView";

function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function App() {
  const route = useRoute();
  return (
    <>
      <header className="topbar">
        <a href={href({ name: "runs" })} className="brand">
          <img src="/favicon.svg" alt="" width={22} height={22} />
          AgentProof
        </a>
        <nav aria-label="Main">
          <a href={href({ name: "runs" })} aria-current={route.name === "runs" ? "page" : undefined}>
            Runs
          </a>
          <a href={href({ name: "diff" })} aria-current={route.name === "diff" ? "page" : undefined}>
            Diff
          </a>
        </nav>
      </header>
      <main>
        {route.name === "runs" && <RunsView />}
        {route.name === "run" && <RunView runId={route.runId} />}
        {route.name === "trial" && <TrialView runId={route.runId} taskId={route.taskId} index={route.index} />}
        {route.name === "diff" && <DiffView base={route.base} head={route.head} />}
      </main>
    </>
  );
}
