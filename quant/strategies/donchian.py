"""Donchian channel breakout with a volatility-regime gate.

The oldest published trend system there is (Turtles), applied to gold on an
intraday channel. Its inclusion is a control: if a plain, un-tuned, 40-year-old
breakout rule beats every clever session system on the same data, that tells you
something important about how much of the clever stuff is fitting.

The one addition over the textbook version is the ATR-percentile gate. Channel
breakouts fail systematically in compressed regimes — you buy the high of a range
and get returned to sender. Requiring volatility to already be expanding removes
a large slice of those.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .base import Strategy
from .indicators import atr, donchian, rolling_percentile


@dataclass
class DonchianBreakout(Strategy):
    channel: int = 48               # 48 × M15 = 12 hours
    atr_period: int = 14
    stop_atr_mult: float = 2.0
    vol_lookback: int = 480
    vol_percentile_min: float = 0.55
    session_start_h: float = 7.0    # skip thin Asian breakouts
    session_end_h: float = 20.0

    trail_atr_mult: float | None = 3.0
    max_trades_per_day: int | None = 2
    breakeven_at_r: float | None = 1.5
    partial_at_r: float | None = 1.5

    def warmup(self) -> int:
        return max(self.vol_lookback + self.channel + 50, 600)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._blank(df)
        a = atr(df, self.atr_period)
        dc = donchian(df, self.channel)
        vol_pct = rolling_percentile(a, self.vol_lookback)
        expanding = vol_pct >= self.vol_percentile_min

        hours = pd.Series(df.index.hour + df.index.minute / 60.0, index=df.index)
        in_session = (hours >= self.session_start_h) & (hours < self.session_end_h)

        broke_up = df["close"] > dc["dc_high"]
        broke_dn = df["close"] < dc["dc_low"]
        # only the *first* close outside the channel
        fresh_up = broke_up & ~(df["close"].shift(1) > dc["dc_high"].shift(1))
        fresh_dn = broke_dn & ~(df["close"].shift(1) < dc["dc_low"].shift(1))

        stop_dist = self.stop_atr_mult * a
        out["long_signal"] = (in_session & expanding & fresh_up).fillna(False)
        out["short_signal"] = (in_session & expanding & fresh_dn).fillna(False)
        out["stop_long"] = df["close"] - stop_dist
        out["stop_short"] = df["close"] + stop_dist
        out["target_long"] = np.nan
        out["target_short"] = np.nan
        out["trail_dist"] = a
        return out
