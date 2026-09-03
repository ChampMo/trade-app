"""Price structure: swings, the trend they describe, and supply/demand zones.

Pure functions over a list of closed bars, so the same arithmetic runs in a backtest and live and
can be tested to the point. Everything here is deliberately *simple* — a swing is a fractal, a
trend is the last two swings, a zone is the base a strong move left from — because a definition
that needs judgement cannot be backtested and one that needs ten parameters will fit anything.

The one thing every function respects: nothing is known before it could have been known. A swing
high needs `right` bars after it to be confirmed, so it is dated to the bar that confirmed it; a
zone is born on the bar that finished the impulse, not on the base it points back to. A strategy
that reads these on a closed bar sees exactly what a person watching the chart would have seen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from tradeapp.contracts import Bar


@dataclass(frozen=True)
class Swing:
    index: int  # the bar that is the extreme
    confirmed_at: int  # the bar on which it became known (index + right)
    price: float
    kind: str  # "high" | "low"
    time_utc: datetime


def swings(bars: Sequence[Bar], left: int = 3, right: int = 3) -> list[Swing]:
    """Fractal swing points. A bar is a swing high if no bar within `left` before or `right` after
    is higher; a swing low is the mirror. The last `right` bars can never be swings yet."""
    # Ties are ordinary on a five-digit chart: two bars printing the same high at a round level.
    # The swing is the *last* bar of such a plateau — at least as high as everything before it,
    # strictly higher than everything after — so the one that price actually turned from counts,
    # and a plateau never yields two swings.
    out: list[Swing] = []
    n = len(bars)
    for i in range(left, n - right):
        before = bars[i - left : i]
        after = bars[i + 1 : i + right + 1]
        hi, lo = bars[i].high, bars[i].low
        if hi >= max(b.high for b in before) and hi > max(b.high for b in after):
            out.append(Swing(i, i + right, hi, "high", bars[i].time_utc))
        if lo <= min(b.low for b in before) and lo < min(b.low for b in after):
            out.append(Swing(i, i + right, lo, "low", bars[i].time_utc))
    return out


def trend(swing_points: Sequence[Swing]) -> str:
    """'up' on a higher high *and* a higher low, 'down' on the mirror, 'flat' otherwise.

    Two of each, no more: a trend read from the last two swings turns when the structure turns,
    which is the whole point of reading structure instead of a moving average.
    """
    highs = [s.price for s in swing_points if s.kind == "high"][-2:]
    lows = [s.price for s in swing_points if s.kind == "low"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return "flat"
    if highs[1] > highs[0] and lows[1] > lows[0]:
        return "up"
    if highs[1] < highs[0] and lows[1] < lows[0]:
        return "down"
    return "flat"


@dataclass(frozen=True)
class Zone:
    kind: str  # "demand" (price left it upwards) | "supply" (downwards)
    low: float
    high: float
    base_index: int  # the bar the base was
    born_index: int  # the bar the impulse finished on: when the zone became known
    born_utc: datetime
    broken: bool = False  # a close beyond the far edge; the zone failed
    touches: int = 0  # times price came back into it after it was born

    @property
    def height(self) -> float:
        return self.high - self.low

    @property
    def fresh(self) -> bool:
        return not self.broken and self.touches == 0

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high


def zones(
    bars: Sequence[Bar],
    atr: Sequence[float | None],
    *,
    impulse_mult: float = 2.0,
    within: int = 3,
    keep: int = 12,
    base_max_atr: float = 1.0,
) -> list[Zone]:
    """Supply and demand zones: the bar a strong move left from.

    A demand zone is bar `i` when, within the next `within` bars, price closes at least
    `impulse_mult` ATRs above its high. The zone is that bar's high-low. Supply is the mirror.
    ATR is the value *at bar i*, so a zone found later still measures the impulse against the
    volatility of its own time.

    A base is a *pause*: a bar no taller than `base_max_atr` ATRs. Without that rule the first
    candle of the impulse itself qualifies as the base of the move it started, and the zone drawn
    from it is a third of the rally rather than the shelf the rally left from.

    Then the zone's life is replayed forward: a close beyond the far edge breaks it; a bar that
    trades back into it is a touch. `keep` is how many of the most recent zones to return, most
    recent last.
    """
    found: list[Zone] = []
    n = len(bars)
    for i in range(n - 1):
        a = atr[i] if i < len(atr) else None
        if not a or a <= 0:
            continue
        if bars[i].high - bars[i].low > base_max_atr * a:
            continue
        end = min(n, i + 1 + within)
        for j in range(i + 1, end):
            if bars[j].close - bars[i].high >= impulse_mult * a:
                found.append(Zone("demand", bars[i].low, bars[i].high, i, j, bars[j].time_utc))
                break
            if bars[i].low - bars[j].close >= impulse_mult * a:
                found.append(Zone("supply", bars[i].low, bars[i].high, i, j, bars[j].time_utc))
                break

    # Several consecutive bars can all sit `impulse_mult` ATRs below the same impulse close. The
    # base is the one the move actually left from — the last of them — so of the candidates that
    # share an impulse bar, keep only the latest base.
    latest: dict[tuple[str, int], Zone] = {}
    for z in found:
        key = (z.kind, z.born_index)
        if key not in latest or z.base_index > latest[key].base_index:
            latest[key] = z
    found = list(latest.values())

    lived: list[Zone] = []
    for z in found:
        broken, touches = False, 0
        for k in range(z.born_index + 1, n):
            b = bars[k]
            if z.kind == "demand":
                if b.close < z.low:
                    broken = True
                    break
                if b.low <= z.high:
                    touches += 1
            else:
                if b.close > z.high:
                    broken = True
                    break
                if b.high >= z.low:
                    touches += 1
        lived.append(Zone(z.kind, z.low, z.high, z.base_index, z.born_index, z.born_utc, broken, touches))
    lived.sort(key=lambda z: z.born_index)
    return lived[-keep:]


def nearest(zone_list: Sequence[Zone], price: float, kind: str, *, unbroken_only: bool = True) -> Zone | None:
    """The closest zone of a kind to the price: the demand below it, or the supply above it."""
    candidates = [z for z in zone_list if z.kind == kind and (not z.broken or not unbroken_only)]
    if kind == "demand":
        candidates = [z for z in candidates if z.high <= price or z.contains(price)]
        return max(candidates, key=lambda z: z.high, default=None)
    candidates = [z for z in candidates if z.low >= price or z.contains(price)]
    return min(candidates, key=lambda z: z.low, default=None)
