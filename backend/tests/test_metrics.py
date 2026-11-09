from __future__ import annotations

import pytest

from agentproof.metrics import pass_at_k, pass_hat_k


def test_all_success_and_all_failure():
    assert pass_at_k(4, 4, 4) == 1.0
    assert pass_hat_k(4, 4, 4) == 1.0
    assert pass_at_k(4, 0, 4) == 0.0
    assert pass_hat_k(4, 0, 4) == 0.0


def test_flaky_agent_separates_the_two_metrics():
    # 3 of 4 trials pass: pass@4 says "fine", pass^4 says "not reliable".
    assert pass_at_k(4, 3, 4) == 1.0
    assert pass_hat_k(4, 3, 4) == 0.0


def test_hand_computed_values():
    # n=5, c=3, k=2: pass^2 = C(3,2)/C(5,2) = 3/10; pass@2 = 1 - C(2,2)/C(5,2) = 9/10
    assert pass_hat_k(5, 3, 2) == pytest.approx(0.3)
    assert pass_at_k(5, 3, 2) == pytest.approx(0.9)
    # k=1 reduces to the plain success rate for both
    assert pass_at_k(8, 6, 1) == pytest.approx(0.75)
    assert pass_hat_k(8, 6, 1) == pytest.approx(0.75)


@pytest.mark.parametrize("n,c,k", [(0, 0, 1), (3, 4, 1), (3, 1, 4), (3, 1, 0)])
def test_invalid_inputs(n, c, k):
    with pytest.raises(ValueError):
        pass_at_k(n, c, k)
