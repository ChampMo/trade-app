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


# --- timeframes ------------------------------------------------------------------------------


def aggregate_bars(bars: Sequence[Bar], to_minutes: int, *, from_minutes: int) -> list[Bar]:
    """Roll smaller bars up into bigger ones, returning only the ones that have fully closed.

    The one rule that matters is at the end: a bucket is included only if its close time is at or
    before the close of the last small bar given. A strategy on M15 asking for H1 must never see
    the H1 bar that the current M15 bar is still part of — that is the look-ahead bias that makes
    most multi-timeframe backtests in the world look better than they are (D30).

    Buckets are aligned to the epoch, which is how MT5 aligns them too (an H4 bar starts on the
    hour divisible by four in server time; server time is handled upstream, so here it is UTC).
    """
    if to_minutes <= 0 or from_minutes <= 0 or to_minutes % from_minutes != 0:
        raise ValueError(f"cannot build {to_minutes}-minute bars from {from_minutes}-minute bars")
    if not bars:
        return []
    width = to_minutes * 60
    out: list[Bar] = []
    bucket_start = None
    o = hi = lo = c = None
    v = 0
    for bar in bars:
        start = int(bar.time_utc.timestamp()) // width * width
        if start != bucket_start:
            if bucket_start is not None:
                out.append(_bucket(bucket_start, o, hi, lo, c, v, bars[0]))
            bucket_start, o, hi, lo, c, v = start, bar.open, bar.high, bar.low, bar.close, 0
        else:
            hi, lo, c = max(hi, bar.high), min(lo, bar.low), bar.close
        v += int(getattr(bar, "volume", 0) or 0)
    out.append(_bucket(bucket_start, o, hi, lo, c, v, bars[0]))

    last_close = bars[-1].time_utc.timestamp() + from_minutes * 60
    return [b for b in out if b.time_utc.timestamp() + width <= last_close]


def _bucket(start: int, o: float, hi: float, lo: float, c: float, v: int, like: Bar) -> Bar:
    from datetime import datetime

    when = datetime.fromtimestamp(start, tz=like.time_utc.tzinfo)
    fields = {"time_utc": when, "open": o, "high": hi, "low": lo, "close": c}
    if hasattr(like, "volume"):
        fields["volume"] = v
    if hasattr(like, "spread"):
        fields["spread"] = getattr(like, "spread", 0)
    return Bar(**fields)
