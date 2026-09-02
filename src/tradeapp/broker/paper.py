"""Paper trading: live prices, imaginary fills (P1-07).

It wraps a real broker for everything that reads — the account, the symbol, ticks, bars — and
simulates everything that writes. No order ever leaves the machine, so this is the honest way to
watch the whole loop behave against a live market before letting it touch even a demo account.

Where it differs from the backtest broker matters: this one cannot see the future. It has a tick
and nothing else, so stops and targets are checked against the ticks that actually arrive. A
position whose stop is jumped over between two ticks fills at the price that was there, which is
the same thing that happens for real.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tradeapp.contracts import (
    TF,
    AccountInfo,
    Bar,
    BrokerError,
    OrderRequest,
    OrderResult,
    Position,
    Side,
    SymbolInfo,
    Tick,
)


@dataclass
class PaperFills:
    slippage_points: float = 0.0
    commission_per_lot_round_trip: float = 0.0


class PaperBroker:
    """Reads through to a real broker; writes stay here."""

    def __init__(self, source: Any, *, balance: float = 10_000.0, fills: PaperFills | None = None) -> None:
        self.source = source
        self.balance = balance
        self.fills = fills or PaperFills()
        self._positions: dict[int, Position] = {}
        self._meta: dict[int, dict] = {}
        self._next_ticket = 900_001
        self.closed: list[dict] = []
        self.connected = False

    # --- read-through --------------------------------------------------------------

    def connect(self) -> AccountInfo:
        self.source.connect()
        self.connected = True
        return self.account()

    def disconnect(self) -> None:
        self.source.disconnect()
        self.connected = False

    def account(self) -> AccountInfo:
        real = self.source.account()
        # The balance is ours, not the account's: paper trading must not report the demo account's
        # equity as if the imaginary trades had happened to it.
        return AccountInfo(
            login=real.login,
            server=f"{real.server} (paper)",
            mode=real.mode,
            balance=round(self.balance, 2),
            equity=round(self.balance + self._floating(), 2),
            currency=real.currency,
            leverage=real.leverage,
            algo_trading=real.algo_trading,
        )

    def symbol_info(self, symbol: str) -> SymbolInfo:
        return self.source.symbol_info(symbol)

    def tick(self, symbol: str) -> Tick:
        tick = self.source.tick(symbol)
        self._settle_touched(tick)
        return tick

    def bars(self, symbol: str, timeframe: TF, count: int = 300, include_forming: bool = False) -> list[Bar]:
        return self.source.bars(symbol, timeframe, count, include_forming)

    @property
    def server_offset(self):
        return getattr(self.source, "server_offset", None)

    # --- simulated writes ------------------------------------------------------------

    def market_order(self, req: OrderRequest) -> OrderResult:
        if not self.connected:
            raise BrokerError("not connected")
        sym = self.symbol_info(req.symbol)
        tick = self.source.tick(req.symbol)
        long = req.side is Side.LONG
        requested = tick.ask if long else tick.bid
        slip = self.fills.slippage_points * sym.point * (1 if long else -1)
        fill = round(requested + slip, sym.digits)

        commission = self.fills.commission_per_lot_round_trip * req.volume
        self.balance -= commission

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
        self._meta[ticket] = {"opened": tick.time_utc, "commission": commission, "info": sym}
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
        sym = self._meta[ticket]["info"]
        tick = self.source.tick(pos.symbol)
        long = pos.side is Side.LONG
        requested = tick.bid if long else tick.ask
        slip = self.fills.slippage_points * sym.point * (-1 if long else 1)
        price = round(requested + slip, sym.digits)
        self._settle(ticket, price, "signal", tick.time_utc)
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

    def position(self, ticket: int) -> Position | None:
        return self._positions.get(ticket)

    def positions(self, symbol: str | None = None, magic: int | None = None) -> list[Position]:
        out = list(self._positions.values())
        if symbol:
            out = [p for p in out if p.symbol == symbol]
        if magic is not None:
            out = [p for p in out if p.magic == magic]
        return out

    def wait_position(self, ticket: int, timeout_s: float = 3.0, poll_s: float = 0.2) -> Position | None:
        return self._positions.get(ticket)

    # --- stops and targets, checked against the ticks that arrive ----------------------

    def _settle_touched(self, tick: Tick) -> None:
        for ticket, pos in list(self._positions.items()):
            if pos.symbol != tick.symbol:
                continue
            price = tick.bid if pos.side is Side.LONG else tick.ask
            long = pos.side is Side.LONG
            if pos.sl > 0 and ((long and price <= pos.sl) or (not long and price >= pos.sl)):
                self._settle(ticket, price, "stop", tick.time_utc)
            elif pos.tp > 0 and ((long and price >= pos.tp) or (not long and price <= pos.tp)):
                self._settle(ticket, price, "target", tick.time_utc)
            else:
                self._positions[ticket] = Position(**{**pos.__dict__, "profit": round(self._pnl(pos, price), 2)})

    def _pnl(self, pos: Position, price: float) -> float:
        sym = self._meta[pos.ticket]["info"]
        sign = 1 if pos.side is Side.LONG else -1
        return sign * (price - pos.price_open) / sym.tick_size * sym.tick_value * pos.volume

    def _floating(self) -> float:
        total = 0.0
        for pos in self._positions.values():
            try:
                tick = self.source.tick(pos.symbol)
            except Exception:  # noqa: BLE001
                continue
            total += self._pnl(pos, tick.bid if pos.side is Side.LONG else tick.ask)
        return total

    def _settle(self, ticket: int, price: float, reason: str, when: datetime) -> None:
        pos = self._positions.pop(ticket)
        meta = self._meta.pop(ticket)
        gross = self._pnl_with(pos, price, meta["info"])
        self.balance = round(self.balance + gross, 2)
        self.closed.append(
            {
                "ticket": ticket,
                "symbol": pos.symbol,
                "side": pos.side.value,
                "volume": pos.volume,
                "entry": pos.price_open,
                "exit": price,
                "gross": round(gross, 2),
                "commission": round(meta["commission"], 2),
                "reason": reason,
                "opened_utc": meta["opened"].isoformat(),
                "closed_utc": when.isoformat(),
            }
        )

    @staticmethod
    def _pnl_with(pos: Position, price: float, sym: SymbolInfo) -> float:
        sign = 1 if pos.side is Side.LONG else -1
        return sign * (price - pos.price_open) / sym.tick_size * sym.tick_value * pos.volume
