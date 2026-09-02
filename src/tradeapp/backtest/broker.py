"""A broker made of history.

It implements the same `Broker` surface the MT5 bridge does, so the backtest can drive the real
Core loop, the real Risk Engine and the real Executor rather than a parallel simulation. That is
rule 04 taken seriously: if the backtest had its own order path, the number it produced would be
about that path and not about the system that trades your money.

Two modelling choices decide how honest the result is:

- **Where a market order fills.** The strategy decides when a bar closes and the live bot sends
  within a second, so the fill is that close plus the spread and slippage — not the next bar's
  open, and never the price the decision was made on without costs.
- **What happens when a bar could have hit both the stop and the target.** OHLC does not say which
  came first. This assumes the stop. It is the pessimistic reading and the only defensible one:
  the optimistic version turns losing systems into winners on paper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from tradeapp.backtest.costs import CostModel
from tradeapp.broker.servertime import ServerTimeOffset
from tradeapp.contracts import (
    TF,
    AccountInfo,
    AccountMode,
    Bar,
    BrokerError,
    OrderRequest,
    OrderResult,
    Position,
    Side,
    SymbolInfo,
    Tick,
)

EURUSD = SymbolInfo(
    symbol="EURUSD",
    digits=5,
    point=0.00001,
    volume_min=0.01,
    volume_step=0.01,
    stops_level_points=0,
    spread_points=20,
    trade_allowed=True,
    tick_size=0.00001,
    tick_value=1.0,
    volume_max=50.0,
    contract_size=100_000.0,
)


@dataclass
class ClosedTrade:
    ticket: int
    symbol: str
    side: Side
    volume: float
    magic: int
    comment: str
    opened_utc: datetime
    closed_utc: datetime
    entry: float
    exit: float
    sl: float
    tp: float
    gross: float
    commission: float
    swap: float
    spread_cost: float  # what crossing the spread cost on entry; already inside `gross`
    exit_reason: str  # stop | target | signal | end_of_data

    @property
    def net(self) -> float:
        return round(self.gross - self.commission - self.swap, 2)

    @property
    def total_cost(self) -> float:
        """Everything the broker took. The spread is the big one and the easiest to forget."""
        return round(self.commission + self.swap + self.spread_cost, 2)

    @property
    def bars_held(self) -> float:
        return (self.closed_utc - self.opened_utc).total_seconds() / 3600.0


@dataclass
class BacktestBroker:
    """Replays bars and pretends to be a broker while doing it."""

    bars_all: list[Bar]
    symbol: str = "EURUSD"
    timeframe: TF = TF.H4
    costs: CostModel = field(default_factory=CostModel)
    info: SymbolInfo = EURUSD
    balance: float = 10_000.0
    index: int = 0  # the bar that has just closed
    connected: bool = False

    _positions: dict[int, Position] = field(default_factory=dict)
    _meta: dict[int, dict] = field(default_factory=dict)
    _next_ticket: int = 1
    trades: list[ClosedTrade] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    total_commission: float = 0.0
    total_swap: float = 0.0
    total_spread_cost: float = 0.0

    # --- clock and prices ---------------------------------------------------------

    @property
    def bar(self) -> Bar:
        return self.bars_all[self.index]

    def now(self) -> datetime:
        return self.bar.time_utc

    @property
    def server_offset(self) -> ServerTimeOffset:
        return ServerTimeOffset(0, 0.0, True, "backtest")

    def _spread_price(self) -> float:
        return self.costs.spread_for(self.bar.spread_points) * self.info.point

    @property
    def bid(self) -> float:
        return self.bar.close

    @property
    def ask(self) -> float:
        return round(self.bar.close + self._spread_price(), self.info.digits)

    # --- broker surface -----------------------------------------------------------

    def connect(self) -> AccountInfo:
        self.connected = True
        return self.account()

    def disconnect(self) -> None:
        self.connected = False

    def account(self) -> AccountInfo:
        return AccountInfo(
            login=1,
            server="backtest",
            mode=AccountMode.DEMO,
            balance=round(self.balance, 2),
            equity=round(self.balance + self._floating(), 2),
            currency="USD",
            leverage=500,
            algo_trading=True,
        )

    def symbol_info(self, symbol: str) -> SymbolInfo:
        spread = int(self.costs.spread_for(self.bar.spread_points))
        return SymbolInfo(**{**self.info.__dict__, "symbol": symbol, "spread_points": spread})

    def tick(self, symbol: str) -> Tick:
        return Tick(
            symbol=symbol,
            bid=self.bid,
            ask=self.ask,
            time_utc=self.now(),
            time_server=self.now().replace(tzinfo=None),
            server_utc_offset_min=0,
        )

    def bars(self, symbol: str, timeframe: TF, count: int = 300, include_forming: bool = False) -> list[Bar]:
        return self.bars_all[max(0, self.index + 1 - count) : self.index + 1]

    def positions(self, symbol: str | None = None, magic: int | None = None) -> list[Position]:
        out = list(self._positions.values())
        if symbol:
            out = [p for p in out if p.symbol == symbol]
        if magic is not None:
            out = [p for p in out if p.magic == magic]
        return out

    def position(self, ticket: int) -> Position | None:
        return self._positions.get(ticket)

    def wait_position(self, ticket: int, timeout_s: float = 3.0, poll_s: float = 0.2) -> Position | None:
        return self._positions.get(ticket)

    def market_order(self, req: OrderRequest) -> OrderResult:
        if not self.connected:
            raise BrokerError("not connected")
        long = req.side is Side.LONG
        requested = self.ask if long else self.bid
        slip = self.costs.slippage_points * self.info.point * (1 if long else -1)
        fill = round(requested + slip, self.info.digits)

        commission = self.costs.commission_per_lot_round_trip * req.volume
        self.balance -= commission
        self.total_commission += commission

        # Crossing the spread is a real cost even though it never appears as a line item: the
        # position is worth the far side of the quote the instant it opens.
        spread_cost = (self._spread_price() / self.info.tick_size) * self.info.tick_value * req.volume
        self.total_spread_cost += spread_cost

        ticket = self._next_ticket
        self._next_ticket += 1
        self._positions[ticket] = Position(
            ticket=ticket,
            symbol=req.symbol,
            side=req.side,
            volume=req.volume,
            price_open=fill,
            sl=req.stop_price,
            tp=req.take_price or 0.0,
            profit=0.0,
            magic=req.magic,
            comment=req.comment,
        )
        self._meta[ticket] = {"opened": self.now(), "commission": commission, "swap": 0.0, "spread": spread_cost}
        return OrderResult(
            ok=True,
            retcode=10009,
            retcode_desc="DONE",
            order_ticket=ticket,
            deal_ticket=ticket,
            position_ticket=ticket,
            price_requested=requested,
            price_filled=fill,
            volume=req.volume,
        )

    def modify_sltp(self, ticket: int, sl: float, tp: float | None) -> OrderResult:
        pos = self._positions.get(ticket)
        if pos is None:
            return OrderResult(ok=False, retcode=10036, retcode_desc="POSITION_CLOSED")
        self._positions[ticket] = Position(**{**pos.__dict__, "sl": sl, "tp": tp if tp is not None else pos.tp})
        return OrderResult(ok=True, retcode=10009, retcode_desc="DONE", position_ticket=ticket)

    def close_position(self, ticket: int, deviation_points: int = 20) -> OrderResult:
        pos = self._positions.get(ticket)
        if pos is None:
            return OrderResult(ok=False, retcode=10036, retcode_desc="POSITION_CLOSED")
        long = pos.side is Side.LONG
        requested = self.bid if long else self.ask
        slip = self.costs.slippage_points * self.info.point * (-1 if long else 1)
        price = round(requested + slip, self.info.digits)
        self._settle(ticket, price, "signal")
        return OrderResult(
            ok=True,
            retcode=10009,
            retcode_desc="DONE",
            order_ticket=ticket,
            deal_ticket=ticket,
            position_ticket=ticket,
            price_requested=requested,
            price_filled=price,
            volume=pos.volume,
        )

    # --- replay -------------------------------------------------------------------

    def advance(self) -> bool:
        """Step to the next bar, resolving stops and targets against its range first."""
        if self.index >= len(self.bars_all) - 1:
            return False
        self.index += 1
        self._apply_swaps()
        self._resolve_exits()
        self._reprice()
        self.equity_curve.append((self.now(), self.account().equity))
        return True

    def _resolve_exits(self) -> None:
        bar = self.bar
        for ticket, pos in list(self._positions.items()):
            long = pos.side is Side.LONG
            hit_stop = (long and bar.low <= pos.sl) or (not long and pos.sl > 0 and bar.high >= pos.sl)
            hit_target = pos.tp > 0 and ((long and bar.high >= pos.tp) or (not long and bar.low <= pos.tp))
            # OHLC cannot say which came first, so assume the stop. The optimistic reading is how
            # losing systems get published as winners.
            if hit_stop:
                self._settle(ticket, pos.sl, "stop")
            elif hit_target:
                self._settle(ticket, pos.tp, "target")

    def _apply_swaps(self) -> None:
        if not (self.costs.swap_long_per_lot_per_night or self.costs.swap_short_per_lot_per_night):
            return
        if self.index == 0:
            return
        previous = self.bars_all[self.index - 1].time_utc
        nights = (self.now().date() - previous.date()).days
        if nights <= 0:
            return
        for ticket, pos in self._positions.items():
            rate = (
                self.costs.swap_long_per_lot_per_night
                if pos.side is Side.LONG
                else self.costs.swap_short_per_lot_per_night
            )
            charge = rate * pos.volume * nights
            self._meta[ticket]["swap"] += charge
            self.balance -= charge
            self.total_swap += charge

    def _settle(self, ticket: int, price: float, reason: str) -> None:
        pos = self._positions.pop(ticket)
        meta = self._meta.pop(ticket)
        gross = self._pnl(pos, price)
        self.balance = round(self.balance + gross, 2)
        self.trades.append(
            ClosedTrade(
                ticket=ticket,
                symbol=pos.symbol,
                side=pos.side,
                volume=pos.volume,
                magic=pos.magic,
                comment=pos.comment,
                opened_utc=meta["opened"],
                closed_utc=self.now(),
                entry=pos.price_open,
                exit=price,
                sl=pos.sl,
                tp=pos.tp,
                gross=round(gross, 2),
                commission=round(meta["commission"], 2),
                swap=round(meta["swap"], 2),
                spread_cost=round(meta["spread"], 2),
                exit_reason=reason,
            )
        )

    def _pnl(self, pos: Position, price: float) -> float:
        sign = 1 if pos.side is Side.LONG else -1
        ticks = sign * (price - pos.price_open) / self.info.tick_size
        return ticks * self.info.tick_value * pos.volume

    def _floating(self) -> float:
        return sum(self._pnl(p, self.bid if p.side is Side.LONG else self.ask) for p in self._positions.values())

    def _reprice(self) -> None:
        for ticket, pos in list(self._positions.items()):
            price = self.bid if pos.side is Side.LONG else self.ask
            self._positions[ticket] = Position(**{**pos.__dict__, "profit": round(self._pnl(pos, price), 2)})

    def close_all_at_end(self) -> None:
        """Positions still open when the data runs out are marked, not silently dropped."""
        for ticket in list(self._positions):
            pos = self._positions[ticket]
            self._settle(ticket, self.bid if pos.side is Side.LONG else self.ask, "end_of_data")


def bars_between(bars: list[Bar], start: datetime | None, end: datetime | None) -> list[Bar]:
    lo = start or datetime.min.replace(tzinfo=UTC)
    hi = end or datetime.max.replace(tzinfo=UTC)
    return [b for b in bars if lo <= b.time_utc <= hi]


def split_windows(
    bars: list[Bar], train: timedelta, test: timedelta, step: timedelta
) -> list[tuple[list[Bar], list[Bar]]]:
    """Rolling train/test pairs for walk-forward. Empty windows are skipped, not padded."""
    if not bars:
        return []
    out: list[tuple[list[Bar], list[Bar]]] = []
    cursor = bars[0].time_utc
    last = bars[-1].time_utc
    while cursor + train + test <= last:
        train_end = cursor + train
        test_end = train_end + test
        tr = bars_between(bars, cursor, train_end)
        te = bars_between(bars, train_end, test_end)
        if tr and te:
            out.append((tr, te))
        cursor += step
    return out
