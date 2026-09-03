"""Correlation between symbols, as a refusal only (P1-04b).

Currency netting already stops three EUR longs from counting as three separate risks. What it
misses is the pair that shares no currency and still moves together: EURUSD and GBPJPY have no
common leg, and on a risk-off day they are one trade wearing two hats.

Three rules keep this honest:

- the numbers are **coarse and conservative**, taken from long-run daily-return behaviour of the
  majors. They are not measured from this account's data, and they are not meant to be precise;
- they may only ever **refuse**. Nothing here can enlarge a position or justify one, which is the
  same rule the AI layer lives under (D6);
- a pair that is not in the table counts as **uncorrelated**, because inventing a correlation is
  worse than missing one. Same-currency netting is still there underneath as the safety net.

Correlations drift, and a number that was 0.9 in a crisis can be 0.3 a year later. That is exactly
why the limit is a whole-position count rather than a portfolio-variance calculation: the answer
only has to be right enough to stop the third copy of the same bet.
"""

from __future__ import annotations

from tradeapp.contracts import Position, Side
from tradeapp.risk.sizing import split_pair

# Signed: positive means the two rise together, negative means one rises as the other falls.
# Keyed by the six-letter core of the symbol, so broker suffixes (EURUSD.raw) do not matter.
CORRELATIONS: dict[frozenset[str], float] = {
    frozenset({"EURUSD", "GBPUSD"}): 0.85,
    frozenset({"EURUSD", "AUDUSD"}): 0.70,
    frozenset({"EURUSD", "NZDUSD"}): 0.65,
    frozenset({"EURUSD", "USDCHF"}): -0.90,
    frozenset({"EURUSD", "USDCAD"}): -0.65,
    frozenset({"EURUSD", "XAUUSD"}): 0.55,
    frozenset({"GBPUSD", "AUDUSD"}): 0.65,
    frozenset({"GBPUSD", "USDCHF"}): -0.75,
    frozenset({"AUDUSD", "NZDUSD"}): 0.90,
    frozenset({"AUDUSD", "USDCAD"}): -0.60,
    frozenset({"AUDUSD", "XAUUSD"}): 0.60,
    frozenset({"USDCHF", "USDCAD"}): 0.60,
    frozenset({"USDJPY", "USDCHF"}): 0.55,
    frozenset({"EURJPY", "GBPJPY"}): 0.85,
    frozenset({"EURUSD", "EURJPY"}): 0.55,
    frozenset({"GBPUSD", "GBPJPY"}): 0.60,
    frozenset({"XAUUSD", "USDCHF"}): -0.55,
}


def _core(symbol: str) -> str:
    pair = split_pair(symbol)
    return f"{pair[0]}{pair[1]}" if pair else symbol.upper()


def correlation(a: str, b: str) -> float:
    """1.0 for the same symbol, the table value for a known pair, 0.0 for anything else."""
    ca, cb = _core(a), _core(b)
    if ca == cb:
        return 1.0
    return CORRELATIONS.get(frozenset({ca, cb}), 0.0)


def _direction(side: Side) -> int:
    return 1 if side is Side.LONG else -1 if side is Side.SHORT else 0


def correlated_units(
    positions: list[Position],
    symbol: str,
    side: Side,
    *,
    floor: float = 0.5,
) -> tuple[float, list[tuple[str, float]]]:
    """How many copies of this bet would be open, counting the new one as 1.0.

    A position correlated 0.85 in the same direction adds 0.85. One correlated -0.90 in the
    *opposite* direction is the same bet too (short USDCHF is long EURUSD in all but name), so it
    also adds. A genuinely opposing position **subtracts**, because it hedges.

    Correlations weaker than `floor` are ignored: below that the number is noise, and counting
    noise would refuse trades for no reason.
    """
    wanted = _direction(side)
    if wanted == 0:
        return 0.0, []
    total, contributors = 1.0, []
    for pos in positions:
        rho = correlation(symbol, pos.symbol)
        if abs(rho) < floor:
            continue
        contribution = rho * _direction(pos.side) * wanted
        if contribution == 0:
            continue
        total += contribution
        contributors.append((pos.symbol, round(contribution, 2)))
    return round(total, 3), contributors
