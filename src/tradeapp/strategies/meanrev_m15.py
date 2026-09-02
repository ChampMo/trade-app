"""Mean reversion on M15: fade a stretch away from the average.

The second strategy exists for a reason beyond having two (P2-04). A trend follower and a mean
reverter are good in opposite conditions, so running them together is the first honest test of
whether the Risk Engine's netting and portfolio limits do anything useful — and whether the
combined equity curve is smoother than either alone. Two variations of the same idea would tell
you nothing.

Entry: price closes beyond a band around the moving average while RSI agrees it is stretched, and
the bar shows some sign of turning back. Stop beyond the extreme, target back at the average.
Parameters are starting points, not findings; the backtest decides.
"""

from __future__ import annotations

from tradeapp.contracts import TF, Intent, Side
from tradeapp.strategies import register


@register
class MeanReversionM15:
    id = "meanrev_m15"
    symbols = ["EURUSD"]
    timeframe = TF.M15
    state = "research"

    def __init__(
        self,
        ma: int = 50,
        band_atr_mult: float = 2.0,
        atr_period: int = 14,
        rsi_period: int = 14,
        rsi_low: float = 30.0,
        rsi_high: float = 70.0,
        sl_atr_mult: float = 1.5,
        confidence: float = 0.5,
    ) -> None:
        if band_atr_mult <= 0 or sl_atr_mult <= 0:
            raise ValueError("band and stop multipliers must be positive")
        self.ma = ma
        self.band_atr_mult = band_atr_mult
        self.atr_period = atr_period
        self.rsi_period = rsi_period
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.sl_atr_mult = sl_atr_mult
        self.confidence = confidence

    @property
    def params(self) -> dict:
        return {
            "ma": self.ma,
            "band_atr_mult": self.band_atr_mult,
            "atr_period": self.atr_period,
            "rsi_period": self.rsi_period,
            "sl_atr_mult": self.sl_atr_mult,
        }

    def on_bar(self, ctx) -> Intent | None:
        if not ctx.has_history(self.ma + self.atr_period + 2):
            return None

        average = ctx.sma(self.ma)
        atr = ctx.atr(self.atr_period)
        rsi = ctx.rsi(self.rsi_period)
        if None in (average, atr, rsi) or atr <= 0:
            return None

        close = ctx.close()
        band = atr * self.band_atr_mult
        digits = ctx.symbol_info.digits if ctx.symbol_info else 5

        stretched_low = close < average - band and rsi < self.rsi_low
        stretched_high = close > average + band and rsi > self.rsi_high
        if not (stretched_low or stretched_high):
            return None

        # Require the bar itself to have turned: buying a falling knife is not mean reversion.
        turning_up = close > ctx.bar.open
        turning_down = close < ctx.bar.open
        if stretched_low and not turning_up:
            return None
        if stretched_high and not turning_down:
            return None

        side = Side.LONG if stretched_low else Side.SHORT
        sign = 1 if stretched_low else -1
        extreme = ctx.low() if stretched_low else ctx.high()
        stop = round(extreme - sign * atr * self.sl_atr_mult, digits)
        take = round(average, digits)  # the average is the whole thesis, so it is also the target

        # A target the wrong side of the entry means the move already reverted; there is no trade.
        if (side is Side.LONG and take <= close) or (side is Side.SHORT and take >= close):
            return None

        return Intent(
            symbol=ctx.symbol,
            side=side,
            confidence=self.confidence,
            stop_price=stop,
            take_price=take,
            reason=(
                f"close {close:.5f} is {abs(close - average) / atr:.1f} ATR from SMA{self.ma} "
                f"with RSI {rsi:.0f}; fading back to the average"
            ),
        )
