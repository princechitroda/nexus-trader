"""Performance statistics.

Beyond the usual suspects there are three numbers here that exist specifically to
catch a backtest that is fooling you:

  `best_trade_share`  — what fraction of total profit came from the single best
                        trade. Above ~0.3 and the "edge" is one lucky day.
  `t_stat`            — t-statistic of the mean R-multiple. Below ~2.0 you cannot
                        distinguish the result from luck, however pretty the curve.
  `max_dd_pct`        — computed from the *worst intrabar* equity, not closes,
                        because that is the number a prop firm measures you on.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .backtest import BacktestResult


@dataclass
class Metrics:
    n_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    std_r: float = 0.0
    t_stat: float = 0.0
    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    max_dd_pct: float = 0.0
    max_dd_days: float = 0.0
    return_over_maxdd: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    trades_per_month: float = 0.0
    best_trade_share: float = 0.0
    avg_bars_held: float = 0.0
    win_rate_long: float = 0.0
    win_rate_short: float = 0.0
    net_profit: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def _max_drawdown(equity: pd.Series) -> tuple[float, float]:
    """(max drawdown as a fraction, longest drawdown duration in days)."""
    if equity.empty:
        return 0.0, 0.0
    running_max = equity.cummax()
    dd = (equity - running_max) / running_max
    max_dd = float(-dd.min())

    under = equity < running_max
    if not under.any():
        return max_dd, 0.0
    # length of the longest stretch below the previous high-water mark
    groups = (~under).cumsum()
    spans = equity.index.to_series().groupby(groups).agg(["first", "last"])
    durations = (spans["last"] - spans["first"]).dt.total_seconds() / 86400.0
    longest = float(durations[under.groupby(groups).any()].max()) if under.any() else 0.0
    return max_dd, longest


def compute(result: BacktestResult) -> Metrics:
    m = Metrics()
    trades = result.trades_df
    eq = result.equity
    eq_worst = result.equity_worst

    if eq.empty:
        return m

    m.n_trades = len(trades)
    m.net_profit = float(eq.iloc[-1] - result.initial_balance)
    m.total_return_pct = 100.0 * m.net_profit / result.initial_balance

    days = max((eq.index[-1] - eq.index[0]).total_seconds() / 86400.0, 1.0)
    years = days / 365.25
    if years > 0 and eq.iloc[-1] > 0:
        m.cagr_pct = 100.0 * ((eq.iloc[-1] / result.initial_balance) ** (1.0 / years) - 1.0)

    m.max_dd_pct, m.max_dd_days = _max_drawdown(eq_worst)
    m.max_dd_pct *= 100.0
    m.return_over_maxdd = m.total_return_pct / m.max_dd_pct if m.max_dd_pct > 0 else 0.0

    # daily returns for risk-adjusted stats
    daily = eq.resample("1D").last().dropna()
    rets = daily.pct_change().dropna()
    if len(rets) > 2 and rets.std() > 0:
        m.sharpe = float(rets.mean() / rets.std() * np.sqrt(252))
        downside = rets[rets < 0]
        if len(downside) > 1 and downside.std() > 0:
            m.sortino = float(rets.mean() / downside.std() * np.sqrt(252))

    if m.n_trades == 0:
        return m

    r = trades["r_multiple"].to_numpy(float)
    pnl = trades["pnl"].to_numpy(float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]

    m.win_rate = 100.0 * len(wins) / len(pnl)
    gross_win, gross_loss = wins.sum(), -losses.sum()
    m.profit_factor = float(gross_win / gross_loss) if gross_loss > 0 else float("inf")
    m.expectancy_r = float(r.mean())
    m.std_r = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    m.t_stat = float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))) if len(r) > 1 and r.std(ddof=1) > 0 else 0.0
    win_r, loss_r = r[r > 0], r[r <= 0]
    m.avg_win_r = float(win_r.mean()) if len(win_r) else 0.0
    m.avg_loss_r = float(loss_r.mean()) if len(loss_r) else 0.0
    m.trades_per_month = m.n_trades / max(days / 30.44, 1e-9)
    m.best_trade_share = float(pnl.max() / gross_win) if gross_win > 0 else 0.0
    m.avg_bars_held = float(trades["bars_held"].mean())

    longs = trades[trades["side"] > 0]
    shorts = trades[trades["side"] < 0]
    m.win_rate_long = 100.0 * (longs["pnl"] > 0).mean() if len(longs) else 0.0
    m.win_rate_short = 100.0 * (shorts["pnl"] > 0).mean() if len(shorts) else 0.0
    return m


def summary_row(result: BacktestResult) -> dict:
    m = compute(result)
    row = {"strategy": result.strategy_name}
    row.update(m.as_dict())
    return row
