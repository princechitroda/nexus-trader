"""Higher-timeframe trend, lower-timeframe pullback entry, ATR trailing exit.

This is the system that pays for gold's *character*: XAUUSD spends long stretches
going nowhere, then delivers multi-hundred-dollar directional runs driven by real
yields, the dollar and risk sentiment. Fixed-target systems cap themselves out of
those runs. This one has no target at all — it trails and lets the winner extend.

Expect a low win rate (35-45%) and a fat right tail. That profile is *hard* on a
prop challenge: the drawdown between runs is what breaks people. It is included
partly as a benchmark for exactly that reason — the metrics will show whether the
tail compensates for the grind.

Trend filter is computed on H4 and projected down causally: the H4 candle stamped
at time T only closes at T, so it is shifted one H4 bar before being forward-filled
onto the execution timeframe. Skipping that shift is the classic multi-timeframe
look-ahead bug and it makes this kind of system look far better than it is.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .base import Strategy
from .indicators import adx, align_higher_tf, atr, ema, resample_ohlc, rsi


@dataclass
class TrendPullback(Strategy):
    htf_rule: str = "4h"
    htf_fast: int = 50
    htf_slow: int = 200
    pullback_ema: int = 20
    rsi_period: int = 14
    rsi_long_min: float = 45.0
    rsi_short_max: float = 55.0
    adx_min: float = 18.0
    adx_period: int = 14
    stop_atr_mult: float = 2.0
    atr_period: int = 14
    session_start_h: float = 6.0
    session_end_h: float = 21.0

    # let the winners run
    trail_atr_mult: float | None = 3.0
    max_trades_per_day: int | None = 2
    breakeven_at_r: float | None = 1.5

    def warmup(self) -> int:
        # 200 H4 bars ≈ 800 hours ≈ 3200 M15 bars
        return 3300

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._blank(df)
        a = atr(df, self.atr_period)

        htf = resample_ohlc(df, self.htf_rule)
        htf_fast = ema(htf["close"], self.htf_fast)
        htf_slow = ema(htf["close"], self.htf_slow)
        up = align_higher_tf(htf_fast > htf_slow, df.index).fillna(False)
        dn = align_higher_tf(htf_fast < htf_slow, df.index).fillna(False)

        e = ema(df["close"], self.pullback_ema)
        r = rsi(df["close"], self.rsi_period)
        adx_ = adx(df, self.adx_period)
        hours = pd.Series(df.index.hour + df.index.minute / 60.0, index=df.index)
        in_session = (hours >= self.session_start_h) & (hours < self.session_end_h)

        # Pullback: the bar dipped to/through the EMA but closed back on the
        # trend side of it, with momentum still intact.
        touched_up = (df["low"] <= e) & (df["close"] > e)
        touched_dn = (df["high"] >= e) & (df["close"] < e)
        trending = adx_ >= self.adx_min

        long_sig = up & in_session & trending & touched_up & (r >= self.rsi_long_min)
        short_sig = dn & in_session & trending & touched_dn & (r <= self.rsi_short_max)

        stop_dist = self.stop_atr_mult * a
        out["long_signal"] = long_sig.fillna(False)
        out["short_signal"] = short_sig.fillna(False)
        out["stop_long"] = df["close"] - stop_dist
        out["stop_short"] = df["close"] + stop_dist
        # no fixed target — the trailing stop is the exit
        out["target_long"] = np.nan
        out["target_short"] = np.nan
        out["trail_dist"] = a
        return out
