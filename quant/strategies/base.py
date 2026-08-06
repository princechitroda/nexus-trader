"""Strategy interface.

Signals are produced **vectorised** in `prepare()`: the strategy returns the
input frame plus these columns —

  long_signal / short_signal : bool  — "I want in, at the next bar's open"
  stop_long / stop_short     : float — absolute stop price for that entry
  target_long / target_short : float — absolute target, or NaN to run a trailing stop
  trail_dist                 : float — ATR (or whatever distance unit) for trailing

Everything stateful — one trade at a time, trades-per-day caps, breakeven moves,
partials, session flattening — lives in the engine, so it is implemented once and
identically for every strategy. That matters: management rules are where most
homemade backtests quietly diverge from the live EA.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class Strategy(ABC):
    # --- risk ---------------------------------------------------------------
    risk_pct: float = 0.005            # fraction of equity risked per trade
    max_trades_per_day: int | None = 2

    # --- trade management ---------------------------------------------------
    max_bars: int | None = None        # hard time stop, in bars
    exit_at_hour: float | None = None  # flatten at this UTC hour
    breakeven_at_r: float | None = None
    breakeven_offset_r: float = 0.1    # park BE slightly in profit to cover costs
    trail_atr_mult: float | None = None
    partial_at_r: float | None = None
    partial_fraction: float = 0.5
    breakeven_after_partial: bool = True
    flat_before_weekend: bool = True   # gold gaps over the weekend; don't hold through it

    @property
    def name(self) -> str:
        return type(self).__name__

    @property
    def params(self) -> dict:
        return asdict(self)

    def warmup(self) -> int:
        """Bars of history needed before the first signal is trustworthy."""
        return 300

    @abstractmethod
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        ...

    # ------------------------------------------------------------------ util
    @staticmethod
    def _blank(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["long_signal"] = False
        out["short_signal"] = False
        out["stop_long"] = np.nan
        out["stop_short"] = np.nan
        out["target_long"] = np.nan
        out["target_short"] = np.nan
        out["trail_dist"] = np.nan
        return out

    def describe(self) -> str:
        bits = [f"{k}={v}" for k, v in self.params.items() if v is not None]
        return f"{self.name}({', '.join(bits)})"
