"""Exit management: what to do with a position after it is open.

A system that only decides entries has handed the whole result to two numbers chosen at the
moment of least information. ema_cross wins 29% of the time at roughly 1:2 — on those numbers the
exit is where the money is, and until now nothing in this system touched a position after the fill.

Three things live here, all pure and all opt-in per strategy:

- **break-even**: once the trade is `trigger_r` times its own initial risk in front, move the stop
  to entry (plus an offset that covers the spread, or the "free trade" is still a small loss);
- **ATR trail**: keep the stop a multiple of ATR behind the price;
- **step trail**: keep it a fixed number of points behind, for symbols where ATR is noisy.

None of them can widen a stop — `risk/stops.py` refuses that whoever asks — so the worst a bad
setting here can do is take a position out early. That is a bounded mistake, unlike the other
direction.

`initial_stop` is the stop the position was opened with, not the one it has now: once a trail has
moved the stop, the distance from entry no longer says what was risked. The core reads it back
from the journal, so it survives a restart in the middle of a trade.
"""

from __future__ import annotations

from tradeapp.contracts import Position, Side


def _favourable_move(position: Position, price: float) -> float:
    """How far the trade is in front, in price. Negative while it is losing."""
    return price - position.price_open if position.side is Side.LONG else position.price_open - price


def break_even(
    position: Position,
    price: float,
    initial_stop: float,
    *,
    trigger_r: float = 1.0,
    offset_points: float = 0.0,
    point: float = 0.00001,
) -> float | None:
    """Entry (plus a small offset) once the trade is `trigger_r` of its own risk in front."""
    risk = abs(position.price_open - initial_stop)
    if risk <= 0 or trigger_r <= 0:
        return None
    if _favourable_move(position, price) < trigger_r * risk:
        return None
    offset = offset_points * point
    return position.price_open + offset if position.side is Side.LONG else position.price_open - offset


def atr_trail(position: Position, price: float, atr: float | None, *, multiple: float = 2.0) -> float | None:
    """A stop that follows the price by a multiple of ATR, and never leads it."""
    if not atr or atr <= 0 or multiple <= 0:
        return None
    return price - multiple * atr if position.side is Side.LONG else price + multiple * atr


def step_trail(position: Position, price: float, *, points: float, point: float = 0.00001) -> float | None:
    """A stop a fixed distance behind the price."""
    if points <= 0:
        return None
    distance = points * point
    return price - distance if position.side is Side.LONG else price + distance


def best_of(*candidates: float | None, side: Side) -> float | None:
    """The tightest of several proposals, which is the only one worth asking the broker for.

    A long wants the highest stop, a short the lowest. Anything looser than the position's current
    stop is refused later anyway, so proposing it would only fill the journal with rejections.
    """
    prices = [c for c in candidates if c is not None]
    if not prices:
        return None
    return max(prices) if side is Side.LONG else min(prices)
