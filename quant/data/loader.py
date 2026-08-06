"""Load XAUUSD OHLC data from the formats you are realistically going to have.

Supported without configuration:
  * **MT5 bar export** (Tools → History Centre / "Save as" from a chart):
    tab- or comma-separated with `<DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> ...`
  * **Dukascopy CSV** — `Gmt time,Open,High,Low,Close,Volume`, dd.mm.yyyy timestamps
  * **generic CSV** — any file with a recognisable time column plus OHLC

The loader normalises everything to: a UTC `DatetimeIndex`, sorted, deduplicated,
columns `open/high/low/close` (+ `volume` when present).

One thing to check before trusting any result: **which broker's data is this, and
what timezone are its bars stamped in?** MT5 exports are in *broker server time*,
which for most gold brokers is UTC+2/+3 with DST. Every session-based strategy in
this package keys off UTC hours, so feeding it server-time bars silently shifts
the London open by two or three hours. Use `server_tz_offset` to correct it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

_OHLC = ["open", "high", "low", "close"]


def _find_col(cols: list[str], *candidates: str) -> str | None:
    lowered = {c.lower().strip().strip("<>"): c for c in cols}
    for cand in candidates:
        if cand in lowered:
            return lowered[cand]
    for cand in candidates:
        for key, orig in lowered.items():
            if cand in key:
                return orig
    return None


def load_csv(
    path: str | Path,
    server_tz_offset: float = 0.0,
    timeframe: str | None = None,
) -> pd.DataFrame:
    """Load and normalise an OHLC file.

    `server_tz_offset` is the broker server's offset from UTC in hours. Pass e.g.
    `3.0` for a UTC+3 server and the timestamps will be shifted back to UTC.
    `timeframe` optionally resamples (e.g. '15min') after loading.
    """
    path = Path(path)
    sep = "\t" if path.suffix.lower() in {".tsv", ".txt"} else None
    df = pd.read_csv(path, sep=sep, engine="python")
    df.columns = [str(c).strip() for c in df.columns]
    cols = list(df.columns)

    date_col = _find_col(cols, "date", "gmt time", "time", "datetime", "timestamp")
    time_col = _find_col(cols, "time") if date_col and _find_col(cols, "date") else None
    if date_col is None:
        raise ValueError(f"no time column found in {path.name}; columns were {cols}")

    if time_col and time_col != date_col:
        stamp = df[date_col].astype(str).str.strip() + " " + df[time_col].astype(str).str.strip()
    else:
        stamp = df[date_col].astype(str).str.strip()

    # dd.mm.yyyy (Dukascopy / MT5 European exports) needs dayfirst; ISO does not.
    sample = stamp.iloc[0] if len(stamp) else ""
    dayfirst = "." in sample.split(" ")[0]
    idx = pd.to_datetime(stamp, dayfirst=dayfirst, format="mixed", utc=False, errors="coerce")
    if idx.isna().mean() > 0.02:
        raise ValueError(f"could not parse timestamps in {path.name}; sample: {sample!r}")

    out = pd.DataFrame(index=pd.DatetimeIndex(idx))
    for name in _OHLC:
        col = _find_col(cols, name)
        if col is None:
            raise ValueError(f"missing '{name}' column in {path.name}; columns were {cols}")
        out[name] = pd.to_numeric(df[col], errors="coerce").to_numpy()

    vol_col = _find_col(cols, "volume", "tickvol", "vol")
    if vol_col:
        out["volume"] = pd.to_numeric(df[vol_col], errors="coerce").to_numpy()

    if server_tz_offset:
        out.index = out.index - pd.Timedelta(hours=server_tz_offset)

    out = out.dropna(subset=_OHLC)
    out = out[~out.index.duplicated(keep="first")].sort_index()
    if timeframe:
        out = resample(out, timeframe)
    validate(out)
    return out


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    return df.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["open"])


def validate(df: pd.DataFrame, verbose: bool = False) -> dict:
    """Sanity checks. Bad data produces confident, wrong backtests."""
    issues: dict = {}
    if df.empty:
        raise ValueError("empty dataframe")

    bad_hl = int((df["high"] < df["low"]).sum())
    bad_range = int(((df["high"] < df[["open", "close"]].max(axis=1)) |
                     (df["low"] > df[["open", "close"]].min(axis=1))).sum())
    flat = int((df["high"] == df["low"]).sum())
    if bad_hl:
        issues["high_below_low"] = bad_hl
    if bad_range:
        issues["ohlc_inconsistent"] = bad_range
    if flat:
        issues["zero_range_bars"] = flat

    # Bars per weekday hour — a sanity check that the timezone is what you think.
    span_days = (df.index[-1] - df.index[0]).days
    issues["bars"] = len(df)
    issues["span_days"] = span_days
    issues["start"] = str(df.index[0])
    issues["end"] = str(df.index[-1])

    gaps = df.index.to_series().diff().dropna()
    if len(gaps):
        typical = gaps.median()
        big = int((gaps > typical * 20).sum())
        issues["median_bar_spacing"] = str(typical)
        issues["large_gaps"] = big

    if verbose:
        for k, v in issues.items():
            print(f"  {k}: {v}")
    return issues
