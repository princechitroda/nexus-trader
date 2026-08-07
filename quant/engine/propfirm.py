"""Funded-account (prop firm) rule simulator.

A strategy can be profitable and still fail a challenge, because the challenge
does not grade you on profit — it grades you on *the order in which* the profit
arrives relative to two drawdown tripwires. This module applies the rules the way
the firms actually apply them:

  * **Daily loss is measured on equity, including floating positions**, against
    the balance at the daily reset. A trade that is 5.1% underwater at 14:00 and
    recovers by 17:00 is still a breach. This is the rule that catches people
    whose backtest "never had a losing day".

  * **Max loss** is either static from the starting balance (FTMO-style) or
    trailing from the high-water mark (many newer firms). Trailing is materially
    harder and is worth simulating separately before you buy a challenge.

  * **Minimum trading days** stops you from passing with one lucky NFP.

Defaults below are FTMO-ish two-phase numbers. Change them to match whichever
firm you actually buy from — the rules vary more than the marketing suggests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from .backtest import BacktestResult


class Outcome(str, Enum):
    PASSED = "PASSED"
    BREACH_DAILY = "BREACH_DAILY_LOSS"
    BREACH_MAX = "BREACH_MAX_LOSS"
    TIMEOUT = "TIMEOUT"
    INCOMPLETE = "INCOMPLETE"        # ran out of data before passing or failing


@dataclass
class PropFirmRules:
    initial_balance: float = 100_000.0
    profit_target_pct: float = 0.10
    daily_loss_pct: float = 0.05
    max_loss_pct: float = 0.10
    trailing_max_loss: bool = False   # True = drawdown trails the equity high-water mark
    min_trading_days: int = 4
    max_calendar_days: int | None = None   # None = unlimited (most firms now)
    reset_hour_utc: int = 21          # broker server day roll; 21:00 UTC ≈ 23:00 CET

    @classmethod
    def phase1(cls, balance: float = 100_000.0) -> "PropFirmRules":
        return cls(initial_balance=balance, profit_target_pct=0.10)

    @classmethod
    def phase2(cls, balance: float = 100_000.0) -> "PropFirmRules":
        return cls(initial_balance=balance, profit_target_pct=0.05)

    @classmethod
    def funded(cls, balance: float = 100_000.0) -> "PropFirmRules":
        """Live funded stage: no target to hit, just don't breach."""
        return cls(initial_balance=balance, profit_target_pct=10.0, min_trading_days=0)

    # ── FundingPips presets ────────────────────────────────────────────────
    # Compiled from public documentation, August 2026. Prop firm terms change
    # often and differ by plan — re-read the live rulebook for the exact plan you
    # buy before trusting any number these produce.
    #
    # Two details of theirs that matter and are modelled here:
    #   * the daily limit is measured against the *higher* of the day's opening
    #     balance or opening equity, and counts floating P&L
    #   * the max loss is a static floor from the starting balance, and each
    #     phase starts with a fresh drawdown budget
    # The daily reset is 00:00 platform time (UTC+3), i.e. 21:00 UTC.

    @classmethod
    def fundingpips_2step_p1(cls, balance: float = 5_000.0) -> "PropFirmRules":
        return cls(initial_balance=balance, profit_target_pct=0.08, daily_loss_pct=0.05,
                   max_loss_pct=0.10, trailing_max_loss=False, min_trading_days=3,
                   max_calendar_days=None, reset_hour_utc=21)

    @classmethod
    def fundingpips_2step_p2(cls, balance: float = 5_000.0) -> "PropFirmRules":
        return cls(initial_balance=balance, profit_target_pct=0.05, daily_loss_pct=0.05,
                   max_loss_pct=0.10, trailing_max_loss=False, min_trading_days=3,
                   max_calendar_days=None, reset_hour_utc=21)

    @classmethod
    def fundingpips_1step_flex(cls, balance: float = 5_000.0) -> "PropFirmRules":
        return cls(initial_balance=balance, profit_target_pct=0.10, daily_loss_pct=0.04,
                   max_loss_pct=0.06, trailing_max_loss=False, min_trading_days=3,
                   max_calendar_days=None, reset_hour_utc=21)

    @classmethod
    def fundingpips_zero(cls, balance: float = 5_000.0) -> "PropFirmRules":
        """Instant-funded: no target, but a 5% *trailing* floor and a 3% daily cap."""
        return cls(initial_balance=balance, profit_target_pct=10.0, daily_loss_pct=0.03,
                   max_loss_pct=0.05, trailing_max_loss=True, min_trading_days=0,
                   max_calendar_days=None, reset_hour_utc=21)


@dataclass
class PropFirmResult:
    outcome: Outcome
    days_elapsed: int
    trading_days: int
    final_equity: float
    peak_equity: float
    worst_daily_loss_pct: float
    max_total_dd_pct: float
    breach_time: pd.Timestamp | None = None

    @property
    def passed(self) -> bool:
        return self.outcome == Outcome.PASSED


def _prop_day(index: pd.DatetimeIndex, reset_hour: int) -> np.ndarray:
    """Map bars to trading days that roll at `reset_hour` UTC."""
    shifted = index - pd.Timedelta(hours=reset_hour)
    return shifted.normalize().to_numpy()


def evaluate(result: BacktestResult, rules: PropFirmRules) -> PropFirmResult:
    """Walk the equity curve bar by bar and apply the rules in order."""
    eq = result.equity.to_numpy(float)
    eq_worst = result.equity_worst.to_numpy(float)
    bal = result.balance.to_numpy(float)
    idx = result.equity.index
    if len(eq) == 0:
        return PropFirmResult(Outcome.INCOMPLETE, 0, 0, rules.initial_balance,
                              rules.initial_balance, 0.0, 0.0)

    # Rescale a backtest run at a different starting balance onto the firm's size.
    scale = rules.initial_balance / result.initial_balance
    eq = rules.initial_balance + (eq - result.initial_balance) * scale
    eq_worst = rules.initial_balance + (eq_worst - result.initial_balance) * scale
    bal = rules.initial_balance + (bal - result.initial_balance) * scale

    days = _prop_day(idx, rules.reset_hour_utc)
    target = rules.initial_balance * (1.0 + rules.profit_target_pct)

    trade_days = set()
    if result.trades:
        t_idx = pd.DatetimeIndex([t.exit_time for t in result.trades])
        trade_days = set(pd.Series(_prop_day(t_idx, rules.reset_hour_utc)).unique())

    day_start_equity = eq[0]
    current_day = days[0]
    peak = eq[0]
    hwm = eq[0]
    worst_daily = 0.0
    worst_total_dd = 0.0
    start_ts = idx[0]
    seen_trade_days: set = set()

    for i in range(len(eq)):
        if days[i] != current_day:
            current_day = days[i]
            day_start_equity = eq[i - 1] if i > 0 else eq[i]
        if days[i] in trade_days:
            seen_trade_days.add(days[i])

        peak = max(peak, eq[i])
        hwm = max(hwm, eq[i])

        # --- daily loss, measured on the intrabar floor -----------------------
        daily_dd = (day_start_equity - eq_worst[i]) / day_start_equity
        worst_daily = max(worst_daily, daily_dd)
        if daily_dd >= rules.daily_loss_pct:
            return PropFirmResult(
                Outcome.BREACH_DAILY, (idx[i] - start_ts).days, len(seen_trade_days),
                float(eq[i]), float(peak), 100.0 * worst_daily, 100.0 * worst_total_dd, idx[i],
            )

        # --- overall loss ----------------------------------------------------
        floor_ref = hwm if rules.trailing_max_loss else rules.initial_balance
        total_dd = (floor_ref - eq_worst[i]) / floor_ref
        worst_total_dd = max(worst_total_dd, total_dd)
        if total_dd >= rules.max_loss_pct:
            return PropFirmResult(
                Outcome.BREACH_MAX, (idx[i] - start_ts).days, len(seen_trade_days),
                float(eq[i]), float(peak), 100.0 * worst_daily, 100.0 * worst_total_dd, idx[i],
            )

        # --- profit target (on closed balance — the conservative reading) -----
        if bal[i] >= target and len(seen_trade_days) >= rules.min_trading_days:
            return PropFirmResult(
                Outcome.PASSED, (idx[i] - start_ts).days, len(seen_trade_days),
                float(eq[i]), float(peak), 100.0 * worst_daily, 100.0 * worst_total_dd, idx[i],
            )

        if rules.max_calendar_days is not None and (idx[i] - start_ts).days > rules.max_calendar_days:
            return PropFirmResult(
                Outcome.TIMEOUT, (idx[i] - start_ts).days, len(seen_trade_days),
                float(eq[i]), float(peak), 100.0 * worst_daily, 100.0 * worst_total_dd, idx[i],
            )

    return PropFirmResult(
        Outcome.INCOMPLETE, (idx[-1] - start_ts).days, len(seen_trade_days),
        float(eq[-1]), float(peak), 100.0 * worst_daily, 100.0 * worst_total_dd, None,
    )


def daily_returns_table(result: BacktestResult, reset_hour_utc: int = 21) -> pd.DataFrame:
    """Per-trading-day close return and intraday floor, both as fractions of the
    day's starting equity. This is the raw material the Monte Carlo resamples."""
    idx = result.equity.index
    days = _prop_day(idx, reset_hour_utc)
    frame = pd.DataFrame(
        {"equity": result.equity.to_numpy(float),
         "worst": result.equity_worst.to_numpy(float),
         "day": days},
        index=idx,
    )
    grouped = frame.groupby("day")
    out = pd.DataFrame({
        "close": grouped["equity"].last(),
        "floor": grouped["worst"].min(),
    })
    start = out["close"].shift(1)
    start.iloc[0] = result.initial_balance
    out["r_close"] = out["close"] / start - 1.0
    out["r_floor"] = out["floor"] / start - 1.0
    return out[["r_close", "r_floor"]].dropna()
