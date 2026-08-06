"""Indicator primitives.

All functions are vectorised and strictly causal: the value at bar *i* uses only
bars <= *i*. The backtest engine additionally executes on the *next* bar's open,
so a signal computed from bar i's close is never filled at bar i's price.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR — the smoothing MT4/MT5 and most gold literature use."""
    tr = true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.fillna(50.0).where(avg_gain.notna(), np.nan)


def donchian(df: pd.DataFrame, period: int) -> pd.DataFrame:
    """Highest high / lowest low over the *previous* `period` bars.

    Shifted by one so the current bar's own extremes are excluded — otherwise a
    breakout test against the channel is trivially self-satisfying.
    """
    out = pd.DataFrame(index=df.index)
    out["dc_high"] = df["high"].rolling(period, min_periods=period).max().shift(1)
    out["dc_low"] = df["low"].rolling(period, min_periods=period).min().shift(1)
    out["dc_mid"] = (out["dc_high"] + out["dc_low"]) / 2.0
    return out


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index — used here purely as a trend/chop gate."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(df)
    alpha = 1.0 / period
    atr_ = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    plus_di = 100.0 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=alpha, adjust=False, min_periods=period
    ).mean() / atr_
    minus_di = 100.0 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=alpha, adjust=False, min_periods=period
    ).mean() / atr_
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()


def rolling_percentile(series: pd.Series, period: int) -> pd.Series:
    """Where does the current value sit within its own trailing window? (0..1)

    Used as a volatility-regime gate: breakout systems want an expanding regime,
    fades want a compressed one.
    """
    return series.rolling(period, min_periods=period).rank(pct=True)


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate to a higher timeframe (e.g. '4h', '1D')."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    return df.resample(rule, label="right", closed="right").agg(agg).dropna(subset=["open"])


def align_higher_tf(higher: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    """Project a higher-timeframe series onto lower-timeframe bars, causally.

    The HTF bar labelled at time T only *closes* at T, so it is shifted one HTF
    bar forward before being forward-filled. Without this shift you leak the
    close of an H4 candle into the M15 bars that formed it — the single most
    common source of fake backtest edge in multi-timeframe systems.
    """
    return higher.shift(1).reindex(target_index, method="ffill")
