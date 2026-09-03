"""Moving a stop after the fill: the one adjustment allowed to an open position (P-exits).

Rule 03 says every order carries a stop at the broker. This module is about the only change that
may be made to it afterwards, and it exists because of one asymmetry:

**a stop may move towards the price, never away from it.**

Tightening a stop can only ever reduce what the position can lose. Widening one turns a bounded
loss into a larger bounded loss, on the say-so of a strategy that is currently *wrong* about the
market — which is precisely the moment not to be trusting it. So the direction is checked here,
not left to the caller, and there is no parameter that disables the check.

Everything in this file is a pure function of the position, the quote and the symbol, so the same
arithmetic runs in a backtest and against a live broker (rule 04).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tradeapp.contracts import Position, Side, SymbolInfo, Tick


class StopRefusal(StrEnum):
    NOT_AN_IMPROVEMENT = "not_an_improvement"  # the same price, or further away
    WOULD_WIDEN_RISK = "would_widen_risk"  # away from the market: refused, always
    WRONG_SIDE_OF_PRICE = "wrong_side_of_price"  # a long's stop cannot sit above the bid
    TOO_CLOSE_TO_PRICE = "too_close_to_price"  # inside the broker's own minimum distance
    NO_STOP_TO_MOVE = "no_stop_to_move"  # rule 03 was already broken; reconcile owns that


@dataclass(frozen=True)
class StopVerdict:
    ok: bool
    price: float | None = None
    reason: StopRefusal | None = None
    detail: str = ""

    @property
    def refused(self) -> bool:
        return not self.ok


def min_distance(sym: SymbolInfo, buffer_points: int = 5) -> float:
    """What the broker will accept: its own stops level, plus the spread, plus a little room."""
    return (sym.stops_level_points + sym.spread_points + buffer_points) * sym.point


def validate_move(
    position: Position,
    new_stop: float,
    sym: SymbolInfo,
    tick: Tick,
    *,
    buffer_points: int = 5,
) -> StopVerdict:
    """Decide whether this stop may replace the one the position already has."""
    if position.sl <= 0:
        return StopVerdict(False, reason=StopRefusal.NO_STOP_TO_MOVE, detail="position has no stop at the broker")

    new_stop = round(new_stop, sym.digits)
    long = position.side is Side.LONG
    # The price this position would be closed at, which is the side the stop has to clear.
    close_price = tick.bid if long else tick.ask

    if new_stop == position.sl:
        return StopVerdict(False, reason=StopRefusal.NOT_AN_IMPROVEMENT, detail="the stop is already there")

    improving = new_stop > position.sl if long else new_stop < position.sl
    if not improving:
        return StopVerdict(
            False,
            reason=StopRefusal.WOULD_WIDEN_RISK,
            detail=f"{position.sl} -> {new_stop} moves the stop away from the price",
        )

    on_the_wrong_side = new_stop >= close_price if long else new_stop <= close_price
    if on_the_wrong_side:
        return StopVerdict(
            False,
            reason=StopRefusal.WRONG_SIDE_OF_PRICE,
            detail=f"{new_stop} is not on the losing side of {close_price}",
        )

    if abs(close_price - new_stop) < min_distance(sym, buffer_points):
        return StopVerdict(
            False,
            reason=StopRefusal.TOO_CLOSE_TO_PRICE,
            detail=f"{abs(close_price - new_stop) / sym.point:.0f} points from price, the broker needs "
            f"{min_distance(sym, buffer_points) / sym.point:.0f}",
        )

    return StopVerdict(True, price=new_stop, detail=f"{position.sl} -> {new_stop}")


def risk_now(position: Position, stop: float | None = None) -> float:
    """Distance from entry to the stop, in price. Negative once the stop is past the entry."""
    stop = position.sl if stop is None else stop
    return position.price_open - stop if position.side is Side.LONG else stop - position.price_open
