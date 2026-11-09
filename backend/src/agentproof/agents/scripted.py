"""A deterministic agent for CI and tests.

The scripted agent replays each task's reference solution through the sandbox,
exactly as a model-driven agent would issue calls. With ``fault_rate`` above
zero it injects seeded mistakes (skipping a step, corrupting an argument, an
extra unrequested call, or a wrong final answer), which makes it a stand-in for
a flaky agent: same seed, same faults, so scores are reproducible.

It is a test double for the harness, not a real agent: it reads the reference
solution, which a real agent never sees.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any

from agentproof.agents.base import Agent, AgentOutcome, estimate_tokens
from agentproof.sandbox import AgentContext
from agentproof.suite import ToolCallSpec

FAULT_MODES = ("skip_step", "wrong_args", "extra_call", "bad_answer")


def stable_rng(*parts: object) -> random.Random:
    digest = hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def _corrupt(args: dict[str, Any]) -> dict[str, Any]:
    out = dict(args)
    for key in sorted(out):
        value = out[key]
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            out[key] = round(value * 2 + 1, 2)
            return out
        if isinstance(value, str):
            out[key] = value + "-X"
            return out
    return out


@dataclass
class ScriptedAgent(Agent):
    version: str = "v1"
    seed: int = 0
    fault_rate: float = 0.0
    task_fault_rates: dict[str, float] = field(default_factory=dict)
    fault_modes: tuple[str, ...] = FAULT_MODES
    name: str = "scripted"

    def __post_init__(self) -> None:
        unknown = set(self.fault_modes) - set(FAULT_MODES)
        if unknown:
            raise ValueError(f"unknown fault modes {sorted(unknown)}")
        if not 0 <= self.fault_rate <= 1:
            raise ValueError("fault_rate must be between 0 and 1")

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "seed": self.seed,
            "fault_rate": self.fault_rate,
            "task_fault_rates": dict(self.task_fault_rates),
        }

    def plan(self, ctx: AgentContext) -> tuple[list[ToolCallSpec], str, str | None]:
        task = ctx.task
        rng = stable_rng(self.seed, task.id, ctx.trial_index)
        steps = list(task.reference)
        answer = task.reference_answer or "Done."
        rate = self.task_fault_rates.get(task.id, self.fault_rate)
        if rng.random() >= rate:
            return steps, answer, None
        fault = rng.choice(list(self.fault_modes))
        index = rng.randrange(len(steps)) if steps else 0
        if fault == "skip_step" and steps:
            steps.pop(index)
        elif fault == "wrong_args" and steps:
            steps[index] = ToolCallSpec(tool=steps[index].tool, args=_corrupt(steps[index].args))
        elif fault == "extra_call":
            other = [t.name for t in ctx.tools if t.name not in {s.tool for s in steps}]
            extra = ToolCallSpec(tool=other[0], args={}) if other else (steps[index] if steps else None)
            if extra is not None:
                steps.insert(index, extra)
        else:
            fault = "bad_answer"
            answer = "Sorry, I was not able to complete that request."
        return steps, answer, fault

    async def run(self, ctx: AgentContext) -> AgentOutcome:
        steps, answer, fault = self.plan(ctx)
        history = ctx.system_prompt + "\n" + ctx.instruction
        for step in steps:
            text = json.dumps({"tool": step.tool, "args": step.args}, sort_keys=True)
            with ctx.llm_call(f"scripted-{self.version}") as call:
                call.input_tokens = estimate_tokens(history)
                call.output_tokens = estimate_tokens(text)
                call.stop_reason = "tool_use"
                if fault:
                    call.attributes["agentproof.injected_fault"] = fault
            result = await ctx.call_tool(step.tool, step.args)
            history += "\n" + text + "\n" + result.as_text()
        with ctx.llm_call(f"scripted-{self.version}") as call:
            call.input_tokens = estimate_tokens(history)
            call.output_tokens = estimate_tokens(answer)
            call.stop_reason = "end_turn"
        return AgentOutcome(answer=answer)
