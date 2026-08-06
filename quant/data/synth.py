"""Synthetic XAUUSD generator — for *validating the machinery*, not the strategies.

Read this before you read any number this package prints on synthetic data.

Synthetic data cannot tell you whether a strategy is profitable on real gold. It
can tell you whether your backtester is honest, which is a prerequisite. Two
tests, and the package runs both in `--selftest`:

**Null test.** `generate(structured=False)` produces a price path with realistic
volatility clustering, session volatility seasonality, weekend gaps and news-like
jumps — but with *no directional structure whatsoever*. Every strategy here must
lose money on it, by roughly the cost of trading. If any of them turns a profit,
the backtester has a look-ahead bug or a cost error. This catches the single most
expensive class of mistake in retail algo trading.

**Positive control.** `generate(structured=True)` injects a genuine, known
session-continuation effect: after price leaves the Asian range, London-hours
drift is biased in the direction of the break. `LondonORB` must find it. If a
known edge is present and the engine cannot detect it, the engine is broken in
the other direction — too pessimistic to be useful.

A backtester that passes both tests is trustworthy enough to point at real data.
One that has never been tested this way is a random number generator with a
progress bar.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# Relative volatility by UTC hour. Gold is quiet in Asia, wakes at the London
# open, peaks through the London/NY overlap and the US data window, then fades.
_HOUR_VOL = np.array([
    0.55, 0.50, 0.48, 0.50, 0.55, 0.60, 0.70, 1.05,  # 00-07
    1.15, 1.10, 1.00, 0.95, 1.30, 1.55, 1.45, 1.25,  # 08-15
    1.10, 1.00, 0.90, 0.80, 0.70, 0.60, 0.55, 0.55,  # 16-23
])


def _market_open(idx: pd.DatetimeIndex) -> np.ndarray:
    """Gold trades ~23h/day, Sunday 22:00 UTC to Friday 21:00 UTC."""
    dow = idx.dayofweek.to_numpy()
    hour = idx.hour.to_numpy()
    closed = (
        ((dow == 4) & (hour >= 21))          # Friday evening
        | (dow == 5)                          # Saturday
        | ((dow == 6) & (hour < 22))          # Sunday until the open
        | ((hour == 21) & (dow != 6))         # daily rollover break
    )
    return ~closed


def generate(
    start: str = "2021-01-01",
    end: str = "2024-12-31",
    timeframe_min: int = 15,
    start_price: float = 1800.0,
    annual_vol: float = 0.16,
    structured: bool = False,
    structure_strength: float = 0.35,
    jump_prob: float = 0.0009,
    jump_scale: float = 5.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic XAUUSD OHLC bars.

    `structured=False` → no exploitable directional structure (null test).
    `structured=True`  → a real Asian-range-break continuation effect is injected
                         during London hours (positive control).
    """
    rng = np.random.default_rng(seed)
    full = pd.date_range(start, end, freq=f"{timeframe_min}min", tz=None)
    idx = full[_market_open(full)]
    n = len(idx)
    if n == 0:
        raise ValueError("no open bars in the requested range")

    bars_per_year = (365.25 * 24 * 60) / timeframe_min * (23 / 24) * (5 / 7)
    base_sigma = annual_vol / np.sqrt(bars_per_year)

    # --- GARCH(1,1)-style volatility clustering ---------------------------
    omega, alpha, beta = 0.05, 0.09, 0.88
    v = np.empty(n)
    v[0] = 1.0
    shocks = rng.standard_normal(n)
    for t in range(1, n):
        v[t] = omega + alpha * shocks[t - 1] ** 2 + beta * v[t - 1]
    vol_factor = np.sqrt(np.clip(v, 0.15, 12.0))

    hours = idx.hour.to_numpy()
    season = _HOUR_VOL[hours]
    sigma = base_sigma * vol_factor * season

    # --- news-like jumps ---------------------------------------------------
    jumps = np.zeros(n)
    hit = rng.random(n) < jump_prob
    # concentrate jumps in the US data window, as with real gold
    hit &= (hours >= 12) & (hours <= 15) | (rng.random(n) < 0.2)
    jumps[hit] = rng.standard_normal(hit.sum()) * base_sigma * jump_scale

    substeps = 12
    log_px = np.empty(n)
    highs = np.empty(n)
    lows = np.empty(n)
    opens = np.empty(n)
    closes = np.empty(n)

    day = idx.normalize().to_numpy()
    in_asia = (hours >= 23) | (hours < 7)
    in_london = (hours >= 7) & (hours < 16)

    cur = np.log(start_price)
    asia_hi, asia_lo = -np.inf, np.inf
    asia_hi_done, asia_lo_done = np.nan, np.nan
    was_asia = False
    sub_noise = rng.standard_normal((n, substeps)) / np.sqrt(substeps)

    for t in range(n):
        # Track the running Asian range, and freeze it the moment the session ends.
        if in_asia[t]:
            if not was_asia:
                asia_hi, asia_lo = cur, cur
            asia_hi = max(asia_hi, cur)
            asia_lo = min(asia_lo, cur)
            was_asia = True
        elif was_asia:
            asia_hi_done, asia_lo_done = asia_hi, asia_lo
            was_asia = False

        drift = 0.0
        if structured and in_london[t] and not np.isnan(asia_hi_done):
            if cur > asia_hi_done:
                drift = structure_strength * sigma[t]
            elif cur < asia_lo_done:
                drift = -structure_strength * sigma[t]

        path = cur + np.cumsum(sub_noise[t] * sigma[t]) + drift + (jumps[t] if t else 0.0)
        opens[t] = cur
        highs[t] = max(cur, path.max())
        lows[t] = min(cur, path.min())
        closes[t] = path[-1]
        cur = path[-1]
        log_px[t] = cur

    df = pd.DataFrame(
        {
            "open": np.exp(opens),
            "high": np.exp(highs),
            "low": np.exp(lows),
            "close": np.exp(closes),
            "volume": (sigma / base_sigma * 1000).round(),
        },
        index=idx,
    )
    # weekend gaps: nudge the Sunday reopen away from the Friday close
    reopen = np.flatnonzero(np.diff(idx.dayofweek.to_numpy()) < -1) + 1
    for r in reopen:
        gap = rng.standard_normal() * base_sigma * 8
        df.iloc[r:, :4] *= np.exp(gap)

    df.index.name = "time"
    return df.round(2)
