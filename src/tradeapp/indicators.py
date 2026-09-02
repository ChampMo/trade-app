"""Indicators, defined the way MetaTrader defines them.

This matters more than it looks. If EMA or ATR here disagrees with the chart the user is looking
at, then a backtest, a live decision and the human reading the journal are three different stories.
So: EMA is seeded with the simple average of the first `period` values, and ATR and RSI use
Wilder's smoothing (alpha = 1/period), which is what MT5 ships.

Every function returns a series the same length as its input, padded with None during warm-up.
A strategy that reads a None has not got enough history yet and must not trade on it.
"""

from __future__ import annotations

from collections.abc import Sequence

from tradeapp.contracts import Bar

Series = list[float | None]


def _check(period: int) -> None:
    if period < 1:
        raise ValueError("period must be >= 1")


def sma(values: Sequence[float], period: int) -> Series:
    _check(period)
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    window = sum(values[:period])
    out[period - 1] = window / period
    for i in range(period, len(values)):
        window += values[i] - values[i - period]
        out[i] = window / period
    return out


def ema(values: Sequence[float], period: int) -> Series:
    """Seeded with the SMA of the first `period` values, as MT5 does."""
    _check(period)
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * alpha + prev * (1.0 - alpha)
        out[i] = prev
    return out


def wilder(values: Sequence[float], period: int) -> Series:
    """Wilder's smoothing: the average used by ATR, RSI and ADX. alpha = 1/period."""
    _check(period)
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def true_range(bars: Sequence[Bar]) -> list[float]:
    """The first bar has no previous close, so its range is simply high - low."""
    out: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            out.append(b.high - b.low)
        else:
            prev_close = bars[i - 1].close
            out.append(max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close)))
    return out


def atr(bars: Sequence[Bar], period: int = 14) -> Series:
    return wilder(true_range(bars), period)


def rsi(values: Sequence[float], period: int = 14) -> Series:
    """Wilder's RSI. Flat input gives 100 by convention, since there are no losses to divide by."""
    _check(period)
    out: Series = [None] * len(values)
    if len(values) <= period:
        return out

    gains = [0.0]
    losses = [0.0]
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def closes(bars: Sequence[Bar]) -> list[float]:
    return [b.close for b in bars]
