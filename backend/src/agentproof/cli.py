"""Command line: run suites, gate PRs, serve the API, check the judge."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from agentproof.results import RunResult


def _git_sha() -> str | None:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def cmd_run(args: argparse.Namespace) -> int:
    from agentproof.agents import load_agent
    from agentproof.judge import build_judge
    from agentproof.runner import RunOptions, run_suite
    from agentproof.suite import load_suite

    suite = load_suite(args.suite)
    agent = load_agent(args.agent)

    def progress(task_id: str, trial) -> None:
        if not args.quiet:
            mark = "PASS" if trial.passed else "FAIL"
            failed = [c["name"] for c in trial.checks if not c["passed"]]
            extra = f" ({', '.join(failed)})" if failed else ""
            print(f"  {mark} {task_id} #{trial.index}{extra}", file=sys.stderr)

    options = RunOptions(
        k=args.k,
        n=args.n,
        seed=args.seed,
        tasks=args.tasks.split(",") if args.tasks else None,
        judge=build_judge(args.judge),
        label=args.label,
        git_sha=_git_sha(),
        on_trial=progress,
    )
    result = asyncio.run(run_suite(suite, agent, options))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        result.save(args.out)
    if args.store or os.environ.get("AGENTPROOF_STORE") == "1":
        from agentproof.db import Database

        Database.from_env().save_run(result)
    s = result.summary
    print(
        f"{result.suite}: {s.tasks} tasks x {result.n} trials | pass@1 {s.pass_at_1:.3f} | "
        f"pass@{result.k} {s.pass_at_k:.3f} | pass^{result.k} {s.pass_hat_k:.3f} | "
        f"tokens {s.input_tokens + s.output_tokens} | cost ${s.cost_usd:.4f}"
    )
    if args.min_pass_hat_k is not None and s.pass_hat_k < args.min_pass_hat_k:
        print(f"pass^k {s.pass_hat_k:.3f} is below the required {args.min_pass_hat_k:.3f}", file=sys.stderr)
        return 1
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    from agentproof.gate import compare

    base = RunResult.load(args.base)
    head = RunResult.load(args.head)
    report = compare(
        base,
        head,
        metrics=tuple(args.metrics.split(",")),
        tolerance=args.tolerance,
        fail_on_missing=not args.allow_missing,
    )
    markdown = report.markdown()
    print(markdown)
    if args.markdown:
        Path(args.markdown).write_text(markdown, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")
    return 1 if report.failed else 0


def cmd_validate(args: argparse.Namespace) -> int:
    from agentproof.suite import SuiteError, load_suite

    status = 0
    for path in args.suites:
        try:
            suite = load_suite(path)
        except SuiteError as exc:
            print(f"INVALID {path}: {exc}")
            status = 1
        else:
            print(
                f"ok      {path}: suite {suite.suite!r}, {len(suite.tasks)} tasks, env {suite.environment.type}"
            )
    return status


def cmd_judge_agreement(args: argparse.Namespace) -> int:
    from agentproof.judge import agreement, build_judge

    report = asyncio.run(agreement(build_judge(args.judge), args.labels))
    print(json.dumps(report.to_dict(), indent=2))
    if args.min_kappa is not None and report.kappa < args.min_kappa:
        return 1
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from agentproof.db import Database

    db = Database.from_env()
    for path in args.files:
        run = RunResult.load(path)
        db.save_run(run)
        print(f"imported {run.id} ({run.suite}) from {path}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("agentproof.api:app", host=args.host, port=args.port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentproof", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a suite and write a results JSON")
    run.add_argument("suite")
    run.add_argument(
        "--agent", default="scripted", help="agent YAML path or spec like scripted:fault_rate=0.2"
    )
    run.add_argument("--k", type=int, help="k for pass@k / pass^k (default: suite k)")
    run.add_argument("--n", type=int, help="trials per task (default: k)")
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--tasks", help="comma-separated task ids to run")
    run.add_argument("--judge", default="keyword", help="keyword, claude or openai")
    run.add_argument("--label", help="free-form label stored with the run, e.g. a PR number")
    run.add_argument("--out", help="write the RunResult JSON here")
    run.add_argument("--store", action="store_true", help="also save to DATABASE_URL")
    run.add_argument("--min-pass-hat-k", type=float, help="exit 1 if suite pass^k is below this")
    run.add_argument("--quiet", action="store_true")
    run.set_defaults(func=cmd_run)

    gate = sub.add_parser("gate", help="fail when head regresses against base")
    gate.add_argument("--base", required=True)
    gate.add_argument("--head", required=True)
    gate.add_argument("--tolerance", type=float, default=0.0)
    gate.add_argument("--metrics", default="pass_hat_k,pass_at_k")
    gate.add_argument(
        "--allow-missing", action="store_true", help="do not fail when a base task is missing in head"
    )
    gate.add_argument("--markdown", help="write the markdown report here")
    gate.add_argument("--json", help="write the JSON report here")
    gate.set_defaults(func=cmd_gate)

    validate = sub.add_parser("validate", help="validate suite files")
    validate.add_argument("suites", nargs="+")
    validate.set_defaults(func=cmd_validate)

    judge = sub.add_parser("judge-agreement", help="measure a judge against hand labels")
    judge.add_argument("labels")
    judge.add_argument("--judge", default="keyword")
    judge.add_argument("--min-kappa", type=float)
    judge.set_defaults(func=cmd_judge_agreement)

    imp = sub.add_parser("import", help="load results JSON files into DATABASE_URL")
    imp.add_argument("files", nargs="+")
    imp.set_defaults(func=cmd_import)

    serve = sub.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
