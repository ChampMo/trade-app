"""Risk limits, engine state, and the AI context the engine is allowed to read.

Every number here comes from DECISIONS.md D3 and was chosen before there was money in the game.
Changing one is a decision, not a tweak: edit docs/DECISIONS.md first (CLAUDE.md rule 7).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum

from tradeapp.contracts import Side


class EngineState(StrEnum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    KILLED = "KILLED"


@dataclass(frozen=True)
class RiskLimits:
    """D3. Percentages are of equity unless stated otherwise."""

    risk_pct: float = 0.25  # per trade, before confidence and AI size multiplier
    daily_loss_limit_pct: float = 3.0  # measured against equity at the start of the trading day
    max_drawdown_pct: float = 30.0  # measured against peak equity; also the kill trigger
    max_open_risk_pct: float = 1.0  # what every open stop would cost if all were hit at once
    max_positions: int = 3  # across every strategy
    max_currency_exposure: int = 2  # net units of any single currency, so three EUR longs are one risk
    trading_start_utc: time = time(7, 0)
    trading_end_utc: time = time(20, 0)
    # A stop must clear the broker's own minimum plus the spread, or the order is rejected on arrival.
    min_stop_buffer_points: int = 5
    # AI may only shrink a position or veto it (D6). Bias never grows one: opposing a maximum bias
    # halves the size, agreeing with it changes nothing. A starting value for A/B, not a proven edge.
    opposing_bias_size_penalty: float = 0.5
    max_size_mult: float = 1.5  # hard ceiling on whatever the AI layer asks for

    def within_trading_hours(self, moment: datetime) -> bool:
        t = moment.time()
        if self.trading_start_utc <= self.trading_end_utc:
            return self.trading_start_utc <= t < self.trading_end_utc
        return t >= self.trading_start_utc or t < self.trading_end_utc  # window crossing midnight


@dataclass(frozen=True)
class AIContext:
    """The three numbers the run-time AI layer is allowed to move (D6), plus their expiry.

    Anything stale is neutral. A silent or broken AI must never freeze trading or, worse, keep
    steering it with yesterday's opinion.
    """

    regime: str | None = None
    bias: float = 0.0  # -1..1 for the symbol
    size_mult: float = 1.0  # 0..1.5
    block: bool = False
    valid_until: datetime | None = None
    source: str = "neutral"

    @classmethod
    def neutral(cls) -> AIContext:
        return cls()

    def is_stale(self, now: datetime) -> bool:
        return self.valid_until is not None and now >= self.valid_until

    def effective(self, now: datetime) -> AIContext:
        """What the engine actually uses. Expired context collapses to neutral."""
        if self.is_stale(now):
            return AIContext(regime=None, source=f"{self.source} (expired)")
        return self

    @classmethod
    def valid_for(cls, minutes: int, now: datetime, **fields) -> AIContext:
        return cls(valid_until=now + timedelta(minutes=minutes), **fields)


def bias_size_multiplier(bias: float, side: Side, penalty: float) -> float:
    """Bias shrinks a position that fights it and never enlarges one that agrees.

    bias > 0 favours long. A long into bias -1.0 with the default penalty is sized at half.
    """
    if side is Side.LONG:
        opposing = max(0.0, -bias)
    elif side is Side.SHORT:
        opposing = max(0.0, bias)
    else:
        return 1.0
    return max(0.0, 1.0 - min(1.0, opposing) * penalty)
