"""Deterministic fake broker for tests and `--fake` runs. Same contract as MT5Broker, no network."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from tradeapp.broker.guard import enforce_live_guard
from tradeapp.broker.servertime import ServerTimeOffset, utc_to_server
from tradeapp.contracts import (
    AccountInfo,
    AccountMode,
    BrokerError,
    OrderRequest,
    OrderResult,
    Position,
    Side,
    SymbolInfo,
    Tick,
)


@dataclass
class FakeBehavior:
    """Knobs that make the fake misbehave the way real brokers do."""

    mode: AccountMode = AccountMode.DEMO
    algo_trading: bool = True
    reject_orders: bool = False  # every order_send → REJECT
    drop_sl_on_fill: bool = False  # position opens without SL (market-execution brokers do this)
    fail_modify: bool = False  # TRADE_ACTION_SLTP → INVALID_STOPS
    fail_close_times: int = 0  # first N close attempts fail, then succeed (retry testing)
    fail_close_always: bool = False  # closing never works: the nightmare the kill switch must report
    raise_on_positions: bool = False  # broker cannot even be read
    slippage_points: float = 0.0  # applied against the requester on fills
    spread_points: int = 12


@dataclass
class FakeBroker:
    behavior: FakeBehavior = field(default_factory=FakeBehavior)
    allow_live: bool = False
    bid: float = 1.08660
    point: float = 0.00001
    digits: int = 5
    login: int = 90000001
    server: str = "Fake-Demo"
    balance: float = 10_000.0
    server_offset_min: int = 180  # EET summer, like XM
    connected: bool = False
    _positions: dict[int, Position] = field(default_factory=dict)
    _next_ticket: int = 500_001
    close_failures: int = 0
    sent: list[dict] = field(default_factory=list)
    closed: list[Position] = field(default_factory=list)

    # --- lifecycle ---------------------------------------------------------------

    def connect(self) -> AccountInfo:
        acct = self.account()
        enforce_live_guard(acct, self.allow_live)
        self.connected = True
        return acct

    def disconnect(self) -> None:
        self.connected = False

    def account(self) -> AccountInfo:
        equity = self.balance + sum(p.profit for p in self._positions.values())
        return AccountInfo(
            login=self.login,
            server=self.server,
            mode=self.behavior.mode,
            balance=self.balance,
            equity=equity,
            currency="USD",
            leverage=500,
            algo_trading=self.behavior.algo_trading,
        )

    def _require(self) -> None:
        if not self.connected:
            raise BrokerError("not connected")

    # --- market data -------------------------------------------------------------

    @property
    def server_offset(self) -> ServerTimeOffset:
        return ServerTimeOffset(self.server_offset_min, 0.0, True, "fake broker")

    @property
    def ask(self) -> float:
        return round(self.bid + self.behavior.spread_points * self.point, self.digits)

    def symbol_info(self, symbol: str) -> SymbolInfo:
        self._require()
        return SymbolInfo(
            symbol=symbol,
            digits=self.digits,
            point=self.point,
            volume_min=0.01,
            volume_step=0.01,
            stops_level_points=0,
            spread_points=self.behavior.spread_points,
            trade_allowed=True,
            tick_size=self.point,
            tick_value=1.0,  # EURUSD-like: 1 lot moving one 0.00001 tick is worth $1
            volume_max=100.0,
            contract_size=100_000.0,
        )

    def tick(self, symbol: str) -> Tick:
        self._require()
        now = datetime.now(UTC)
        return Tick(
            symbol=symbol,
            bid=self.bid,
            ask=self.ask,
            time_utc=now,
            time_server=utc_to_server(now, self.server_offset_min),
            server_utc_offset_min=self.server_offset_min,
        )

    def move(self, points: float) -> None:
        """Move the market; open positions re-price."""
        self.bid = round(self.bid + points * self.point, self.digits)
        for t, p in list(self._positions.items()):
            self._positions[t] = self._reprice(p)

    def _reprice(self, p: Position) -> Position:
        px = self.bid if p.side is Side.LONG else self.ask
        sign = 1 if p.side is Side.LONG else -1
        profit = round(sign * (px - p.price_open) / self.point * p.volume * 1.0, 2)  # $1 per point per lot (fake)
        return Position(**{**p.__dict__, "profit": profit})

    # --- trading -----------------------------------------------------------------

    def market_order(self, req: OrderRequest) -> OrderResult:
        self._require()
        self.sent.append({"kind": "open", "req": req})
        if self.behavior.reject_orders:
            return OrderResult(ok=False, retcode=10006, retcode_desc="REJECT", price_requested=self.ask)
        is_long = req.side is Side.LONG
        requested = self.ask if is_long else self.bid
        slip = self.behavior.slippage_points * self.point * (1 if is_long else -1)
        filled = round(requested + slip, self.digits)
        ticket = self._next_ticket
        self._next_ticket += 1
        pos = Position(
            ticket=ticket,
            symbol=req.symbol,
            side=req.side,
            volume=req.volume,
            price_open=filled,
            sl=0.0 if self.behavior.drop_sl_on_fill else req.stop_price,
            tp=req.take_price or 0.0,
            profit=0.0,
            magic=req.magic,
            comment=req.comment,
        )
        self._positions[ticket] = self._reprice(pos)
        return OrderResult(
            ok=True,
            retcode=10009,
            retcode_desc="DONE",
            order_ticket=ticket,
            deal_ticket=ticket + 1_000_000,
            position_ticket=ticket,
            price_requested=requested,
            price_filled=filled,
            volume=req.volume,
        )

    def modify_sltp(self, ticket: int, sl: float, tp: float | None) -> OrderResult:
        self._require()
        self.sent.append({"kind": "modify", "ticket": ticket, "sl": sl, "tp": tp})
        pos = self._positions.get(ticket)
        if pos is None:
            return OrderResult(ok=False, retcode=10036, retcode_desc="POSITION_CLOSED")
        if self.behavior.fail_modify:
            return OrderResult(ok=False, retcode=10016, retcode_desc="INVALID_STOPS", position_ticket=ticket)
        self._positions[ticket] = Position(**{**pos.__dict__, "sl": sl, "tp": tp if tp is not None else pos.tp})
        return OrderResult(ok=True, retcode=10009, retcode_desc="DONE", position_ticket=ticket)

    def close_position(self, ticket: int, deviation_points: int = 20) -> OrderResult:
        self._require()
        self.sent.append({"kind": "close", "ticket": ticket})
        if self.behavior.fail_close_always:
            return OrderResult(ok=False, retcode=10006, retcode_desc="REJECT", position_ticket=ticket)
        if self.close_failures < self.behavior.fail_close_times:
            self.close_failures += 1
            return OrderResult(ok=False, retcode=10004, retcode_desc="REQUOTE", position_ticket=ticket)
        pos = self._positions.pop(ticket, None)
        if pos is None:
            return OrderResult(ok=False, retcode=10036, retcode_desc="POSITION_CLOSED")
        closing_long = pos.side is Side.LONG
        requested = self.bid if closing_long else self.ask
        slip = self.behavior.slippage_points * self.point * (-1 if closing_long else 1)
        filled = round(requested + slip, self.digits)
        pos = self._reprice(pos)
        self.balance = round(self.balance + pos.profit, 2)
        self.closed.append(pos)
        return OrderResult(
            ok=True,
            retcode=10009,
            retcode_desc="DONE",
            order_ticket=self._next_ticket,
            deal_ticket=self._next_ticket + 1_000_000,
            position_ticket=ticket,
            price_requested=requested,
            price_filled=filled,
            volume=pos.volume,
        )

    # --- positions ---------------------------------------------------------------

    @property
    def open_tickets(self) -> list[int]:
        """Test inspection helper; works after disconnect (the real broker cannot answer then)."""
        return sorted(self._positions)

    def seed_position(
        self,
        symbol: str = "EURUSD",
        side: Side = Side.LONG,
        volume: float = 0.01,
        sl: float | None = None,
        magic: int = 100_001,
    ) -> Position:
        """Put a position on the books without going through order flow.

        Lets drills and tests set up a scenario without calling the trading methods, which keeps
        the rule 02 exception list short and honest.
        """
        ticket = self._next_ticket
        self._next_ticket += 1
        entry = self.ask if side is Side.LONG else self.bid
        default_sl = entry - 0.0020 if side is Side.LONG else entry + 0.0020
        pos = Position(
            ticket=ticket,
            symbol=symbol,
            side=side,
            volume=volume,
            price_open=entry,
            sl=round(sl if sl is not None else default_sl, self.digits),
            tp=0.0,
            profit=0.0,
            magic=magic,
        )
        self._positions[ticket] = pos
        return pos

    def position(self, ticket: int) -> Position | None:
        self._require()
        return self._positions.get(ticket)

    def positions(self, symbol: str | None = None, magic: int | None = None) -> list[Position]:
        self._require()
        if self.behavior.raise_on_positions:
            raise BrokerError("terminal not responding")
        out = list(self._positions.values())
        if symbol:
            out = [p for p in out if p.symbol == symbol]
        if magic is not None:
            out = [p for p in out if p.magic == magic]
        return out

    def wait_position(self, ticket: int, timeout_s: float = 3.0, poll_s: float = 0.2) -> Position | None:
        return self.position(ticket)
