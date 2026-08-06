"""Measure raw statistical structure in gold *before* fitting any strategy to it.

Testing five hand-picked strategies answers "do my five guesses work". It does not
answer "where is there any edge at all", and if the five all fail you have learned
almost nothing about what to build next.

This module works the other way round. It measures conditional forward returns
directly — for a condition C and a horizon h, what is the mean forward move, and
is it distinguishable from zero? Whatever survives here is a *fact about gold*
that a strategy can then be built around. Whatever doesn't is not worth coding.

Two methodological points that decide whether these numbers are real:

**Returns are normalised by ATR at the signal bar.** Gold ran from $1,046 to
$2,075 in this sample; a $3 move means something completely different at each
end. Measuring in ATR units makes 2013 and 2020 comparable.

**Significance comes from a daily block bootstrap, not a textbook t-test.**
Overlapping forward windows are heavily autocorrelated — a 96-bar forward return
sampled every bar reuses the same move 96 times — and intraday observations
cluster within a day. A naive t-stat on overlapping samples is inflated by
roughly sqrt(h) and will show you "significant" edges that are pure artifact.
Resampling whole days preserves both problems inside the resample, so the
resulting interval is honest.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..strategies.indicators import atr


@dataclass
class EdgeStat:
    name: str
    horizon: int
    n: int
    mean_atr: float          # mean forward return in ATR units
    ci_lo: float
    ci_hi: float
    p_value: float           # two-sided, vs a null of zero
    hit_rate: float          # fraction of observations with a positive signed move

    @property
    def significant(self) -> bool:
        return self.p_value < 0.05 and self.n >= 100

    def row(self) -> dict:
        return {
            "condition": self.name, "h_bars": self.horizon, "n": self.n,
            "mean_ATR": round(self.mean_atr, 4),
            "ci95": f"[{self.ci_lo:+.3f}, {self.ci_hi:+.3f}]",
            "p": round(self.p_value, 4), "hit%": round(100 * self.hit_rate, 1),
            "sig": "***" if self.significant else "",
        }


def forward_return_atr(df: pd.DataFrame, horizon: int, atr_series: pd.Series) -> pd.Series:
    """Forward close-to-close move over `horizon` bars, in units of ATR at the signal bar."""
    fwd = df["close"].shift(-horizon) - df["close"]
    return fwd / atr_series


def _bootstrap_mean(
    values: np.ndarray, day_ids: np.ndarray, n_boot: int, rng: np.random.Generator
) -> tuple[float, float, float]:
    """Block bootstrap by day. Returns (ci_lo, ci_hi, two-sided p vs zero)."""
    if len(values) == 0:
        return 0.0, 0.0, 1.0
    # collapse to per-day (sum, count) so each resample is O(n_days)
    uniq, inv = np.unique(day_ids, return_inverse=True)
    sums = np.bincount(inv, weights=values, minlength=len(uniq))
    counts = np.bincount(inv, minlength=len(uniq)).astype(float)
    n_days = len(uniq)
    if n_days < 20:
        return 0.0, 0.0, 1.0

    picks = rng.integers(0, n_days, size=(n_boot, n_days))
    boot_sums = sums[picks].sum(axis=1)
    boot_counts = counts[picks].sum(axis=1)
    boot_means = boot_sums / np.maximum(boot_counts, 1.0)

    lo, hi = np.percentile(boot_means, [2.5, 97.5])
    # p-value: how much of the bootstrap distribution sits on the other side of 0
    observed = values.mean()
    if observed >= 0:
        p = 2.0 * float((boot_means <= 0).mean())
    else:
        p = 2.0 * float((boot_means >= 0).mean())
    return float(lo), float(hi), min(1.0, p)


def measure(
    df: pd.DataFrame,
    mask: pd.Series,
    side: pd.Series | int,
    horizons: tuple[int, ...],
    name: str,
    atr_series: pd.Series,
    n_boot: int = 2000,
    seed: int = 3,
) -> list[EdgeStat]:
    """Mean signed forward return for the bars where `mask` is true.

    `side` is +1/-1 per bar (or a scalar) so that long and short setups can be
    pooled into a single directional statistic.
    """
    rng = np.random.default_rng(seed)
    out: list[EdgeStat] = []
    sides = pd.Series(side, index=df.index) if np.isscalar(side) else side
    day_ids_all = df.index.normalize().astype("int64").to_numpy()

    for h in horizons:
        fwd = forward_return_atr(df, h, atr_series) * sides
        sel = mask.fillna(False).to_numpy() & fwd.notna().to_numpy()
        vals = fwd.to_numpy()[sel]
        days = day_ids_all[sel]
        if len(vals) < 30:
            continue
        lo, hi, p = _bootstrap_mean(vals, days, n_boot, rng)
        out.append(EdgeStat(name, h, len(vals), float(vals.mean()), lo, hi, p,
                            float((vals > 0).mean())))
    return out


def scan(df: pd.DataFrame, horizons: tuple[int, ...] = (4, 16, 32, 96),
         n_boot: int = 2000) -> pd.DataFrame:
    """Run the full battery of structural tests. Returns a tidy results table."""
    from ..data.sessions import ASIA, prior_day_levels, session_range

    a = atr(df, 14)
    hours = pd.Series(df.index.hour, index=df.index)
    close, high, low = df["close"], df["high"], df["low"]
    results: list[EdgeStat] = []

    # ── 1. unconditional drift by hour ────────────────────────────────────
    for h in range(0, 24, 1):
        m = hours == h
        if m.sum() < 500:
            continue
        results += measure(df, m, 1, (4,), f"hour_{h:02d}_drift", a, n_boot)

    # ── 2. day-of-week drift ──────────────────────────────────────────────
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    for d in range(5):
        m = pd.Series(df.index.dayofweek == d, index=df.index) & (hours == 8)
        results += measure(df, m, 1, (32, 96), f"dow_{dow_names[d]}_from_08h", a, n_boot)

    # ── 3. momentum vs mean reversion, by lookback ────────────────────────
    for lb in (4, 16, 32, 96, 192):
        past = close - close.shift(lb)
        sign = np.sign(past).fillna(0.0)
        m = (sign != 0) & (hours >= 7) & (hours < 18)
        results += measure(df, m, sign, horizons, f"momentum_lb{lb}", a, n_boot)

    # ── 4. Asian-range break continuation (the LondonORB premise) ─────────
    rng_a = session_range(df, ASIA)
    hi_a, lo_a = rng_a["prev_sess_high"], rng_a["prev_sess_low"]
    in_london = (hours >= 7) & (hours < 12)
    broke_up = close > hi_a
    broke_dn = close < lo_a
    fresh_up = broke_up & ~broke_up.shift(1).fillna(False)
    fresh_dn = broke_dn & ~broke_dn.shift(1).fillna(False)
    brk = (fresh_up | fresh_dn) & in_london
    brk_side = pd.Series(np.where(fresh_up, 1.0, -1.0), index=df.index)
    results += measure(df, brk, brk_side, horizons, "asia_range_break_continuation", a, n_boot)

    # ── 5. prior-day-level sweep and reclaim (the SweepReversal premise) ──
    lv = prior_day_levels(df)
    pdh, pdl = lv["pdh"], lv["pdl"]
    in_sess = (hours >= 7) & (hours < 20)
    swept_up = (high > pdh) & (close < pdh)
    swept_dn = (low < pdl) & (close > pdl)
    sweep = (swept_up | swept_dn) & in_sess
    sweep_side = pd.Series(np.where(swept_up, -1.0, 1.0), index=df.index)
    results += measure(df, sweep, sweep_side, horizons, "pd_level_sweep_reversal", a, n_boot)

    # ── 6. overnight (Asian) drift — the classic carry-ish anomaly ────────
    asia_bars = (hours >= 23) | (hours < 7)
    results += measure(df, pd.Series(asia_bars, index=df.index), 1, (16, 32),
                       "asian_session_drift", a, n_boot)

    # ── 7. volatility regime: does momentum work better when vol expands? ─
    from ..strategies.indicators import rolling_percentile
    vp = rolling_percentile(a, 480)
    past32 = np.sign(close - close.shift(32)).fillna(0.0)
    for label, vmask in (("lowvol", vp < 0.35), ("highvol", vp > 0.65)):
        m = (past32 != 0) & vmask & (hours >= 7) & (hours < 18)
        results += measure(df, m, past32, (32, 96), f"momentum32_{label}", a, n_boot)

    # ── 8. NY-session continuation of the London move ─────────────────────
    london_move = close - close.shift(20)
    m = (hours == 13) & (london_move.abs() > 0.5 * a)
    results += measure(df, m, np.sign(london_move).fillna(0.0), (16, 32),
                       "ny_continues_london", a, n_boot)

    return pd.DataFrame([r.row() for r in results])
