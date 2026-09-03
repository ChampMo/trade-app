"""Contracts shared by every layer.

Everything downstream depends on these shapes. Change them deliberately and update tests.
Rule 02: strategies emit Intents; only the Risk Engine turns an Intent into an OrderRequest.
Rule 03: an OrderRequest without a stop price cannot exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class AccountMode(StrEnum):
    DEMO = "demo"
    CONTEST = "contest"
    REAL = "real"


class TF(StrEnum):
    """Chart timeframes. Names match MT5's TIMEFRAME_* constants so the bridge maps them by name."""

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"

    @property
    def minutes(self) -> int:
        return {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}[self.value]


@dataclass(frozen=True)
class Bar:
    """One completed candle. `time_utc` is real UTC (D13), converted by the bridge."""

    time_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    spread_points: int = 0


class BrokerError(RuntimeError):
    """The broker bridge could not do what was asked (connection, API failure)."""


class LiveAccountBlocked(BrokerError):
    """Connected account is REAL and ALLOW_LIVE is not set. Rule 8."""


@dataclass(frozen=True)
class AccountInfo:
    login: int
    server: str
    mode: AccountMode
    balance: float
    equity: float
    currency: str
    leverage: int
    algo_trading: bool  # terminal "Algo Trading" switch; False = every order_send is refused
    # Margin, as the broker reports it. None means the broker did not say, and a check that needs
    # it must skip rather than guess — 0.0 is a real value meaning "nothing left".
    margin_free: float | None = None
    margin_level: float | None = None  # equity / margin as a percentage; None when nothing is open


@dataclass(frozen=True)
class SymbolInfo:
    symbol: str
    digits: int
    point: float
    volume_min: float
    volume_step: float
    stops_level_points: int  # broker minimum SL/TP distance, in points
    spread_points: int
    trade_allowed: bool
    # Sizing inputs. tick_value is what one tick of movement is worth for one lot, already in the
    # account currency, so position sizing works for any symbol without an FX conversion table.
    tick_size: float = 0.0
    tick_value: float = 0.0
    volume_max: float = 0.0
    contract_size: float = 0.0
    # Needed to turn lots into margin in the account currency. Empty means the broker did not say.
    currency_base: str = ""
    currency_profit: str = ""


@dataclass(frozen=True)
class Tick:
    symbol: str
    bid: float
    ask: float
    time_utc: datetime  # real UTC, converted with the measured server offset (D13, P0-08)
    time_server: datetime | None = None  # the broker's own wall clock, naive, exactly as MT5 reported it
    server_utc_offset_min: int | None = None  # None means the offset could not be measured; time_utc is then a guess


@dataclass(frozen=True)
class OrderRequest:
    """A market order. Stop is mandatory (rule 03)."""

    symbol: str
    side: Side
    volume: float
    stop_price: float
    take_price: float | None
    magic: int
    comment: str = ""
    deviation_points: int = 20

    def __post_init__(self) -> None:
        if self.side not in (Side.LONG, Side.SHORT):
            raise ValueError("OrderRequest.side must be LONG or SHORT")
        if self.volume <= 0:
            raise ValueError("OrderRequest.volume must be > 0")
        if self.stop_price is None or self.stop_price <= 0:
            raise ValueError("OrderRequest.stop_price is mandatory (rule 03)")
        if self.take_price is not None and self.take_price <= 0:
            raise ValueError("OrderRequest.take_price must be > 0 when given")


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    retcode: int
    retcode_desc: str
    order_ticket: int | None = None
    deal_ticket: int | None = None
    position_ticket: int | None = None
    price_requested: float | None = None
    price_filled: float | None = None
    volume: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Position:
    ticket: int
    symbol: str
    side: Side
    volume: float
    price_open: float
    sl: float  # 0.0 means "no stop at the broker" — must never stay that way (rule 03)
    tp: float
    profit: float
    magic: int
    comment: str = ""


@runtime_checkable
class Broker(Protocol):
    """The only door to the market. In Phase 1 the Risk Engine stands in front of it."""

    def connect(self) -> AccountInfo: ...
    def disconnect(self) -> None: ...
    def account(self) -> AccountInfo: ...
    def symbol_info(self, symbol: str) -> SymbolInfo: ...
    def tick(self, symbol: str) -> Tick: ...
    def market_order(self, req: OrderRequest) -> OrderResult: ...
    def position(self, ticket: int) -> Position | None: ...
    def positions(self, symbol: str | None = None, magic: int | None = None) -> list[Position]: ...
    def modify_sltp(self, ticket: int, sl: float, tp: float | None) -> OrderResult: ...
    def close_position(self, ticket: int, deviation_points: int = 20) -> OrderResult: ...


# --- Strategy side (Phase 1 implements the runtime; the shape is fixed now) ---


@dataclass(frozen=True)
class Intent:
    """What a strategy wants. It knows nothing about sizing, balance or other strategies."""

    symbol: str
    side: Side
    confidence: float  # 0..1, Risk Engine multiplies size by it
    stop_price: float  # mandatory; no stop = no trade (rule 03)
    take_price: float | None
    reason: str  # human language, goes to the journal and to post-mortems

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Intent.confidence must be within 0..1")
        if self.side is not Side.FLAT and self.stop_price <= 0:
            raise ValueError("Intent.stop_price is mandatory for LONG/SHORT (rule 03)")


class Strategy(Protocol):
    id: str  # mapped to an MT5 magic number → clean per-strategy PnL
    symbols: list[str]
    timeframe: str

    def on_bar(self, ctx: Any) -> Intent | None: ...
