"""Strategy registry."""

from .asia_break_momentum import AsiaBreakMomentum
from .asian_fade import AsianRangeFade
from .base import Strategy
from .donchian import DonchianBreakout
from .london_orb import LondonORB
from .sweep_reversal import SweepReversal
from .trend_pullback import TrendPullback

REGISTRY: dict[str, type[Strategy]] = {
    "london_orb": LondonORB,
    "asian_fade": AsianRangeFade,
    "trend_pullback": TrendPullback,
    "donchian": DonchianBreakout,
    "sweep_reversal": SweepReversal,
    "asia_break_momentum": AsiaBreakMomentum,
}

# Parameter grids for walk-forward optimisation. Kept deliberately small — a
# grid with thousands of points will find something that worked in every training
# window and nothing that works in any test window.
PARAM_GRIDS: dict[str, dict] = {
    "london_orb": {
        "stop_atr_mult": [1.0, 1.5, 2.0],
        "target_r": [1.5, 2.0, 3.0],
        "max_range_atr": [4.0, 6.0],
    },
    "asian_fade": {
        "stop_atr_mult": [0.75, 1.0, 1.5],
        "vol_percentile_max": [0.5, 0.6, 0.75],
    },
    "trend_pullback": {
        "stop_atr_mult": [1.5, 2.0, 3.0],
        "trail_atr_mult": [2.5, 3.0, 4.0],
        "adx_min": [15.0, 18.0, 25.0],
    },
    "donchian": {
        "channel": [24, 48, 96],
        "stop_atr_mult": [1.5, 2.0, 3.0],
        "vol_percentile_min": [0.45, 0.55],
    },
    "sweep_reversal": {
        "stop_buffer_atr": [0.2, 0.35, 0.5],
        "target_r": [1.5, 2.0, 3.0],
        "confirm_bars": [2, 3, 5],
    },
    "asia_break_momentum": {
        "stop_atr_mult": [2.5, 5.0, 8.0],
        "trail_atr_mult": [4.0, 6.0, None],
        "max_bars": [16, 24, 48],
        "vol_percentile_min": [0.0, 0.5, 0.65],
    },
}

__all__ = [
    "Strategy", "AsiaBreakMomentum", "LondonORB", "AsianRangeFade", "TrendPullback",
    "DonchianBreakout", "SweepReversal", "REGISTRY", "PARAM_GRIDS",
]
