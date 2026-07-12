"""Statistical inference for the eval pipeline — the honest-numbers layer.

An eval rate like 0.8 from a handful of tasks is a point estimate with real
uncertainty; treating it as exact makes small-sample gates and A/B verdicts
noise-driven. This module turns counts into calibrated statements, using only
the standard library (no numpy/scipy — a few closed forms and one continued
fraction):

  wilson         binomial rate → confidence interval (accurate for small n)
  underpowered   is the sample too small / interval too wide to trust?
  mcnemar_exact  paired A/B (same tasks) → exact p on the tasks that changed
  beta_binom_gt  drift → posterior P(true error rate exceeds a threshold)
  vote           k judge draws → majority + agreement

Each is a pure function. `python -m any2agent.evals.stats` runs reference-value
self-checks.
"""
from __future__ import annotations

from math import comb, sqrt, lgamma, exp, log
from typing import List, Tuple


def wilson(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion k/n. Stays within [0,1]
    and is accurate near 0/1 where the textbook Wald interval breaks. n=0 →
    total ignorance (0,1)."""
    if n <= 0:
        return (0.0, 1.0)
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (k / n + z2 / (2 * n)) / denom
    half = (z / denom) * sqrt((k / n) * (1 - k / n) / n + z2 / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def underpowered(k: int, n: int, min_n: int = 5, max_hw: float = 0.15) -> bool:
    """True when the sample can't support a trustworthy gate: fewer than
    `min_n` graded tasks, or a Wilson half-width wider than `max_hw`."""
    if n < min_n:
        return True
    lo, hi = wilson(k, n)
    return (hi - lo) / 2 > max_hw


def tasks_needed(k: int, n: int, min_n: int = 5, max_hw: float = 0.15) -> int:
    """How many more graded tasks (at the current success ratio) would bring
    the interval within `max_hw` — the concrete 'add N tasks' advice."""
    if n <= 0:
        return min_n
    p = k / n
    m = max(n, min_n)
    while m < 500:
        lo, hi = wilson(round(p * m), m)
        if m >= min_n and (hi - lo) / 2 <= max_hw:
            return m - n
        m += 1
    return m - n


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for paired binary outcomes. b and c are
    the DISCORDANT counts (one side passed where the other failed); concordant
    pairs carry no information and are excluded. b+c=0 → no change (p=1)."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(0, min(b, c) + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def _betacf(a: float, bb: float, x: float, itmax: int = 200, eps: float = 1e-12) -> float:
    """Continued fraction for the incomplete beta (Numerical Recipes `betacf`).
    ponytail: caps at `itmax` iterations; for the a,b,x we feed it (small
    integer counts) this converges in well under that."""
    qab, qap, qam = a + bb, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (bb - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a: float, bb: float, x: float) -> float:
    """Regularized incomplete beta I_x(a,b) ∈ [0,1]. stdlib only."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = lgamma(a + bb) - lgamma(a) - lgamma(bb)
    front = exp(lbeta + a * log(x) + bb * log(1.0 - x))
    if x < (a + 1.0) / (a + bb + 2.0):
        return front * _betacf(a, bb, x) / a
    return 1.0 - front * _betacf(bb, a, 1.0 - x) / bb


def beta_binom_gt(errors: int, n: int, p0: float = 0.5,
                  prior: Tuple[float, float] = (1.0, 1.0)) -> float:
    """Posterior P(true error rate > p0) after observing `errors` failures in
    `n` calls, with a Beta(prior) belief (default uniform). This is the
    calibrated 'how sure are we the tool degraded' behind the drift flag."""
    if n <= 0:
        return 0.0
    a = prior[0] + errors
    bb = prior[1] + (n - errors)
    return 1.0 - _betai(a, bb, p0)   # P(p > p0) = 1 - CDF(p0)


def vote(passes: List[bool]) -> Tuple[bool, float]:
    """Majority vote over k judge draws + agreement (fraction agreeing with the
    majority). One draw → (that draw, 1.0). Ties (even k) → fail (conservative)."""
    if not passes:
        return (False, 0.0)
    yes = sum(1 for p in passes if p)
    n = len(passes)
    majority = yes > n / 2
    agreement = max(yes, n - yes) / n
    return (majority, agreement)


def _selfcheck() -> None:
    lo, hi = wilson(8, 10)
    assert 0.47 < lo < 0.51 and 0.92 < hi < 0.95, (lo, hi)          # ref ≈ [0.49,0.94]
    assert wilson(0, 0) == (0.0, 1.0)
    assert underpowered(3, 3) and underpowered(9, 10)   # even 10 tasks @0.9 is wide (~±0.19)
    assert not underpowered(45, 50)                       # 50 tasks: half-width < 0.15
    assert mcnemar_exact(0, 0) == 1.0
    assert abs(mcnemar_exact(0, 3) - 0.25) < 1e-9                    # 2·(1/8)
    assert mcnemar_exact(5, 5) > 0.9                                 # symmetric → no signal
    # 6/10 errors, uniform prior: posterior P(p>0.5) should be moderately > 0.5
    g = beta_binom_gt(6, 10, 0.5)
    assert 0.6 < g < 0.85, g
    assert beta_binom_gt(9, 10, 0.5) > 0.97
    assert vote([True, True, False]) == (True, 2 / 3)
    assert vote([True, False]) == (False, 0.5)                       # tie → fail
    assert tasks_needed(4, 5) > 0
    print("stats self-check OK")


if __name__ == "__main__":
    _selfcheck()
