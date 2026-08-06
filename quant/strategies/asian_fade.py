"""Asian-session range fade.

The deliberate opposite of `LondonORB`, and included for that reason: if both
make money on the same data, at least one of them is fitting noise.

Logic: build a reference range from the first few hours of the Asian session,
then fade pokes outside it back toward the midpoint for the rest of the session.
Gold in Asian hours is genuinely mean-reverting — thin book, no scheduled news,
no directional macro flow — and that is the only window where fading gold is
defensible. Fading gold during London or NY is how accounts die.

Gated on a *compressed* volatility regime. When daily ATR is in the top of its
own range, the market is trending and this system must stand aside.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .base import Strategy
from .indicators import atr, rolling_percentile


@dataclass
class AsianRangeFade(Strategy):
    build_start_h: float = 23.0     # range construction window (UTC), wraps midnight
    build_end_h: float = 2.0
    trade_start_h: float = 2.0      # fade window
    trade_end_h: float = 7.0
    stop_atr_mult: float = 1.0
    min_range_atr: float = 0.8      # too tight and the target is inside the spread
    max_range_atr: float = 4.0
    vol_percentile_max: float = 0.6  # only in compressed regimes
    vol_lookback: int = 480          # ~5 days of M15
    atr_period: int = 14

    max_trades_per_day: int | None = 2
    exit_at_hour: float | None = 7.0
    max_bars: int | None = 20

    def warmup(self) -> int:
        return max(self.vol_lookback + 50, 300)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._blank(df)
        a = atr(df, self.atr_period)
        hours = pd.Series(df.index.hour + df.index.minute / 60.0, index=df.index)

        # The build window wraps midnight, so tag it by "trading night" — bars at
        # 23:00 belong to the *next* calendar day's Asian session.
        night = (df.index + pd.Timedelta(hours=1)).normalize()
        in_build = (hours >= self.build_start_h) | (hours < self.build_end_h)
        in_trade = (hours >= self.trade_start_h) & (hours < self.trade_end_h)

        night_key = pd.Series(np.where(in_build, night, pd.NaT), index=df.index)
        build_high = df["high"].groupby(night_key).transform("max")
        build_low = df["low"].groupby(night_key).transform("min")
        # Expose the completed build range only once the build window has closed.
        ref_high = build_high.where(in_build).groupby(night).transform("last")
        ref_low = build_low.where(in_build).groupby(night).transform("last")
        ref_high = ref_high.where(in_trade)
        ref_low = ref_low.where(in_trade)

        width = ref_high - ref_low
        mid = (ref_high + ref_low) / 2.0
        ok_width = (width >= self.min_range_atr * a) & (width <= self.max_range_atr * a)

        vol_pct = rolling_percentile(a, self.vol_lookback)
        calm = vol_pct <= self.vol_percentile_max

        # Poke above the range then close back inside → fade short (mirror for long).
        poked_up = (df["high"] > ref_high) & (df["close"] < ref_high)
        poked_dn = (df["low"] < ref_low) & (df["close"] > ref_low)

        base = in_trade & ok_width & calm
        out["long_signal"] = (base & poked_dn).fillna(False)
        out["short_signal"] = (base & poked_up).fillna(False)

        out["stop_long"] = df["low"] - self.stop_atr_mult * a
        out["stop_short"] = df["high"] + self.stop_atr_mult * a
        out["target_long"] = mid
        out["target_short"] = mid
        out["trail_dist"] = a
        return out
