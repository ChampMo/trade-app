"""Position sizing and exposure arithmetic. Pure functions, so they can be tested to the cent.

The money question is always the same: if this stop is hit, how much is lost? Everything else
(lot rounding, currency netting, open risk) follows from answering that one correctly.
"""

from __future__ import annotations

import math

from tradeapp.contracts import Position, Side, SymbolInfo


def money_at_risk_per_lot(stop_distance: float, sym: SymbolInfo) -> float:
    """Account-currency loss for one lot if price travels `stop_distance`.

    Uses the broker's own tick value, which is already expressed in the account currency, so this
    works for a USD account trading EURUSD and for a EUR account trading gold without a rate table.
    """
    if stop_distance <= 0:
        raise ValueError("stop_distance must be > 0")
    tick_size = sym.tick_size or sym.point
    if tick_size <= 0 or sym.tick_value <= 0:
        raise ValueError(f"{sym.symbol}: broker gave no usable tick_size/tick_value for sizing")
    return (stop_distance / tick_size) * sym.tick_value


def round_down_to_step(volume: float, step: float) -> float:
    """Always round down: rounding up would spend more risk than the limit allows."""
    if step <= 0:
        return volume
    steps = math.floor(round(volume / step, 9))
    # carry the step's own precision so 0.1+0.2 style noise never reaches the broker
    decimals = max(0, -math.floor(math.log10(step))) if step < 1 else 0
    return round(steps * step, decimals + 2)


def lots_for_risk(risk_amount: float, stop_distance: float, sym: SymbolInfo) -> float:
    """Largest whole number of lot steps whose loss at the stop stays within `risk_amount`."""
    if risk_amount <= 0:
        return 0.0
    per_lot = money_at_risk_per_lot(stop_distance, sym)
    return round_down_to_step(risk_amount / per_lot, sym.volume_step)


def position_risk(pos: Position, sym: SymbolInfo) -> float:
    """What this open position loses if its stop is hit. No stop means unbounded, so say so."""
    if pos.sl <= 0:
        raise ValueError(f"position {pos.ticket} has no stop at the broker; risk is unbounded (rule 03)")
    return money_at_risk_per_lot(abs(pos.price_open - pos.sl), sym) * pos.volume


def split_pair(symbol: str) -> tuple[str, str] | None:
    """EURUSD -> (EUR, USD). Handles broker suffixes like EURUSD.raw or EURUSD-ECN.

    Returns None for anything that is not a six-letter pair (indices, single-name CFDs), which the
    caller treats as "cannot net this", not as "no exposure".
    """
    core = "".join(ch for ch in symbol.upper() if ch.isalpha())
    for cut in (symbol.find("."), symbol.find("-"), symbol.find("_")):
        if cut > 0:
            core = "".join(ch for ch in symbol[:cut].upper() if ch.isalpha())
            break
    if len(core) != 6:
        return None
    return core[:3], core[3:]


def currency_units(symbol: str, side: Side) -> dict[str, int]:
    """Long EURUSD is long EUR and short USD. One unit each, so exposure can be netted."""
    pair = split_pair(symbol)
    if pair is None or side is Side.FLAT:
        return {}
    base, quote = pair
    sign = 1 if side is Side.LONG else -1
    return {base: sign, quote: -sign}


def net_currency_exposure(positions: list[Position], extra: dict[str, int] | None = None) -> dict[str, int]:
    """Net units per currency across open positions, optionally including one not yet placed."""
    totals: dict[str, int] = {}
    for pos in positions:
        for cur, units in currency_units(pos.symbol, pos.side).items():
            totals[cur] = totals.get(cur, 0) + units
    for cur, units in (extra or {}).items():
        totals[cur] = totals.get(cur, 0) + units
    return {cur: units for cur, units in totals.items() if units != 0}
