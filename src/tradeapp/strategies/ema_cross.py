"""Reference strategy: EMA cross with an ATR stop.

Deliberately plain. It exists to exercise the plugin contract end to end and to be the thing the
real trend strategy (P2-04) is built from, not because a moving average cross is an edge. Its
lifecycle state is `research` until a backtest says otherwise (D10).

The shape is what matters: look at closed bars, return one Intent or None, never touch anything
else. Sizing, limits and whether the trade happens at all belong to the Risk Engine.
"""

from __future__ import annotations

from tradeapp.contracts import TF, Intent, Side
from tradeapp.exits import atr_trail, best_of, break_even
from tradeapp.strategies import register


@register
class EmaCross:
    id = "ema_cross"
    symbols = ["EURUSD"]
    timeframe = TF.H4
    state = "research"

    def __init__(
        self,
        fast: int = 20,
        slow: int = 50,
        atr_period: int = 14,
        sl_atr_mult: float = 1.5,
        rr: float = 2.0,
        confidence: float = 0.6,
        break_even_r: float = 0.0,
        break_even_offset_points: float = 0.0,
        trail_atr_mult: float = 0.0,
    ) -> None:
        if fast >= slow:
            raise ValueError("fast period must be shorter than slow")
        self.fast = fast
        self.slow = slow
        self.atr_period = atr_period
        self.sl_atr_mult = sl_atr_mult
        self.rr = rr
        self.confidence = confidence
        # Exit management, off by default. Turning either on is a parameter change like any other:
        # it needs its own backtest, and it demotes the strategy to research (D3).
        self.break_even_r = break_even_r
        self.break_even_offset_points = break_even_offset_points
        self.trail_atr_mult = trail_atr_mult

    @property
    def params(self) -> dict:
        return {
            "fast": self.fast,
            "slow": self.slow,
            "atr_period": self.atr_period,
            "sl_atr_mult": self.sl_atr_mult,
            "rr": self.rr,
            "break_even_r": self.break_even_r,
            "break_even_offset_points": self.break_even_offset_points,
            "trail_atr_mult": self.trail_atr_mult,
        }

    def manage(self, ctx, position, initial_stop):
        """Where the stop should be now, or None to leave it alone.

        Only ever the tighter of what break-even and the trail ask for: a looser stop is refused
        downstream anyway, and proposing one would fill the journal with rejections. Both are off
        unless configured, so the strategy behaves exactly as it always did until someone turns
        one on and backtests it.
        """
        if not (self.break_even_r or self.trail_atr_mult):
            return None
        price = ctx.close()
        point = ctx.symbol_info.point if ctx.symbol_info else 0.00001
        candidates = []
        if self.break_even_r and initial_stop:
            candidates.append(
                break_even(
                    position,
                    price,
                    initial_stop,
                    trigger_r=self.break_even_r,
                    offset_points=self.break_even_offset_points,
                    point=point,
                )
            )
        if self.trail_atr_mult:
            candidates.append(atr_trail(position, price, ctx.atr(self.atr_period), multiple=self.trail_atr_mult))
        return best_of(*candidates, side=position.side)

    def on_bar(self, ctx) -> Intent | None:
        if not ctx.has_history(self.slow + 2):
            return None

        fast_now, fast_prev = ctx.ema(self.fast), ctx.ema(self.fast, 1)
        slow_now, slow_prev = ctx.ema(self.slow), ctx.ema(self.slow, 1)
        atr = ctx.atr(self.atr_period)
        if None in (fast_now, fast_prev, slow_now, slow_prev, atr) or atr <= 0:
            return None  # still warming up; a None indicator is never a signal

        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now
        if not (crossed_up or crossed_down):
            return None

        close = ctx.close()
        distance = atr * self.sl_atr_mult
        digits = ctx.symbol_info.digits if ctx.symbol_info else 5
        side = Side.LONG if crossed_up else Side.SHORT
        sign = 1 if crossed_up else -1
        stop = round(close - sign * distance, digits)
        take = round(close + sign * distance * self.rr, digits)

        return Intent(
            symbol=ctx.symbol,
            side=side,
            confidence=self.confidence,
            stop_price=stop,
            take_price=take,
            reason=(
                f"EMA{self.fast} crossed {'above' if crossed_up else 'below'} EMA{self.slow} "
                f"on {ctx.timeframe.value}; stop {self.sl_atr_mult}x ATR({self.atr_period})={atr:.5f}"
            ),
        )
