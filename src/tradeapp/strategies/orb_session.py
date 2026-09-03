"""Opening-range breakout on M15: the London open, measured before it is traded.

The second candidate in the M15 research, chosen to be as different from the zone method as
possible. Where zones wait for a rare, precise setup (a year gave twenty), a session breakout
happens most days, so it yields a sample size a statistic can be computed from — which is the
first thing a candidate has to offer before its edge can even be measured.

The mechanism, in three sentences. The first `range_bars` bars of the session (07:00-08:00 UTC
by default: the hour the range table in RESEARCH.md shows volatility doubling) set a high and a
low. The first close beyond either side, before `until_hour`, is the entry; only the first one,
so a day trades at most once per direction, decided statelessly from the bars themselves so a
restart cannot change the answer. The stop is the other side of the range plus one spread, and
the target is `rr` times that distance.

Cost-aware where it must be: a range narrower than `min_range_spread_mult` spreads is skipped,
because the stop would be inside the noise the spread itself makes.
"""

from __future__ import annotations

from tradeapp.contracts import TF, Intent, Side
from tradeapp.exits import atr_trail, best_of, break_even
from tradeapp.strategies import register
from tradeapp.structure import swings, trend


@register
class OpeningRangeBreakout:
    id = "orb_session"
    symbols = ["EURUSD"]
    timeframe = TF.M15
    state = "research"
    # A candidate under research is not registered by the loop unless named with --strategy or
    # attached on the Markets page. Adding a file to this package must not add a trader to the
    # owner's demo account on the next restart (D31).
    auto_trade = False

    def __init__(
        self,
        range_start: int = 7,
        range_bars: int = 4,
        until_hour: int = 12,
        rr: float = 2.0,
        min_range_spread_mult: float = 3.0,
        max_spread_points: int = 25,
        with_trend: bool = False,
        higher: str = "H1",
        confidence: float = 0.6,
        break_even_r: float = 0.0,
        break_even_offset_points: float = 0.0,
        trail_atr_mult: float = 0.0,
    ) -> None:
        self.range_start = range_start
        self.range_bars = range_bars
        self.until_hour = until_hour
        self.rr = rr
        self.min_range_spread_mult = min_range_spread_mult
        self.max_spread_points = max_spread_points
        self.with_trend = with_trend
        self.higher = TF(higher.upper())
        self.confidence = confidence
        self.break_even_r = break_even_r
        self.break_even_offset_points = break_even_offset_points
        self.trail_atr_mult = trail_atr_mult

    @property
    def params(self) -> dict:
        return {
            "range_start": self.range_start,
            "range_bars": self.range_bars,
            "until_hour": self.until_hour,
            "rr": self.rr,
            "min_range_spread_mult": self.min_range_spread_mult,
            "with_trend": self.with_trend,
            "break_even_r": self.break_even_r,
            "trail_atr_mult": self.trail_atr_mult,
        }

    def _today(self, ctx) -> list:
        """This bar's day, oldest first, up to and including this bar."""
        day = ctx.bar.time_utc.date()
        out = []
        for b in reversed(ctx.bars):
            if b.time_utc.date() != day:
                break
            out.append(b)
        return list(reversed(out))

    def on_bar(self, ctx) -> Intent | None:
        if ctx.symbol_info is None or not ctx.has_history(self.range_bars + 2):
            return None
        if ctx.symbol_info.spread_points > self.max_spread_points:
            return None
        bar = ctx.bar
        if not (self.range_start <= bar.time_utc.hour < self.until_hour):
            return None

        today = self._today(ctx)
        opening = [b for b in today if b.time_utc.hour == self.range_start][: self.range_bars]
        if len(opening) < self.range_bars or bar.time_utc <= opening[-1].time_utc:
            return None  # the range is not complete yet, or this bar is part of it
        hi = max(b.high for b in opening)
        lo = min(b.low for b in opening)
        spread = ctx.symbol_info.spread_points * ctx.point
        if hi - lo < self.min_range_spread_mult * spread:
            return None

        # Stateless "first breakout": any earlier bar today, after the range, that already closed
        # outside means this is not the first, and the day is spent in that direction.
        after = [b for b in today if b.time_utc > opening[-1].time_utc and b is not bar]
        if bar.close > hi:
            side, stop, taken = Side.LONG, lo - spread, any(b.close > hi for b in after)
        elif bar.close < lo:
            side, stop, taken = Side.SHORT, hi + spread, any(b.close < lo for b in after)
        else:
            return None
        if taken:
            return None

        if self.with_trend:
            htf = ctx.higher(self.higher, 240)
            direction = trend(swings(htf.bars)) if htf is not None else "flat"
            if (side is Side.LONG and direction != "up") or (side is Side.SHORT and direction != "down"):
                return None

        digits = ctx.symbol_info.digits
        risk = abs(bar.close - stop)
        take = bar.close + risk * self.rr if side is Side.LONG else bar.close - risk * self.rr
        return Intent(
            symbol=ctx.symbol,
            side=side,
            confidence=self.confidence,
            stop_price=round(stop, digits),
            take_price=round(take, digits),
            reason=(
                f"{self.range_start:02d}:00 range {lo:.5f}-{hi:.5f} ({(hi - lo) / ctx.point:.0f}pt) "
                f"broken {'up' if side is Side.LONG else 'down'} at {bar.time_utc:%H:%M}; RR {self.rr}"
            ),
        )

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
