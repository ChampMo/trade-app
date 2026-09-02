"""Reference strategy: EMA cross with an ATR stop.

Deliberately plain. It exists to exercise the plugin contract end to end and to be the thing the
real trend strategy (P2-04) is built from, not because a moving average cross is an edge. Its
lifecycle state is `research` until a backtest says otherwise (D10).

The shape is what matters: look at closed bars, return one Intent or None, never touch anything
else. Sizing, limits and whether the trade happens at all belong to the Risk Engine.
"""

from __future__ import annotations

from tradeapp.contracts import TF, Intent, Side
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
    ) -> None:
        if fast >= slow:
            raise ValueError("fast period must be shorter than slow")
        self.fast = fast
        self.slow = slow
        self.atr_period = atr_period
        self.sl_atr_mult = sl_atr_mult
        self.rr = rr
        self.confidence = confidence

    @property
    def params(self) -> dict:
        return {
            "fast": self.fast,
            "slow": self.slow,
            "atr_period": self.atr_period,
            "sl_atr_mult": self.sl_atr_mult,
            "rr": self.rr,
        }

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
