"""Judges for open-ended answers, and how much to trust them.

Deterministic checkers decide everything they can. A judge is only consulted
when a task's ``expected.judge`` block asks for one, for answers whose quality
cannot be pinned to state or a pattern (for example "explain the refund
policy"). Judges are pluggable:

- ``KeywordJudge``: deterministic, uses the rubric's must/must-not mention lists.
- ``LLMJudge``: asks a model to grade against the rubric text.

``agreement`` measures a judge against hand labels (accuracy and Cohen's kappa),
so a judge is only trusted once it agrees with people.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from agentproof.suite import JudgeSpec


@dataclass
class Verdict:
    passed: bool
    rationale: str


class Judge(Protocol):
    name: str

    async def grade(self, question: str, answer: str, spec: JudgeSpec) -> Verdict: ...


def _mentions(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(phrase.lower()) + r"(?!\w)", text.lower()) is not None


class KeywordJudge:
    name = "keyword"

    async def grade(self, question: str, answer: str, spec: JudgeSpec) -> Verdict:
        missing = [p for p in spec.must_mention if not _mentions(answer, p)]
        banned = [p for p in spec.must_not_mention if _mentions(answer, p)]
        if missing or banned:
            parts = []
            if missing:
                parts.append(f"missing {missing}")
            if banned:
                parts.append(f"mentions forbidden {banned}")
            return Verdict(False, "; ".join(parts))
        return Verdict(True, "all required points present")


JUDGE_PROMPT = """You grade a customer-operations agent's final answer against a rubric.

Question given to the agent:
{question}

Agent's final answer:
{answer}

Rubric:
{rubric}

Reply with only a JSON object: {{"pass": true or false, "rationale": "one sentence"}}"""


class LLMJudge:
    """Grades with a model through the same provider sessions the agents use."""

    def __init__(self, provider: str = "claude", model: str | None = None, client: Any = None):
        from agentproof.agents.llm import AnthropicSession, OpenAISession

        self.name = f"llm:{provider}"
        if provider == "claude":
            self.session_factory = lambda: AnthropicSession(
                model or os.environ.get("AGENTPROOF_JUDGE_MODEL", "claude-sonnet-5-5"),
                client=client,
                max_tokens=2000,
            )
        elif provider == "openai":
            self.session_factory = lambda: OpenAISession(
                model or os.environ.get("AGENTPROOF_JUDGE_MODEL", "gpt-4.1-mini"), client=client
            )
        else:
            raise ValueError(f"unknown judge provider {provider!r}")

    async def grade(self, question: str, answer: str, spec: JudgeSpec) -> Verdict:
        session = self.session_factory()
        prompt = JUDGE_PROMPT.format(question=question, answer=answer, rubric=spec.rubric)
        session.start("You are a strict, consistent grader.", prompt, [])
        turn = await session.step()
        match = re.search(r"\{.*\}", turn.text, re.S)
        if not match:
            return Verdict(False, f"judge returned no JSON: {turn.text[:200]!r}")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return Verdict(False, f"judge returned invalid JSON: {match.group(0)[:200]!r}")
        return Verdict(bool(data.get("pass")), str(data.get("rationale", "")))


def build_judge(name: str) -> Judge:
    if name == "keyword":
        return KeywordJudge()
    if name in {"claude", "openai"}:
        return LLMJudge(provider=name)
    raise ValueError(f"unknown judge {name!r}; use keyword, claude or openai")


@dataclass
class AgreementReport:
    judge: str
    n: int
    accuracy: float
    kappa: float
    confusion: dict[str, int]  # tp, tn, fp, fn with "positive" meaning pass
    disagreements: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "judge": self.judge,
            "n": self.n,
            "accuracy": round(self.accuracy, 4),
            "kappa": round(self.kappa, 4),
            "confusion": self.confusion,
            "disagreements": self.disagreements,
        }


def cohens_kappa(human: list[bool], judge: list[bool]) -> float:
    n = len(human)
    if n == 0 or n != len(judge):
        raise ValueError("label lists must be non-empty and the same length")
    observed = sum(h == j for h, j in zip(human, judge, strict=True)) / n
    p_h = sum(human) / n
    p_j = sum(judge) / n
    expected = p_h * p_j + (1 - p_h) * (1 - p_j)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1 - expected)


async def agreement(judge: Judge, labels_path: str | Path) -> AgreementReport:
    """Score a judge against a YAML file of hand-labelled answers."""
    data = yaml.safe_load(Path(labels_path).read_text(encoding="utf-8"))
    items = data["items"] if isinstance(data, dict) else data
    human: list[bool] = []
    predicted: list[bool] = []
    disagreements = []
    for item in items:
        spec = JudgeSpec.model_validate(item["judge"])
        verdict = await judge.grade(item["question"], item["answer"], spec)
        label = item["label"] == "pass"
        human.append(label)
        predicted.append(verdict.passed)
        if verdict.passed != label:
            disagreements.append(
                {"id": item.get("id"), "human": item["label"], "judge_rationale": verdict.rationale}
            )
    confusion = {
        "tp": sum(h and p for h, p in zip(human, predicted, strict=True)),
        "tn": sum((not h) and (not p) for h, p in zip(human, predicted, strict=True)),
        "fp": sum((not h) and p for h, p in zip(human, predicted, strict=True)),
        "fn": sum(h and (not p) for h, p in zip(human, predicted, strict=True)),
    }
    accuracy = (confusion["tp"] + confusion["tn"]) / len(human)
    return AgreementReport(
        judge.name, len(human), accuracy, cohens_kappa(human, predicted), confusion, disagreements
    )
