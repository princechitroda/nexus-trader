"""Monte Carlo challenge simulation.

A backtest gives you exactly one ordering of your trades. You will not get that
ordering. The question that actually matters for a funded account is:

    "Across all the plausible orderings of this edge, what fraction reach the
     profit target before touching a drawdown tripwire?"

That number is usually far lower than people expect. A system with a 1.4 profit
factor and a clean-looking backtest equity curve can easily sit at a 45% pass
probability, because the daily-loss rule truncates the left tail hard.

Method: resample whole *trading days* — the pair (close-to-close return, worst
intraday floor) — with a **stationary block bootstrap**. Blocks matter: losing
days cluster in real markets, and an i.i.d. resample understates the odds of the
consecutive-loss run that actually breaches you.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .propfirm import Outcome, PropFirmRules


@dataclass
class MonteCarloResult:
    n_sims: int
    pass_rate: float
    breach_daily_rate: float
    breach_max_rate: float
    timeout_rate: float
    median_days_to_pass: float
    p10_final_equity: float
    median_final_equity: float
    p90_final_equity: float
    risk_of_ruin: float
    outcomes: dict = field(default_factory=dict)

    def report(self) -> str:
        return (
            f"pass {self.pass_rate:5.1%} | "
            f"daily-breach {self.breach_daily_rate:5.1%} | "
            f"max-breach {self.breach_max_rate:5.1%} | "
            f"timeout {self.timeout_rate:5.1%} | "
            f"median days to pass {self.median_days_to_pass:.0f}"
        )


def _block_bootstrap_indices(
    n_source: int, n_draw: int, mean_block: float, rng: np.random.Generator
) -> np.ndarray:
    """Stationary bootstrap (Politis & Romano): geometric block lengths, wrapping."""
    if n_source == 0:
        return np.empty(0, dtype=int)
    p = 1.0 / max(mean_block, 1.0)
    out = np.empty(n_draw, dtype=int)
    pos = rng.integers(0, n_source)
    for k in range(n_draw):
        out[k] = pos
        if rng.random() < p:
            pos = rng.integers(0, n_source)
        else:
            pos = (pos + 1) % n_source
    return out


def simulate_challenge(
    daily: pd.DataFrame,
    rules: PropFirmRules,
    n_sims: int = 5_000,
    max_days: int = 120,
    mean_block: float = 5.0,
    risk_multiplier: float = 1.0,
    seed: int = 7,
) -> MonteCarloResult:
    """`daily` is the frame from `propfirm.daily_returns_table` (r_close, r_floor).

    `risk_multiplier` scales both returns linearly, approximating "what if I risked
    k times as much per trade". The approximation is good for the small risk levels
    that matter here (<=1.5% per trade) and degrades at larger sizes where
    compounding within the day stops being negligible.
    """
    if daily.empty:
        return MonteCarloResult(0, 0.0, 0.0, 0.0, 1.0, float("nan"), 0.0, 0.0, 0.0, 1.0)

    rng = np.random.default_rng(seed)
    r_close = daily["r_close"].to_numpy(float) * risk_multiplier
    r_floor = np.minimum(daily["r_floor"].to_numpy(float) * risk_multiplier, 0.0)
    n_src = len(r_close)

    target_mult = 1.0 + rules.profit_target_pct
    daily_lim = rules.daily_loss_pct
    max_lim = rules.max_loss_pct

    counts = {o: 0 for o in Outcome}
    days_to_pass: list[int] = []
    finals = np.empty(n_sims)

    for s in range(n_sims):
        draw = _block_bootstrap_indices(n_src, max_days, mean_block, rng)
        equity = 1.0
        hwm = 1.0
        traded_days = 0
        outcome = Outcome.TIMEOUT

        for d in range(max_days):
            j = draw[d]
            day_start = equity
            floor_eq = day_start * (1.0 + r_floor[j])

            if (day_start - floor_eq) / day_start >= daily_lim:
                outcome = Outcome.BREACH_DAILY
                equity = day_start * (1.0 - daily_lim)
                break

            ref = hwm if rules.trailing_max_loss else 1.0
            if (ref - floor_eq) / ref >= max_lim:
                outcome = Outcome.BREACH_MAX
                equity = ref * (1.0 - max_lim)
                break

            equity = day_start * (1.0 + r_close[j])
            hwm = max(hwm, equity)
            if abs(r_close[j]) > 1e-12:
                traded_days += 1

            if equity >= target_mult and traded_days >= rules.min_trading_days:
                outcome = Outcome.PASSED
                days_to_pass.append(d + 1)
                break

        counts[outcome] += 1
        finals[s] = equity

    finals_abs = finals * rules.initial_balance
    return MonteCarloResult(
        n_sims=n_sims,
        pass_rate=counts[Outcome.PASSED] / n_sims,
        breach_daily_rate=counts[Outcome.BREACH_DAILY] / n_sims,
        breach_max_rate=counts[Outcome.BREACH_MAX] / n_sims,
        timeout_rate=counts[Outcome.TIMEOUT] / n_sims,
        median_days_to_pass=float(np.median(days_to_pass)) if days_to_pass else float("nan"),
        p10_final_equity=float(np.percentile(finals_abs, 10)),
        median_final_equity=float(np.percentile(finals_abs, 50)),
        p90_final_equity=float(np.percentile(finals_abs, 90)),
        risk_of_ruin=(counts[Outcome.BREACH_DAILY] + counts[Outcome.BREACH_MAX]) / n_sims,
        outcomes={o.value: c for o, c in counts.items()},
    )


def risk_curve(
    daily: pd.DataFrame,
    rules: PropFirmRules,
    multipliers: tuple[float, ...] = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0),
    **kwargs,
) -> pd.DataFrame:
    """Pass probability as a function of position size.

    This curve is the most practically useful output in the whole package. It is
    almost always humped: too little risk and you time out, too much and the
    daily-loss rule takes you out before the edge compounds. The peak is rarely
    where people intuitively put it — it is usually *lower*.
    """
    rows = []
    for k in multipliers:
        mc = simulate_challenge(daily, rules, risk_multiplier=k, **kwargs)
        rows.append({
            "risk_multiplier": k,
            "pass_rate": mc.pass_rate,
            "breach_daily": mc.breach_daily_rate,
            "breach_max": mc.breach_max_rate,
            "timeout": mc.timeout_rate,
            "median_days_to_pass": mc.median_days_to_pass,
        })
    return pd.DataFrame(rows)
