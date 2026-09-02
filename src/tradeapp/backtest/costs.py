"""What a trade actually costs.

Backtests flatter themselves by forgetting this. The defaults here are not textbook numbers, they
are what was measured on the real XM demo account (D18): EURUSD quotes a 20-point spread, so a
0.01-lot round trip costs 0.20 USD before anything else happens. A strategy that only wins by less
than its costs is not a strategy.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    spread_points: int = 20  # measured on XM EURUSD (D18); overridden per bar when data carries it
    use_bar_spread: bool = True  # prefer the spread recorded with each bar over the flat default
    news_spread_multiplier: float = 3.0  # applied to bars flagged as high-impact
    slippage_points: float = 0.3  # against us on entry and exit
    commission_per_lot_round_trip: float = 0.0  # XM Standard is spread-only; Raw accounts are not
    swap_long_per_lot_per_night: float = 0.0
    swap_short_per_lot_per_night: float = 0.0

    def spread_for(self, bar_spread_points: int, news: bool = False) -> float:
        base = float(bar_spread_points if (self.use_bar_spread and bar_spread_points > 0) else self.spread_points)
        return base * (self.news_spread_multiplier if news else 1.0)


ZERO_COSTS = CostModel(
    spread_points=0,
    use_bar_spread=False,
    slippage_points=0.0,
    commission_per_lot_round_trip=0.0,
)
"""For tests that check mechanics rather than economics. Never use this to judge a strategy."""
