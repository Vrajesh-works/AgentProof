"""Reliability metrics over repeated trials.

With ``n`` trials of a task of which ``c`` succeeded:

- ``pass@k`` (Chen et al., 2021) is the probability that at least one of ``k``
  trials drawn without replacement succeeds: ``1 - C(n-c, k) / C(n, k)``.
- ``pass^k`` (Yao et al., tau-bench, 2024) is the probability that all ``k``
  drawn trials succeed: ``C(c, k) / C(n, k)``.

pass@k rewards an agent that sometimes gets it right; pass^k rewards one that
always does, which is what a production agent needs. Their gap is flakiness.
"""

from __future__ import annotations

from math import comb


def _validate(n: int, c: int, k: int) -> None:
    if n < 1:
        raise ValueError("n must be at least 1")
    if not 0 <= c <= n:
        raise ValueError("c must be between 0 and n")
    if not 1 <= k <= n:
        raise ValueError("k must be between 1 and n")


def pass_at_k(n: int, c: int, k: int) -> float:
    _validate(n, c, k)
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    _validate(n, c, k)
    return comb(c, k) / comb(n, k)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
