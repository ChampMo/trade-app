"""Zone MTF: enter on M15 inside a supply/demand zone that the H1 chart drew.

The idea the owner proposed, written so that a backtest can argue with it. Three layers:

- **context, on the higher timeframe**: the trend from the last two swings, and the zones that
  strong moves left behind (`structure.py`). Both read through `ctx.higher()`, which never shows
  a bar that has not closed (D30).
- **trigger, on this timeframe**: price trades into a fresh zone in the trend's direction and the
  bar rejects it - closes back out, in the top (or bottom) part of its own range.
- **risk from the structure, not from an indicator**: the stop sits beyond the far edge of the
  zone plus one spread, because if price gets there the zone was wrong, not merely noisy; the
  target is the next opposing zone, and a trade whose target is nearer than `min_rr` stops is
  refused before it exists.

Three filters exist purely because of the 20-point spread this account pays (D18): the zone has
to be taller than `zone_min_spread_mult` spreads or there is nowhere for a profit to be, entries
only happen in the session windows where an M15 bar is worth the cost, and the spread itself is
checked on every bar. Exits are the shared helpers and are off by default so the entry can be
measured on its own first.
"""

from __future__ import annotations

from tradeapp import indicators
from tradeapp.contracts import TF, Intent, Side
from tradeapp.exits import atr_trail, best_of, break_even
from tradeapp.strategies import register
from tradeapp.structure import nearest, swings, trend, zones


@register
class ZoneMTF:
    id = "zone_mtf"
    symbols = ["EURUSD"]
    timeframe = TF.M15
    state = "research"
    # A candidate under research is not registered by the loop unless named with --strategy or
    # attached on the Markets page. Adding a file to this package must not add a trader to the
    # owner's demo account on the next restart (D31).
    auto_trade = False

    def __init__(
        self,
        higher: str = "H1",
        higher_bars: int = 240,
        swing_left: int = 3,
        swing_right: int = 3,
        impulse_mult: float = 2.0,
        within: int = 3,
        zone_min_spread_mult: float = 3.0,
        reject_ratio: float = 0.6,
        min_rr: float = 2.0,
        max_spread_points: int = 25,
        session: bool = True,
        session_windows: str = "6-9,12-16",
        fresh_only: bool = True,
        confidence: float = 0.6,
        break_even_r: float = 0.0,
        break_even_offset_points: float = 0.0,
        trail_atr_mult: float = 0.0,
    ) -> None:
        self.higher = TF(higher.upper())
        self.higher_bars = higher_bars
        self.swing_left, self.swing_right = swing_left, swing_right
        self.impulse_mult, self.within = impulse_mult, within
        self.zone_min_spread_mult = zone_min_spread_mult
        self.reject_ratio = reject_ratio
        self.min_rr = min_rr
        self.max_spread_points = max_spread_points
        self.session = session
        self.windows = self._parse_windows(session_windows)
        self.fresh_only = fresh_only
        self.confidence = confidence
        self.break_even_r = break_even_r
        self.break_even_offset_points = break_even_offset_points
        self.trail_atr_mult = trail_atr_mult

    @staticmethod
    def _parse_windows(text: str) -> list[tuple[int, int]]:
        out = []
        for piece in str(text).split(","):
            a, _, b = piece.strip().partition("-")
            if a and b:
                out.append((int(a), int(b)))
        return out

    @property
    def params(self) -> dict:
        return {
            "higher": self.higher.value,
            "impulse_mult": self.impulse_mult,
            "zone_min_spread_mult": self.zone_min_spread_mult,
            "reject_ratio": self.reject_ratio,
            "min_rr": self.min_rr,
            "session": self.session,
            "fresh_only": self.fresh_only,
            "break_even_r": self.break_even_r,
            "trail_atr_mult": self.trail_atr_mult,
        }

    def in_session(self, ctx) -> bool:
        if not self.session:
            return True
        hour = ctx.closes_at.hour
        return any(start <= hour < end for start, end in self.windows)

    # --- the decision -----------------------------------------------------------------

    def _setup(self, ctx):
        """The higher-timeframe view, or None when there is not enough of it to trust."""
        htf = ctx.higher(self.higher, self.higher_bars)
        if htf is None or not htf.has_history(self.swing_left + self.swing_right + 30):
            return None
        sw = swings(htf.bars, self.swing_left, self.swing_right)
        direction = trend(sw)
        if direction == "flat":
            return None
        zs = zones(htf.bars, indicators.atr(htf.bars, 14), impulse_mult=self.impulse_mult, within=self.within)
        return direction, sw, zs

    def on_bar(self, ctx) -> Intent | None:
        if not ctx.has_history(20) or ctx.symbol_info is None or not self.in_session(ctx):
            return None
        if ctx.symbol_info.spread_points > self.max_spread_points:
            return None
        setup = self._setup(ctx)
        if setup is None:
            return None
        direction, sw, zs = setup

        bar = ctx.bar
        close, rng = bar.close, bar.high - bar.low
        if rng <= 0:
            return None
        spread = ctx.symbol_info.spread_points * ctx.point
        digits = ctx.symbol_info.digits
        long = direction == "up"

        zone = nearest(zs, close, "demand" if long else "supply")
        if zone is None or zone.broken or (self.fresh_only and not zone.fresh):
            return None
        if zone.height < self.zone_min_spread_mult * spread:
            return None

        if long:
            touched = bar.low <= zone.high and close >= zone.low
            rejected = close > bar.open and (close - bar.low) / rng >= self.reject_ratio
        else:
            touched = bar.high >= zone.low and close <= zone.high
            rejected = close < bar.open and (bar.high - close) / rng >= self.reject_ratio
        if not (touched and rejected):
            return None

        stop = round(zone.low - spread, digits) if long else round(zone.high + spread, digits)
        target = self._target(zs, sw, close, long)
        if target is None:
            return None
        target = round(target, digits)
        risk = (close - stop) if long else (stop - close)
        reward = (target - close) if long else (close - target)
        if risk <= 0 or reward < self.min_rr * risk:
            return None

        return Intent(
            symbol=ctx.symbol,
            side=Side.LONG if long else Side.SHORT,
            confidence=self.confidence,
            stop_price=stop,
            take_price=target,
            reason=(
                f"{self.higher.value} trend {direction}; {zone.kind} zone {zone.low:.5f}-{zone.high:.5f} "
                f"rejected on {ctx.timeframe.value}; RR {reward / risk:.1f}"
            ),
        )

    def _target(self, zs, sw, close: float, long: bool) -> float | None:
        """The next opposing zone; failing that, the next swing in the way."""
        opposite = nearest(zs, close, "supply" if long else "demand")
        if opposite is not None:
            return opposite.low if long else opposite.high
        if long:
            above = [s.price for s in sw if s.kind == "high" and s.price > close]
            return min(above) if above else None
        below = [s.price for s in sw if s.kind == "low" and s.price < close]
        return max(below) if below else None

    def manage(self, ctx, position, initial_stop):
        if not (self.break_even_r or self.trail_atr_mult):
            return None
        price = ctx.close()
        candidates = []
        if self.break_even_r and initial_stop:
            candidates.append(
                break_even(
                    position,
                    price,
                    initial_stop,
                    trigger_r=self.break_even_r,
                    offset_points=self.break_even_offset_points,
                    point=ctx.point,
                )
            )
        if self.trail_atr_mult:
            candidates.append(atr_trail(position, price, ctx.atr(14), multiple=self.trail_atr_mult))
        return best_of(*candidates, side=position.side)
