"""Run one strategy across many symbols and combine the results into one account.

This exists because of a number, not a hunch. The requirements grid says a feeble
+0.05R edge taken three times a day beats a strong +0.10R edge taken once a day,
by a wide margin, for the purpose of passing a drawdown-limited challenge. Our
gold edge is fine (+0.049R out of sample) and its frequency is not (0.35
trades/day). Frequency is short by roughly 10x.

The Asian-range break is not a gold-specific pattern. It is a liquidity mechanism
— a thin overnight session accumulating orders, then a busy London session running
them — and it exists in every pair with that structure. Running the same signal
across ten symbols multiplies frequency by ten *and*, to the extent the symbols are
not perfectly correlated, cuts the drawdown per unit of return. Both effects push
the same way.

**Risk budgeting.** Each symbol is backtested independently at `risk_pct`, then the
account is formed as the equal-weighted average of the per-symbol return streams.
That is equivalent to each symbol risking `risk_pct / n`, so total exposure is
`risk_pct` when every symbol happens to be in a trade at once. The intraday floor
is combined the same way, which assumes all symbols reach their worst excursion
simultaneously — pessimistic, and deliberately so, since that is the assumption the
daily-loss rule effectively makes about you.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from .backtest import Backtester, BacktestResult
from .costs import CostModel, Instrument


@dataclass(frozen=True)
class SymbolSpec:
    """Per-symbol scaling and cost assumptions.

    `price_scale` divides the raw integer prices these public datasets ship
    (MT5 "points": 1e5 for 5-digit majors, 1e3 for JPY crosses, 1e2 for gold).
    `spread` is in price units after scaling. `contract_size` is units per 1.0 lot.
    """

    price_scale: float
    spread: float
    contract_size: float


# Typical raw-spread pricing. Deliberately on the pessimistic side of retail.
SYMBOL_SPECS: dict[str, SymbolSpec] = {
    "XAUUSD": SymbolSpec(1e2, 0.30, 100.0),
    "EURUSD": SymbolSpec(1e5, 0.00010, 100_000.0),
    "GBPUSD": SymbolSpec(1e5, 0.00016, 100_000.0),
    "AUDUSD": SymbolSpec(1e5, 0.00013, 100_000.0),
    "USDCAD": SymbolSpec(1e5, 0.00017, 100_000.0),
    "USDCHF": SymbolSpec(1e5, 0.00016, 100_000.0),
    "EURGBP": SymbolSpec(1e5, 0.00014, 100_000.0),
    "EURCHF": SymbolSpec(1e5, 0.00017, 100_000.0),
    "USDJPY": SymbolSpec(1e3, 0.011, 100_000.0),
    "EURJPY": SymbolSpec(1e3, 0.016, 100_000.0),
    "GBPJPY": SymbolSpec(1e3, 0.026, 100_000.0),
    "AUDJPY": SymbolSpec(1e3, 0.016, 100_000.0),
}


def combine(results: dict[str, BacktestResult], initial_balance: float = 100_000.0) -> BacktestResult:
    """Equal-weight the per-symbol return streams into a single account curve."""
    live = {s: r for s, r in results.items() if len(r.equity) > 1}
    if not live:
        raise ValueError("no symbol produced an equity curve")

    idx = sorted(set().union(*(set(r.equity.index) for r in live.values())))
    idx = pd.DatetimeIndex(idx)

    rets, worst_rets = [], []
    for res in live.values():
        eq = res.equity.reindex(idx).ffill().bfill()
        ew = res.equity_worst.reindex(idx).ffill().bfill()
        prev = eq.shift(1)
        rets.append((eq / prev - 1.0).fillna(0.0))
        # worst excursion measured against the same denominator as the close return
        worst_rets.append((ew / prev - 1.0).fillna(0.0))

    port_ret = pd.concat(rets, axis=1).mean(axis=1)
    port_worst = pd.concat(worst_rets, axis=1).mean(axis=1)

    equity = initial_balance * (1.0 + port_ret).cumprod()
    prev_eq = equity.shift(1).fillna(initial_balance)
    equity_worst = prev_eq * (1.0 + port_worst)
    equity_worst = pd.concat([equity, equity_worst], axis=1).min(axis=1)

    trades = []
    for res in live.values():
        trades.extend(res.trades)
    trades.sort(key=lambda t: t.exit_time)

    return BacktestResult(
        trades=trades,
        equity=equity.rename("equity"),
        equity_worst=equity_worst.rename("equity_worst"),
        balance=equity.rename("balance"),
        initial_balance=initial_balance,
        strategy_name=f"Portfolio[{len(live)}]",
        params={"symbols": list(live)},
    )


def run_portfolio(
    frames: dict[str, pd.DataFrame],
    strategy_proto,
    initial_balance: float = 100_000.0,
    commission_per_lot: float = 7.0,
    max_risk_pct: float = 0.02,
) -> tuple[BacktestResult, dict[str, BacktestResult]]:
    """Backtest `strategy_proto` on every frame, each with its own spec, then combine."""
    per_symbol: dict[str, BacktestResult] = {}
    for symbol, df in frames.items():
        spec = SYMBOL_SPECS.get(symbol)
        if spec is None:
            raise KeyError(f"no SymbolSpec for {symbol}")
        bt = Backtester(
            instrument=Instrument(symbol=symbol, contract_size=spec.contract_size),
            costs=CostModel(spread=spec.spread, commission_per_lot=commission_per_lot,
                            slippage_entry=spec.spread * 0.1, slippage_stop=spec.spread * 0.4),
            initial_balance=initial_balance,
            max_risk_pct=max_risk_pct,
        )
        per_symbol[symbol] = bt.run(df, replace(strategy_proto))
    return combine(per_symbol, initial_balance), per_symbol


def correlation_table(per_symbol: dict[str, BacktestResult]) -> pd.DataFrame:
    """Daily-return correlation between the symbol streams.

    The diversification benefit is only as good as this matrix is empty. If the
    average off-diagonal correlation is above ~0.5, ten symbols are behaving like
    three and the frequency gain is largely an illusion.
    """
    cols = {}
    for sym, res in per_symbol.items():
        if len(res.equity) > 1:
            cols[sym] = res.equity.resample("1D").last().pct_change()
    return pd.DataFrame(cols).corr()
