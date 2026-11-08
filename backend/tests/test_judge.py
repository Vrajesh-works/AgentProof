from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentproof.cli import main
from agentproof.judge import KeywordJudge, LLMJudge, agreement, build_judge, cohens_kappa
from agentproof.suite import JudgeSpec
from tests.conftest import SUITES

SPEC = JudgeSpec(rubric="mention a human", must_mention=["human"], must_not_mention=["refunded"])


def grade(judge, answer):
    return asyncio.run(judge.grade("q", answer, SPEC))


def test_keyword_judge_uses_word_boundaries():
    judge = KeywordJudge()
    assert grade(judge, "I transferred you to a human agent.").passed
    assert not grade(judge, "I transferred you to humanity.").passed
    verdict = grade(judge, "A human refunded you.")
    assert not verdict.passed and "forbidden" in verdict.rationale


def test_cohens_kappa():
    assert cohens_kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert cohens_kappa([True, True, False, False], [True, False, True, False]) == pytest.approx(0.0)
    assert cohens_kappa([True, True], [True, True]) == 1.0
    with pytest.raises(ValueError):
        cohens_kappa([], [])


def test_agreement_against_shipped_hand_labels():
    report = asyncio.run(agreement(KeywordJudge(), SUITES / "judge_labels.yaml"))
    assert report.n >= 10
    assert report.accuracy >= 0.75
    # The keyword judge is not perfect; the report must show where it disagrees.
    assert len(report.disagreements) == report.confusion["fp"] + report.confusion["fn"]


def test_cli_judge_agreement(capsys):
    assert main(["judge-agreement", str(SUITES / "judge_labels.yaml")]) == 0
    assert '"kappa"' in capsys.readouterr().out
    assert main(["judge-agreement", str(SUITES / "judge_labels.yaml"), "--min-kappa", "1.01"]) == 1


def test_llm_judge_parses_json_verdict():
    class Client:
        def __init__(self, text):
            self.text = text
            self.messages = SimpleNamespace(create=self.create)

        async def create(self, **kwargs):
            return SimpleNamespace(
                stop_reason="end_turn",
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                content=[SimpleNamespace(type="text", text=self.text)],
            )

    judge = LLMJudge(
        "claude", model="claude-sonnet-5-5", client=Client('Verdict: {"pass": true, "rationale": "ok"}')
    )
    assert grade(judge, "anything").passed
    judge = LLMJudge("claude", model="claude-sonnet-5-5", client=Client("no json here"))
    assert not grade(judge, "anything").passed


def test_build_judge():
    assert build_judge("keyword").name == "keyword"
    with pytest.raises(ValueError):
        build_judge("magic")
