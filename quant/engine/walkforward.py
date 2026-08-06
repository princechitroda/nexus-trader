"""Anchored / rolling walk-forward optimisation.

The point of this module is to stop you from lying to yourself.

Any of the strategies here can be made to look excellent on a fixed history by
tuning six parameters until the curve is pretty. Walk-forward answers the only
question worth asking: *if I had tuned this using only data available at the
time, would the following months have made money?*

Procedure per fold: optimise on the training window, take the single best
parameter set, trade it untouched through the test window, then roll forward.
Only the concatenated test-window results are reported. The starting balance of
each fold chains from the previous one, so the stitched equity curve is a real
compounding curve and can be fed straight into the prop-firm evaluator.

Read the fold table, not just the summary. A system whose winning parameters
jump around wildly between folds has no stable edge, even if the aggregate OOS
number happens to be positive.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from . import metrics as metrics_mod
from .backtest import Backtester, BacktestResult


@dataclass
class Fold:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    best_params: dict
    train_score: float
    test_result: BacktestResult
    test_metrics: metrics_mod.Metrics


@dataclass
class WalkForwardResult:
    folds: list[Fold]
    oos: BacktestResult
    strategy_name: str
    param_grid: dict = field(default_factory=dict)

    @property
    def fold_table(self) -> pd.DataFrame:
        rows = []
        for f in self.folds:
            row = {
                "test_start": f.test_start.date(),
                "test_end": f.test_end.date(),
                "train_score": round(f.train_score, 3),
                "oos_trades": f.test_metrics.n_trades,
                "oos_return_%": round(f.test_metrics.total_return_pct, 2),
                "oos_pf": round(f.test_metrics.profit_factor, 2),
                "oos_exp_r": round(f.test_metrics.expectancy_r, 3),
                "oos_maxdd_%": round(f.test_metrics.max_dd_pct, 2),
            }
            row.update({f"p_{k}": v for k, v in f.best_params.items()})
            rows.append(row)
        return pd.DataFrame(rows)

    @property
    def efficiency(self) -> float:
        """Fraction of folds that were profitable out of sample.

        Below 0.5 means the optimiser is picking noise. Treat anything under 0.6
        as a system you should not fund.
        """
        if not self.folds:
            return 0.0
        return float(np.mean([f.test_metrics.total_return_pct > 0 for f in self.folds]))


def expand_grid(grid: dict[str, Sequence[Any]]) -> list[dict]:
    if not grid:
        return [{}]
    keys = list(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(grid[k] for k in keys))]


def default_objective(m: metrics_mod.Metrics, min_trades: int = 20) -> float:
    """Robust-ish selection score.

    Deliberately *not* raw return. Return alone picks the fold's luckiest
    parameter set every time. This rewards edge per unit of drawdown, but only
    once there are enough trades for the estimate to mean anything.
    """
    if m.n_trades < min_trades:
        return -1e9
    if m.max_dd_pct <= 0:
        return -1e9
    # penalise reliance on a single outlier trade
    concentration_penalty = max(0.0, m.best_trade_share - 0.25) * 4.0
    return m.return_over_maxdd * min(1.0, m.n_trades / 60.0) - concentration_penalty


def walk_forward(
    df: pd.DataFrame,
    strategy_proto,
    param_grid: dict[str, Sequence[Any]],
    backtester: Backtester | None = None,
    train_months: int = 12,
    test_months: int = 3,
    anchored: bool = False,
    objective: Callable[[metrics_mod.Metrics], float] = default_objective,
    warmup_bars: int | None = None,
    verbose: bool = True,
) -> WalkForwardResult:
    """`strategy_proto` is an instantiated strategy; grid values override its fields."""
    bt = backtester or Backtester()
    combos = expand_grid(param_grid)
    start, end = df.index[0], df.index[-1]
    warmup = warmup_bars if warmup_bars is not None else strategy_proto.warmup()

    folds: list[Fold] = []
    oos_equity, oos_worst, oos_balance = [], [], []
    oos_trades = []
    balance = bt.initial_balance

    train_start = start
    train_end = train_start + pd.DateOffset(months=train_months)

    while train_end + pd.DateOffset(months=test_months) <= end:
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=test_months)

        train_df = df.loc[train_start:train_end]
        # Give the test slice enough leading history to warm its indicators up,
        # otherwise every fold throws away its first N bars of signals.
        test_pos = df.index.searchsorted(test_start)
        lead = df.index[max(0, test_pos - warmup)]
        test_df = df.loc[lead:test_end]

        best_score, best_params = -np.inf, {}
        for combo in combos:
            cand = replace(strategy_proto, **combo)
            res = bt.run(train_df, cand)
            score = objective(metrics_mod.compute(res))
            if score > best_score:
                best_score, best_params = score, combo

        # The lead-in bars only warm indicators up — the engine's own warm-up gate
        # means no signal fires before `test_start` — so the chained balance here
        # is also the balance at the start of the out-of-sample window.
        fold_start_balance = balance
        fold_bt = Backtester(
            instrument=bt.inst, costs=bt.costs, initial_balance=fold_start_balance,
            pessimistic_intrabar=bt.pessimistic, leverage=bt.leverage,
            max_risk_pct=bt.max_risk_pct,
        )
        chosen = replace(strategy_proto, **best_params)
        test_res = fold_bt.run(test_df, chosen)

        # keep only the true out-of-sample portion (drop the warm-up lead-in)
        mask = test_res.equity.index >= test_start
        oos_equity.append(test_res.equity[mask])
        oos_worst.append(test_res.equity_worst[mask])
        oos_balance.append(test_res.balance[mask])
        kept = [t for t in test_res.trades if t.entry_time >= test_start]
        oos_trades.extend(kept)
        if len(test_res.balance[mask]):
            balance = float(test_res.balance[mask].iloc[-1])

        fm = metrics_mod.compute(
            BacktestResult(kept, test_res.equity[mask], test_res.equity_worst[mask],
                           test_res.balance[mask], fold_start_balance,
                           strategy_proto.name, best_params)
        )
        folds.append(Fold(train_start, train_end, test_start, test_end,
                          best_params, best_score, test_res, fm))
        if verbose:
            print(f"  fold {test_start.date()}→{test_end.date()}  "
                  f"params={best_params}  OOS {fm.total_return_pct:+.2f}%  "
                  f"({fm.n_trades} trades, PF {fm.profit_factor:.2f})")

        if not anchored:
            train_start = train_start + pd.DateOffset(months=test_months)
        train_end = train_end + pd.DateOffset(months=test_months)

    if oos_equity:
        eq = pd.concat(oos_equity)
        ew = pd.concat(oos_worst)
        ba = pd.concat(oos_balance)
    else:
        eq = ew = ba = pd.Series(dtype=float)

    oos = BacktestResult(
        trades=oos_trades, equity=eq, equity_worst=ew, balance=ba,
        initial_balance=bt.initial_balance,
        strategy_name=f"{strategy_proto.name}[WFO]", params=dict(param_grid),
    )
    return WalkForwardResult(folds=folds, oos=oos, strategy_name=strategy_proto.name,
                             param_grid=dict(param_grid))
