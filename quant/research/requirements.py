"""What would a strategy actually have to deliver to pass a funded challenge?

This is the inverse of a backtest, and it is the more useful direction of enquiry
once your first few candidates have failed. Rather than asking "does this system
pass", it asks "what would *any* system need in order to pass" — so you can judge
a new idea against a target before spending three weeks coding it.

The answer is not data-mined from any particular price history. It comes from the
arithmetic of the rules themselves plus the sequencing risk the Monte Carlo
captures, so it stays valid when the market regime changes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..engine.montecarlo import simulate_challenge
from ..engine.propfirm import PropFirmRules


def synthetic_daily_table(
    expectancy_r: float,
    trades_per_day: float,
    risk_pct: float,
    win_rate: float = 0.45,
    n_days: int = 2000,
    seed: int = 17,
) -> pd.DataFrame:
    """Build a (r_close, r_floor) daily table for a hypothetical strategy.

    R-multiples are drawn from a two-point distribution matched to the requested
    expectancy and win rate: losers are -1R, winners are whatever payoff makes the
    expectancy work. That is deliberately optimistic in one way — real losers
    overshoot their stop — and pessimistic in another — real winners have a tail.
    The intraday floor is modelled as the worst partial sum within the day, which
    is what the daily-loss rule actually measures.
    """
    rng = np.random.default_rng(seed)
    if win_rate <= 0 or win_rate >= 1:
        raise ValueError("win_rate must be strictly between 0 and 1")
    win_payoff = (expectancy_r + (1.0 - win_rate)) / win_rate

    rows = []
    for _ in range(n_days):
        n = rng.poisson(trades_per_day)
        if n == 0:
            rows.append((0.0, 0.0))
            continue
        wins = rng.random(n) < win_rate
        r = np.where(wins, win_payoff, -1.0)
        pnl = r * risk_pct
        cum = np.cumsum(pnl)
        rows.append((float(cum[-1]), float(min(0.0, cum.min()))))
    return pd.DataFrame(rows, columns=["r_close", "r_floor"])


def requirement_grid(
    rules: PropFirmRules | None = None,
    expectancies: tuple[float, ...] = (0.02, 0.05, 0.10, 0.15, 0.20, 0.30),
    risks: tuple[float, ...] = (0.005, 0.0075, 0.01, 0.015, 0.02),
    trades_per_day: float = 1.0,
    win_rate: float = 0.45,
    n_sims: int = 2000,
    max_days: int = 120,
) -> pd.DataFrame:
    """Pass probability as a function of edge (rows) and risk per trade (columns)."""
    rules = rules or PropFirmRules.phase1()
    out = {}
    for risk in risks:
        col = []
        for e in expectancies:
            daily = synthetic_daily_table(e, trades_per_day, risk, win_rate)
            mc = simulate_challenge(daily, rules, n_sims=n_sims, max_days=max_days)
            col.append(mc.pass_rate)
        out[f"risk {risk:.2%}"] = col
    return pd.DataFrame(out, index=[f"exp {e:+.2f}R" for e in expectancies])
