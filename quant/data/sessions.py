"""Trading-session helpers.

Everything in this package works in **UTC**. Gold's character changes a lot
between sessions, so most of the strategies here key off these windows rather
than off clock-agnostic indicators.

Caveat worth knowing: these are fixed UTC windows. London and New York both
observe DST, so during northern-hemisphere summer the real London open lands an
hour earlier in UTC than in winter. `dst_shift_hours` lets a strategy nudge the
windows if you want to model that; the default of 0 keeps it simple and is what
most published gold-session research uses.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Session:
    """A UTC time window. `start_h` may be greater than `end_h` (wraps midnight)."""

    name: str
    start_h: float
    end_h: float

    def mask(self, index: pd.DatetimeIndex, dst_shift_hours: float = 0.0) -> np.ndarray:
        hours = index.hour + index.minute / 60.0
        start = (self.start_h + dst_shift_hours) % 24
        end = (self.end_h + dst_shift_hours) % 24
        if start <= end:
            return (hours >= start) & (hours < end)
        # window wraps through midnight
        return (hours >= start) | (hours < end)


# Standard windows (UTC).
ASIA = Session("asia", 23.0, 7.0)          # Sydney open through pre-London
TOKYO = Session("tokyo", 0.0, 8.0)
LONDON = Session("london", 7.0, 16.0)
NEW_YORK = Session("new_york", 12.0, 21.0)
OVERLAP = Session("overlap", 12.0, 16.0)   # London/NY — gold's highest-volume window

ALL_SESSIONS = (ASIA, TOKYO, LONDON, NEW_YORK, OVERLAP)


def tag_sessions(df: pd.DataFrame, dst_shift_hours: float = 0.0) -> pd.DataFrame:
    """Add a boolean column per session, plus a `trade_day` grouping key.

    `trade_day` is the calendar UTC date. Prop firms almost universally reset
    their daily-loss counter at a fixed server time, so grouping by date is the
    right unit for the daily drawdown rule.
    """
    out = df.copy()
    idx = out.index
    for sess in ALL_SESSIONS:
        out[f"in_{sess.name}"] = sess.mask(idx, dst_shift_hours)
    out["trade_day"] = idx.normalize()
    out["dow"] = idx.dayofweek
    return out


def session_range(
    df: pd.DataFrame,
    session: Session,
    dst_shift_hours: float = 0.0,
) -> pd.DataFrame:
    """Running high/low of the *current* session, and the *completed* previous one.

    Returns columns:
      `sess_high` / `sess_low`   — running extremes so far inside the live window
                                   (NaN outside it)
      `prev_sess_high` / `prev_sess_low`
                                 — extremes of the last window that has fully
                                   closed. These are safe to trade off: they are
                                   only populated on bars *after* the window ended.

    No look-ahead: both are built from cumulative maxima within already-seen bars.
    """
    mask = session.mask(df.index, dst_shift_hours)
    # Each contiguous run of `mask` is one session instance.
    block = (mask != np.roll(mask, 1)).cumsum()
    block[0] = block[0]  # roll wraps; first element handled by the groupby below
    sess_id = pd.Series(np.where(mask, block, np.nan), index=df.index)

    grouped_high = df["high"].groupby(sess_id).cummax()
    grouped_low = df["low"].groupby(sess_id).cummin()

    out = pd.DataFrame(index=df.index)
    out["sess_high"] = grouped_high.where(mask)
    out["sess_low"] = grouped_low.where(mask)

    # Final extreme of each completed session, forward-filled onto later bars.
    final_high = out["sess_high"].groupby(sess_id).transform("last")
    final_low = out["sess_low"].groupby(sess_id).transform("last")
    completed_high = final_high.where(mask).ffill()
    completed_low = final_low.where(mask).ffill()
    # Only expose them once we are *outside* the window that produced them.
    out["prev_sess_high"] = completed_high.where(~mask)
    out["prev_sess_low"] = completed_low.where(~mask)
    out["prev_sess_high"] = out["prev_sess_high"].ffill()
    out["prev_sess_low"] = out["prev_sess_low"].ffill()
    return out


def prior_day_levels(df: pd.DataFrame) -> pd.DataFrame:
    """Previous completed UTC day's high / low / close, aligned onto each bar.

    Shifted by one day, so a bar never sees its own day's extremes.
    """
    day = df.index.normalize()
    daily = df.groupby(day).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    prev = daily.shift(1)
    prev.columns = ["pdh", "pdl", "pdc"]
    return prev.reindex(day).set_index(df.index)
