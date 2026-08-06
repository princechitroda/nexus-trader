"""London opening-range breakout.

The single most durable structural pattern in gold: the Asian session compresses
into a narrow range on thin volume, then London arrives and expands it. The
trade is simply "go with the side that breaks first, once London is live".

Why it has a plausible reason to exist (not just a curve fit): the Asian range is
where overnight stops accumulate, and the London open is when the day's real
volume shows up to run them. That is a liquidity mechanism, not a chart pattern,
which is why it has survived for decades — though the edge has thinned as more
of the flow has automated.

Filters that matter:
  * the Asian range must be *narrow relative to normal daily movement*. A wide
    Asian range means the move already happened; breaking it is late.
  * the break must occur inside the London window. Breaks at 06:45 are noise.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..data.sessions import ASIA, session_range
from .base import Strategy
from .indicators import atr


@dataclass
class LondonORB(Strategy):
    # entry window (UTC)
    entry_start_h: float = 7.0
    entry_end_h: float = 12.0
    # Asian range must sit inside this band, measured in multiples of ATR(14)
    min_range_atr: float = 1.2
    max_range_atr: float = 6.0
    # buffer beyond the range edge, in ATR, to avoid paying for a 1-tick poke
    breakout_buffer_atr: float = 0.10
    stop_atr_mult: float = 1.5
    target_r: float = 2.0
    atr_period: int = 14

    # management defaults tuned for an intraday breakout
    max_trades_per_day: int | None = 1
    exit_at_hour: float | None = 16.0
    breakeven_at_r: float | None = 1.0
    partial_at_r: float | None = 1.0

    def warmup(self) -> int:
        return max(200, self.atr_period * 5)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._blank(df)
        a = atr(df, self.atr_period)
        rng = session_range(df, ASIA)
        hi, lo = rng["prev_sess_high"], rng["prev_sess_low"]
        width = hi - lo

        hours = pd.Series(df.index.hour + df.index.minute / 60.0, index=df.index)
        in_window = (hours >= self.entry_start_h) & (hours < self.entry_end_h)

        ok_width = (width >= self.min_range_atr * a) & (width <= self.max_range_atr * a)
        buf = self.breakout_buffer_atr * a

        # A *close* beyond the edge, not just a wick. Wick-triggered entries look
        # great in a backtest and get shredded live by the spread.
        broke_up = df["close"] > (hi + buf)
        broke_dn = df["close"] < (lo - buf)
        prev_inside = (df["close"].shift(1) <= (hi + buf)) & (df["close"].shift(1) >= (lo - buf))

        long_sig = in_window & ok_width & broke_up & prev_inside & hi.notna()
        short_sig = in_window & ok_width & broke_dn & prev_inside & lo.notna()

        stop_dist = self.stop_atr_mult * a
        out["long_signal"] = long_sig.fillna(False)
        out["short_signal"] = short_sig.fillna(False)
        out["stop_long"] = df["close"] - stop_dist
        out["stop_short"] = df["close"] + stop_dist
        out["target_long"] = df["close"] + self.target_r * stop_dist
        out["target_short"] = df["close"] - self.target_r * stop_dist
        out["trail_dist"] = a
        out["asia_high"] = hi
        out["asia_low"] = lo
        return out
