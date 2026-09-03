"""Context: the single box through which a strategy sees the world (rule 04).

The whole point is that `Strategy.on_bar(ctx)` cannot tell where its data came from. A live loop
fills a Context from MT5; a backtest fills the same shape from stored bars. That is what makes a
backtest mean something, and it is why Context deliberately exposes no broker, no account balance
and no way to place an order: those are the Risk Engine's business, not a strategy's.

Indicator values are computed on demand and cached per (name, period), so five strategies asking
for EMA(20) on the same bars pay for it once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tradeapp import indicators
from tradeapp.contracts import TF, Bar, SymbolInfo, Tick
from tradeapp.risk.limits import AIContext


@dataclass(frozen=True)
class Context:
    symbol: str
    timeframe: TF
    bars: list[Bar]  # oldest first; the last one is the bar that just closed
    now_utc: datetime
    tick: Tick | None = None
    symbol_info: SymbolInfo | None = None
    ai: AIContext = field(default_factory=AIContext.neutral)
    # How to get bars of a bigger timeframe. Set by whoever built this Context (the loop or the
    # backtest); a strategy only ever calls `higher()`, which applies the alignment rule below.
    higher_bars: Callable[[TF, int], list[Bar]] | None = field(default=None, repr=False, compare=False)
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    # --- bars ---------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.bars)

    @property
    def bar(self) -> Bar:
        """The bar that just closed. Strategies decide on closed bars, never on a forming one."""
        return self.bars[-1]

    def close(self, back: int = 0) -> float:
        return self.bars[-1 - back].close

    def high(self, back: int = 0) -> float:
        return self.bars[-1 - back].high

    def low(self, back: int = 0) -> float:
        return self.bars[-1 - back].low

    def has_history(self, bars_needed: int) -> bool:
        return len(self.bars) >= bars_needed

    # --- other timeframes ------------------------------------------------------------

    @property
    def closes_at(self) -> datetime:
        """When the bar that just closed actually closed: its open time plus the timeframe."""
        return self.bar.time_utc + timedelta(minutes=self.timeframe.minutes)

    def higher(self, timeframe: TF, count: int = 200) -> Context | None:
        """The same market on a bigger timeframe, as of *now* — never a bar that has not closed.

        A strategy on M15 deciding at 13:15 may look at the H1 bar that closed at 13:00 and not
        at the one that will close at 14:00, however much of it already exists. The filter is
        applied here, once, for the loop and the backtest alike (rule 04, D30). The result is a
        full Context, so the usual indicators work on it.
        """
        if timeframe.minutes <= self.timeframe.minutes:
            raise ValueError(f"{timeframe.value} is not a higher timeframe than {self.timeframe.value}")
        key = ("higher", timeframe.value, count)
        if key in self._cache:
            return self._cache[key]
        if self.higher_bars is None or not self.bars:
            return None
        cutoff = self.closes_at.timestamp()
        width = timeframe.minutes * 60
        bars = [b for b in self.higher_bars(timeframe, count) if b.time_utc.timestamp() + width <= cutoff]
        ctx = (
            Context(
                symbol=self.symbol,
                timeframe=timeframe,
                bars=bars,
                now_utc=self.now_utc,
                tick=self.tick,
                symbol_info=self.symbol_info,
                ai=self.ai,
            )
            if bars
            else None
        )
        self._cache[key] = ctx
        return ctx

    # --- indicators ---------------------------------------------------------------

    def _series(self, name: str, period: int):
        key = (name, period)
        if key not in self._cache:
            if name == "ema":
                self._cache[key] = indicators.ema(indicators.closes(self.bars), period)
            elif name == "sma":
                self._cache[key] = indicators.sma(indicators.closes(self.bars), period)
            elif name == "rsi":
                self._cache[key] = indicators.rsi(indicators.closes(self.bars), period)
            elif name == "atr":
                self._cache[key] = indicators.atr(self.bars, period)
            else:
                raise KeyError(f"unknown indicator {name!r}")
        return self._cache[key]

    def ema(self, period: int, back: int = 0) -> float | None:
        return self._at(self._series("ema", period), back)

    def sma(self, period: int, back: int = 0) -> float | None:
        return self._at(self._series("sma", period), back)

    def rsi(self, period: int = 14, back: int = 0) -> float | None:
        return self._at(self._series("rsi", period), back)

    def atr(self, period: int = 14, back: int = 0) -> float | None:
        return self._at(self._series("atr", period), back)

    @staticmethod
    def _at(series, back: int) -> float | None:
        idx = len(series) - 1 - back
        return series[idx] if 0 <= idx < len(series) else None

    # --- prices -------------------------------------------------------------------

    @property
    def point(self) -> float:
        """Falls back to a 5-digit FX point when no symbol info was supplied (tests, backtests)."""
        return self.symbol_info.point if self.symbol_info else 0.00001

    def snapshot(self) -> dict:
        """Small dict for the journal, so a decision can be replayed later."""
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe.value,
            "bar_time_utc": self.bar.time_utc.isoformat() if self.bars else None,
            "close": self.close() if self.bars else None,
            "bars_available": len(self.bars),
        }


def build_context(
    broker,
    symbol: str,
    timeframe: TF,
    *,
    count: int = 300,
    now_utc: datetime | None = None,
    ai: AIContext | None = None,
) -> Context:
    """Live Context, filled from the broker. The backtest builds the same object from stored bars."""
    bars = broker.bars(symbol, timeframe, count)
    return Context(
        symbol=symbol,
        timeframe=timeframe,
        bars=bars,
        now_utc=now_utc or (bars[-1].time_utc if bars else datetime.now()),
        tick=broker.tick(symbol),
        symbol_info=broker.symbol_info(symbol),
        ai=ai or AIContext.neutral(),
        higher_bars=lambda tf, n: broker.bars(symbol, tf, n),
    )
