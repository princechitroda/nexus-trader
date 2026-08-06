"""Event-driven backtester for a single instrument.

Design notes — these are the things that decide whether a backtest tells you the
truth or flatters you:

1. **No look-ahead.** A strategy emits its signal from bar *i*'s close. The
   engine fills it at bar *i+1*'s open. Never the same bar.

2. **Intrabar ambiguity resolves against you.** If a bar's range contains both
   the stop and the target, we assume the stop filled first. On M15 gold that
   situation is common, and the optimistic assumption is worth a fake 10-20% a
   year on any wide-target system.

3. **Floating drawdown is real drawdown.** Equity is marked bar by bar using the
   worst excursion inside the bar, not the close. Prop firms breach you on
   floating loss, so the daily-loss rule has to see it.

4. **Spread is paid once, on the correct side.** See `costs.py`. Shorts get
   stopped out by the ask, which fires `spread` earlier than a bid chart shows.

A strategy supplies signals *vectorised* (see `strategies/base.py`); the engine
handles order state, sizing, stop management and accounting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .costs import CostModel, Instrument


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: int                  # +1 long, -1 short
    entry: float
    exit: float
    lots: float
    pnl: float                 # net, after commission
    r_multiple: float
    reason: str                # stop | target | time | session_end | eod
    risk_amount: float
    bars_held: int
    mae_r: float               # worst excursion in R
    mfe_r: float               # best excursion in R
    tag: str = ""


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: pd.Series          # mark-to-market on bar close
    equity_worst: pd.Series    # mark-to-market at the bar's worst excursion
    balance: pd.Series         # realised only
    initial_balance: float
    strategy_name: str
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def trades_df(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(
                columns=[
                    "entry_time", "exit_time", "side", "entry", "exit", "lots", "pnl",
                    "r_multiple", "reason", "risk_amount", "bars_held", "mae_r", "mfe_r", "tag",
                ]
            )
        return pd.DataFrame([t.__dict__ for t in self.trades])


@dataclass
class _Position:
    side: int
    entry: float
    entry_time: pd.Timestamp
    entry_idx: int
    lots: float
    lots_open: float
    stop: float
    target: float
    risk_amount: float
    risk_price: float          # |entry - initial stop|, for R maths
    realised_partial: float = 0.0
    partial_done: bool = False
    moved_to_be: bool = False
    mae: float = 0.0           # in price units, adverse
    mfe: float = 0.0
    tag: str = ""


class Backtester:
    def __init__(
        self,
        instrument: Instrument | None = None,
        costs: CostModel | None = None,
        initial_balance: float = 100_000.0,
        pessimistic_intrabar: bool = True,
        leverage: float = 20.0,       # prop firms commonly cap gold at 1:20
        max_risk_pct: float = 0.02,   # hard cap, whatever the strategy asks for
    ) -> None:
        self.inst = instrument or Instrument()
        self.costs = costs or CostModel()
        self.initial_balance = float(initial_balance)
        self.pessimistic = pessimistic_intrabar
        self.leverage = leverage
        self.max_risk_pct = max_risk_pct

    # ---------------------------------------------------------------- sizing
    def _size(self, equity: float, entry: float, stop: float, risk_pct: float) -> tuple[float, float]:
        """Return (lots, risk_amount). Risk is a fraction of *current* equity."""
        risk_pct = min(risk_pct, self.max_risk_pct)
        risk_amount = equity * risk_pct
        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            return 0.0, 0.0
        # Commission counts against the risk budget: a stop-out costs you the
        # stop distance *plus* the round turn.
        per_lot_loss = stop_dist * self.inst.contract_size + self.costs.commission_per_lot
        lots = risk_amount / per_lot_loss
        # Margin cap — you cannot risk 1% on a 3-cent stop with 1:20 leverage.
        max_by_margin = (equity * self.leverage) / (entry * self.inst.contract_size)
        lots = min(lots, max_by_margin)
        lots = self.inst.round_lots(lots)
        return lots, risk_amount

    # ------------------------------------------------------------------ run
    def run(self, df: pd.DataFrame, strategy) -> BacktestResult:
        data = strategy.prepare(df)
        required = ["long_signal", "short_signal", "stop_long", "stop_short"]
        missing = [c for c in required if c not in data.columns]
        if missing:
            raise ValueError(f"{strategy.name}.prepare() did not produce {missing}")

        n = len(data)
        idx = data.index
        o = data["open"].to_numpy(float)
        h = data["high"].to_numpy(float)
        l = data["low"].to_numpy(float)
        c = data["close"].to_numpy(float)
        hours = idx.hour.to_numpy()
        days = idx.normalize().to_numpy()

        long_sig = data["long_signal"].fillna(False).to_numpy(bool)
        short_sig = data["short_signal"].fillna(False).to_numpy(bool)
        stop_l = data["stop_long"].to_numpy(float)
        stop_s = data["stop_short"].to_numpy(float)
        tgt_l = data["target_long"].to_numpy(float) if "target_long" in data else np.full(n, np.nan)
        tgt_s = data["target_short"].to_numpy(float) if "target_short" in data else np.full(n, np.nan)
        trail = data["trail_dist"].to_numpy(float) if "trail_dist" in data else np.full(n, np.nan)

        cs = self.inst.contract_size
        balance = self.initial_balance
        pos: _Position | None = None
        pending: dict | None = None
        trades: list[Trade] = []

        eq = np.full(n, self.initial_balance)
        eq_worst = np.full(n, self.initial_balance)
        bal_arr = np.full(n, self.initial_balance)

        trades_today = 0
        current_day = days[0] if n else None
        warmup = max(strategy.warmup(), 1)

        def close_position(i: int, price: float, reason: str) -> None:
            nonlocal balance, pos
            assert pos is not None
            leg = pos.side * (price - pos.entry) * cs * pos.lots_open
            commission = self.costs.commission(pos.lots)
            pnl = pos.realised_partial + leg - commission
            balance += pnl
            r = pnl / pos.risk_amount if pos.risk_amount > 0 else 0.0
            denom = pos.risk_price if pos.risk_price > 0 else np.nan
            trades.append(
                Trade(
                    entry_time=pos.entry_time,
                    exit_time=idx[i],
                    side=pos.side,
                    entry=pos.entry,
                    exit=price,
                    lots=pos.lots,
                    pnl=pnl,
                    r_multiple=r,
                    reason=reason,
                    risk_amount=pos.risk_amount,
                    bars_held=i - pos.entry_idx,
                    mae_r=float(pos.mae / denom) if denom == denom else 0.0,
                    mfe_r=float(pos.mfe / denom) if denom == denom else 0.0,
                    tag=pos.tag,
                )
            )
            pos = None

        for i in range(n):
            if days[i] != current_day:
                current_day = days[i]
                trades_today = 0

            spread = self.costs.spread_at(int(hours[i]))

            # ---- 1. fill a pending order at this bar's open -----------------
            if pending is not None and pos is None:
                side = pending["side"]
                if side > 0:
                    entry = o[i] + spread + self.costs.slippage_entry
                else:
                    entry = o[i] - self.costs.slippage_entry
                stop = pending["stop"]
                valid = (side > 0 and stop < entry) or (side < 0 and stop > entry)
                equity_now = balance
                lots, risk_amt = self._size(equity_now, entry, stop, pending["risk_pct"])
                if valid and lots >= self.inst.min_lot:
                    pos = _Position(
                        side=side,
                        entry=entry,
                        entry_time=idx[i],
                        entry_idx=i,
                        lots=lots,
                        lots_open=lots,
                        stop=stop,
                        target=pending["target"],
                        risk_amount=risk_amt,
                        risk_price=abs(entry - stop),
                        tag=pending["tag"],
                    )
                    trades_today += 1
                pending = None

            # ---- 2. manage an open position against this bar ---------------
            if pos is not None:
                # excursion tracking (price units)
                if pos.side > 0:
                    pos.mae = max(pos.mae, pos.entry - l[i])
                    pos.mfe = max(pos.mfe, h[i] - pos.entry)
                    stop_hit = l[i] <= pos.stop
                    tgt_hit = (pos.target == pos.target) and h[i] >= pos.target
                else:
                    pos.mae = max(pos.mae, h[i] - pos.entry)
                    pos.mfe = max(pos.mfe, pos.entry - l[i])
                    # a short is stopped by the ask, which leads the bid by `spread`
                    stop_hit = h[i] >= pos.stop - spread
                    tgt_hit = (pos.target == pos.target) and l[i] <= pos.target - spread

                # partial profit-taking, if configured and not yet done
                partial_r = strategy.partial_at_r
                if (
                    partial_r
                    and not pos.partial_done
                    and pos.risk_price > 0
                    and pos.lots_open > self.inst.min_lot
                ):
                    lvl = pos.entry + pos.side * partial_r * pos.risk_price
                    reached = (h[i] >= lvl) if pos.side > 0 else (l[i] <= lvl - spread)
                    # Only honour the partial if the stop did not fire first.
                    if reached and not (stop_hit and self.pessimistic):
                        cut = self.inst.round_lots(pos.lots * strategy.partial_fraction)
                        cut = min(cut, pos.lots_open)
                        if cut >= self.inst.min_lot:
                            pos.realised_partial += pos.side * (lvl - pos.entry) * cs * cut
                            pos.lots_open = round(pos.lots_open - cut, 8)
                            pos.partial_done = True
                            if strategy.breakeven_after_partial:
                                pos.stop = pos.entry
                                pos.moved_to_be = True

                if stop_hit and (self.pessimistic or not tgt_hit):
                    px = pos.stop - self.costs.slippage_stop if pos.side > 0 else pos.stop + self.costs.slippage_stop
                    close_position(i, px, "stop")
                elif tgt_hit:
                    close_position(i, pos.target, "target")

            # ---- 3. time-based exits ---------------------------------------
            if pos is not None:
                hold = i - pos.entry_idx
                force = False
                reason = ""
                if strategy.max_bars and hold >= strategy.max_bars:
                    force, reason = True, "time"
                elif strategy.exit_at_hour is not None and hours[i] >= strategy.exit_at_hour:
                    if hours[pos.entry_idx] < strategy.exit_at_hour or hold > 0:
                        force, reason = True, "session_end"
                elif strategy.flat_before_weekend and idx[i].dayofweek == 4 and hours[i] >= 19:
                    force, reason = True, "eod"
                if force:
                    px = c[i] - self.costs.slippage_entry if pos.side > 0 else c[i] + spread + self.costs.slippage_entry
                    close_position(i, px, reason)

            # ---- 4. stop management for the *next* bar ---------------------
            if pos is not None:
                if strategy.breakeven_at_r and not pos.moved_to_be and pos.risk_price > 0:
                    trigger = pos.entry + pos.side * strategy.breakeven_at_r * pos.risk_price
                    reached = (h[i] >= trigger) if pos.side > 0 else (l[i] <= trigger)
                    if reached:
                        be = pos.entry + pos.side * strategy.breakeven_offset_r * pos.risk_price
                        pos.stop = max(pos.stop, be) if pos.side > 0 else min(pos.stop, be)
                        pos.moved_to_be = True
                if strategy.trail_atr_mult and trail[i] == trail[i]:
                    dist = trail[i] * strategy.trail_atr_mult
                    if pos.side > 0:
                        pos.stop = max(pos.stop, c[i] - dist)
                    else:
                        pos.stop = min(pos.stop, c[i] + dist)

            # ---- 5. mark to market -----------------------------------------
            if pos is not None:
                float_close = pos.side * (c[i] - pos.entry) * cs * pos.lots_open + pos.realised_partial
                worst_px = l[i] if pos.side > 0 else h[i]
                float_worst = pos.side * (worst_px - pos.entry) * cs * pos.lots_open + pos.realised_partial
                commission = self.costs.commission(pos.lots)
                eq[i] = balance + float_close - commission
                eq_worst[i] = balance + float_worst - commission
            else:
                eq[i] = balance
                eq_worst[i] = balance
            bal_arr[i] = balance

            # ---- 6. new signal from this bar's close, filled next bar ------
            if pos is None and pending is None and i >= warmup and i < n - 1:
                if strategy.max_trades_per_day and trades_today >= strategy.max_trades_per_day:
                    continue
                if long_sig[i] and stop_l[i] == stop_l[i]:
                    pending = {
                        "side": 1, "stop": float(stop_l[i]), "target": float(tgt_l[i]),
                        "risk_pct": strategy.risk_pct, "tag": strategy.name,
                    }
                elif short_sig[i] and stop_s[i] == stop_s[i]:
                    pending = {
                        "side": -1, "stop": float(stop_s[i]), "target": float(tgt_s[i]),
                        "risk_pct": strategy.risk_pct, "tag": strategy.name,
                    }

        # close anything still open at the final bar
        if pos is not None:
            spread = self.costs.spread_at(int(hours[-1]))
            px = c[-1] - self.costs.slippage_entry if pos.side > 0 else c[-1] + spread + self.costs.slippage_entry
            close_position(n - 1, px, "eod")
            eq[-1] = eq_worst[-1] = bal_arr[-1] = balance

        return BacktestResult(
            trades=trades,
            equity=pd.Series(eq, index=idx, name="equity"),
            equity_worst=pd.Series(eq_worst, index=idx, name="equity_worst"),
            balance=pd.Series(bal_arr, index=idx, name="balance"),
            initial_balance=self.initial_balance,
            strategy_name=strategy.name,
            params=dict(strategy.params),
        )
