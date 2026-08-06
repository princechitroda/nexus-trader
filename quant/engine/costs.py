"""Instrument spec and transaction costs for XAUUSD.

Convention used everywhere in this package: **the OHLC in your dataframe is the
BID.** That is what MT5 exports and what almost every free gold feed gives you.
The ask is `bid + spread`, so:

  * a long pays the spread on entry (buys at ask, later sells at bid)
  * a short's stop is triggered by the *ask*, i.e. it fires while the visible bid
    is still `spread` away

That second point is not pedantry. Gold spreads widen to 40-60 cents around
rollover and news, and a short stop that looks untouched on a bid chart is
routinely filled in reality. Ignoring it is worth several percent a year of
imaginary profit.

Defaults below are deliberately pessimistic — roughly a mid-tier prop firm's
raw-spread account on gold, not an idealised ECN.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Instrument:
    """Contract spec. Gold: 1.00 lot = 100 oz, so a $1 move = $100 per lot."""

    symbol: str = "XAUUSD"
    contract_size: float = 100.0
    lot_step: float = 0.01
    min_lot: float = 0.01
    max_lot: float = 50.0
    digits: int = 2

    def round_lots(self, lots: float) -> float:
        if lots <= 0:
            return 0.0
        stepped = round(lots / self.lot_step) * self.lot_step
        stepped = max(self.min_lot, min(self.max_lot, stepped))
        return round(stepped, 8)

    def pnl(self, side: int, entry: float, exit_: float, lots: float) -> float:
        """Gross P&L in account currency, before commission."""
        return side * (exit_ - entry) * self.contract_size * lots


@dataclass
class CostModel:
    """Spread / commission / slippage.

    `spread` and the slippage figures are in **price units** — for gold, 0.25
    means 25 cents, which is a typical raw-spread quote in liquid hours.
    """

    spread: float = 0.25
    commission_per_lot: float = 7.0      # USD, round turn, per 1.00 lot
    slippage_entry: float = 0.03         # market orders in liquid conditions
    slippage_stop: float = 0.12          # stops are worse: you are the liquidity taker
    # Hour-of-day (UTC) multipliers on the spread. Rollover (21:00-23:00 UTC) and
    # the thin pre-Asia hours are where gold spreads blow out.
    spread_by_hour: dict[int, float] = field(
        default_factory=lambda: {
            21: 4.0, 22: 6.0, 23: 3.0, 0: 1.6, 1: 1.4, 2: 1.3, 3: 1.3, 4: 1.2, 5: 1.2,
        }
    )

    def spread_at(self, hour: int) -> float:
        return self.spread * self.spread_by_hour.get(hour, 1.0)

    def commission(self, lots: float) -> float:
        return self.commission_per_lot * lots


@dataclass
class ZeroCosts(CostModel):
    """Frictionless baseline.

    Only useful for one thing: measuring how much of a strategy's edge is real
    versus how much is eaten by costs. If a system's profit factor collapses
    from 1.8 to 0.9 when you switch from this to the realistic model, it was
    never a strategy — it was a spread-harvesting artifact.
    """

    spread: float = 0.0
    commission_per_lot: float = 0.0
    slippage_entry: float = 0.0
    slippage_stop: float = 0.0
    spread_by_hour: dict[int, float] = field(default_factory=dict)
