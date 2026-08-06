"""Asian-range break, held as a swing — built from measured structure, not intuition.

This strategy exists because of `research/edge_scan.py`, not the other way round.
The scan measured, on ten years of real XAUUSD, the mean forward move after price
breaks the Asian range during London hours:

    horizon   1h      +0.00 ATR   (p = 0.94)   ← nothing
    horizon   4h      +0.23 ATR   (p = 0.005)
    horizon   8h      +0.52 ATR   (p = 0.001)
    horizon  24h      +0.70 ATR   (p = 0.007)

The edge is **zero intraday and accrues over a day**. That single fact explains
why the conventional intraday ORB — 2R target, flat by the London close — loses
money: it is structurally correct about direction and then exits before the move
it correctly predicted has happened, paying the spread for the privilege.

Three design consequences, all of them departures from how retail trades this:

1. **Hold for up to a day**, no intraday flatten. The time stop is the exit of
   last resort, not a session bell.
2. **No breakeven stop and no partial by default.** Both truncate exactly the
   right tail that the 8-24h horizon is there to capture. They are available as
   parameters so walk-forward can overrule this, but the prior is against them.
3. **Run it on H1, not M15.** The edge is horizon-based, so the execution
   timeframe is free — and cost drag scales with 1/stop-distance. Measured on
   M15 the drag was 0.14-0.25R per trade against a gross edge of 0.03-0.07R;
   on H1 with a 2.5-ATR stop it falls to a few percent of R. Same edge, a
   fraction of the toll.

The momentum filter is the second measured effect (`momentum_lb16`, +0.25 ATR at
24h): only take the break when the multi-hour trend already agrees with it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..data.sessions import ASIA, session_range
from .base import Strategy
from .indicators import atr, rolling_percentile


@dataclass
class AsiaBreakMomentum(Strategy):
    # entry window (UTC hours, on a DST-free broker clock)
    # Defaults below are the configuration walk-forward selected most often across
    # 17 folds (see FINDINGS.md §5) — London *and* New York, long-biased, wide stop,
    # 48h hold. They are documented as such rather than presented as tuned optima.
    entry_start_h: float = 7.0
    entry_end_h: float = 18.0

    breakout_buffer_atr: float = 0.10
    min_range_atr: float = 0.5
    max_range_atr: float = 5.0

    # momentum confirmation: the N-bar move must agree with the break direction
    mom_lookback: int = 4
    require_mom_align: bool = True

    stop_atr_mult: float = 8.0
    atr_period: int = 14

    # Volatility-regime gate. The edge scan found momentum is significant in
    # expanding volatility (+0.14 ATR, p=0.027) and absent otherwise, and the
    # year-by-year record agrees violently: 2013 and 2020 — the two high-vol
    # trending years — carry more than the whole ten-year profit, while the
    # quiet years bleed. 0.0 disables the gate.
    vol_percentile_min: float = 0.0
    vol_lookback: int = 480

    # Direction filter: 0 = both sides, +1 = longs only, -1 = shorts only.
    # Controlling for gold's secular drift (+0.40 ATR per 48h over 2004-2025),
    # the break signal carries near-identical *excess* information on both sides
    # (+0.39 long, +0.36 short). The realised P&L does not match that symmetry,
    # because a short additionally pays the drift it is standing in front of.
    # Restricting to longs therefore keeps the signal and stops donating the
    # baseline — at the cost of being an explicit bet that gold keeps rising.
    direction: int = 1

    # Let it run. The edge is at 8-24h, so the exits are deliberately loose.
    trail_atr_mult: float | None = None
    max_bars: int | None = 48
    max_trades_per_day: int | None = 1
    breakeven_at_r: float | None = None
    partial_at_r: float | None = None
    exit_at_hour: float | None = None
    flat_before_weekend: bool = True

    def warmup(self) -> int:
        base = max(120, self.atr_period * 6)
        return max(base, self.vol_lookback + 50) if self.vol_percentile_min > 0 else base

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
        broke_up = df["close"] > (hi + buf)
        broke_dn = df["close"] < (lo - buf)
        # only the first close outside the range
        fresh_up = broke_up & ~broke_up.shift(1).fillna(False)
        fresh_dn = broke_dn & ~broke_dn.shift(1).fillna(False)

        if self.require_mom_align:
            mom = df["close"] - df["close"].shift(self.mom_lookback)
            fresh_up = fresh_up & (mom > 0)
            fresh_dn = fresh_dn & (mom < 0)

        base = in_window & ok_width & hi.notna()
        if self.vol_percentile_min > 0.0:
            base = base & (rolling_percentile(a, self.vol_lookback) >= self.vol_percentile_min)
        long_ok = self.direction >= 0
        short_ok = self.direction <= 0
        out["long_signal"] = (base & fresh_up).fillna(False) & long_ok
        out["short_signal"] = (base & fresh_dn).fillna(False) & short_ok

        stop_dist = self.stop_atr_mult * a
        out["stop_long"] = df["close"] - stop_dist
        out["stop_short"] = df["close"] + stop_dist
        # no fixed target — the trailing stop and the time stop are the exits
        out["target_long"] = np.nan
        out["target_short"] = np.nan
        out["trail_dist"] = a
        return out
