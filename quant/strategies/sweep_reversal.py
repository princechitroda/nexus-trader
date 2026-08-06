"""Liquidity-sweep reversal (prior-day high/low stop hunt).

The mechanical version of what a lot of discretionary gold traders actually do:
wait for price to run the obvious level where stops are parked — yesterday's high
or low — then take the reversal when it fails to hold above it.

Rules, deliberately simple so there is little to overfit:
  1. price trades beyond the prior day's high (or low)
  2. within `confirm_bars` it closes back inside the prior day's range
  3. enter against the sweep, stop beyond the sweep extreme

The stop placement is the whole trade. It sits past the point where the market
just proved it could not sustain, so when the read is wrong you find out quickly
and cheaply. That produces a tight loss distribution, which is exactly what a
drawdown-limited funded account wants.

Restricted to London/NY: a sweep of the prior-day high at 02:00 UTC on 300 lots
of Asian volume means nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..data.sessions import prior_day_levels
from .base import Strategy
from .indicators import atr


@dataclass
class SweepReversal(Strategy):
    confirm_bars: int = 3           # bars allowed for the reclaim
    stop_buffer_atr: float = 0.35   # beyond the sweep extreme
    target_r: float = 2.0
    min_sweep_atr: float = 0.15     # ignore a 2-cent poke; it isn't a sweep
    max_sweep_atr: float = 2.5      # a huge break is a real breakout, not a hunt
    atr_period: int = 14
    session_start_h: float = 7.0
    session_end_h: float = 20.0

    max_trades_per_day: int | None = 2
    exit_at_hour: float | None = 21.0
    breakeven_at_r: float | None = 1.0
    partial_at_r: float | None = 1.0

    def warmup(self) -> int:
        return 400

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._blank(df)
        a = atr(df, self.atr_period)
        lv = prior_day_levels(df)
        pdh, pdl = lv["pdh"], lv["pdl"]

        hours = pd.Series(df.index.hour + df.index.minute / 60.0, index=df.index)
        in_session = (hours >= self.session_start_h) & (hours < self.session_end_h)

        # Step 1: did any of the last `confirm_bars` bars trade beyond the level,
        # by a sane amount?
        over = df["high"] - pdh
        under = pdl - df["low"]
        swept_up = (over >= self.min_sweep_atr * a) & (over <= self.max_sweep_atr * a)
        swept_dn = (under >= self.min_sweep_atr * a) & (under <= self.max_sweep_atr * a)

        win = self.confirm_bars
        recent_sweep_up = swept_up.rolling(win, min_periods=1).max().astype(bool)
        recent_sweep_dn = swept_dn.rolling(win, min_periods=1).max().astype(bool)
        # highest point of the sweep, to anchor the stop
        sweep_high = df["high"].rolling(win, min_periods=1).max()
        sweep_low = df["low"].rolling(win, min_periods=1).min()

        # Step 2: reclaim — close back inside the prior day's range.
        reclaimed_dn = recent_sweep_up & (df["close"] < pdh)
        reclaimed_up = recent_sweep_dn & (df["close"] > pdl)
        # fire on the bar the reclaim happens, not on every bar after it
        first_dn = reclaimed_dn & ~reclaimed_dn.shift(1).fillna(False)
        first_up = reclaimed_up & ~reclaimed_up.shift(1).fillna(False)

        out["short_signal"] = (in_session & first_dn).fillna(False)
        out["long_signal"] = (in_session & first_up).fillna(False)

        stop_short = sweep_high + self.stop_buffer_atr * a
        stop_long = sweep_low - self.stop_buffer_atr * a
        out["stop_short"] = stop_short
        out["stop_long"] = stop_long
        out["target_short"] = df["close"] - self.target_r * (stop_short - df["close"]).clip(lower=0.01)
        out["target_long"] = df["close"] + self.target_r * (df["close"] - stop_long).clip(lower=0.01)
        out["trail_dist"] = a
        return out
