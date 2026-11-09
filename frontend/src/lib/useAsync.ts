import { useEffect, useState } from "react";

export type AsyncState<T> = { status: "loading" } | { status: "error"; error: string } | { status: "ok"; data: T };

/** Run an async loader whenever its dependency key changes; ignores results from stale calls. */
export function useAsync<T>(load: () => Promise<T>, key: string): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({ status: "loading" });
  useEffect(() => {
    let active = true;
    setState({ status: "loading" });
    load().then(
      (data) => active && setState({ status: "ok", data }),
      (error: unknown) => active && setState({ status: "error", error: error instanceof Error ? error.message : String(error) }),
    );
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return state;
}
