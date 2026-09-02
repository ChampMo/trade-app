"""MetaTrader 5 bridge.

Wraps the official `MetaTrader5` package (Windows only, blocking). The package is imported lazily so
this module can be imported anywhere; tests inject a fake module through `mt5_module`.

Rules enforced here:
  8  refuse a REAL account unless allow_live (via broker.guard)
  3  market orders always carry SL; `verify_stop` is used by callers after a fill
"""

from __future__ import annotations

import time
from datetime import UTC
from typing import Any

from tradeapp.broker.guard import enforce_live_guard
from tradeapp.broker.servertime import ServerTimeOffset, epoch_to_server_wall, measure_offset, server_to_utc
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

# MQL5 TRADE_RETCODE_* values
RETCODE_DESC: dict[int, str] = {
    10004: "REQUOTE",
    10006: "REJECT",
    10007: "CANCEL",
    10008: "PLACED",
    10009: "DONE",
    10010: "DONE_PARTIAL",
    10011: "ERROR",
    10012: "TIMEOUT",
    10013: "INVALID",
    10014: "INVALID_VOLUME",
    10015: "INVALID_PRICE",
    10016: "INVALID_STOPS",
    10017: "TRADE_DISABLED",
    10018: "MARKET_CLOSED",
    10019: "NO_MONEY",
    10020: "PRICE_CHANGED",
    10021: "PRICE_OFF",
    10022: "INVALID_EXPIRATION",
    10023: "ORDER_CHANGED",
    10024: "TOO_MANY_REQUESTS",
    10025: "NO_CHANGES",
    10026: "SERVER_DISABLES_AT",
    10027: "CLIENT_DISABLES_AT",
    10028: "LOCKED",
    10029: "FROZEN",
    10030: "INVALID_FILL",
    10031: "CONNECTION",
    10032: "ONLY_REAL",
    10033: "LIMIT_ORDERS",
    10034: "LIMIT_VOLUME",
    10035: "INVALID_ORDER",
    10036: "POSITION_CLOSED",
    10038: "INVALID_CLOSE_VOLUME",
    10039: "CLOSE_ORDER_EXIST",
    10040: "LIMIT_POSITIONS",
    10041: "REJECT_CANCEL",
    10042: "LONG_ONLY",
    10043: "SHORT_ONLY",
    10044: "CLOSE_ONLY",
    10045: "FIFO_CLOSE",
    10046: "HEDGE_PROHIBITED",
}
RETCODE_OK = (10009, 10010)  # DONE, DONE_PARTIAL
RETCODE_RETRYABLE = (10004, 10020, 10021, 10024)  # requote, price changed, price off, too many requests

_TRADE_MODE = {0: AccountMode.DEMO, 1: AccountMode.CONTEST, 2: AccountMode.REAL}

# `mt5.initialize` failures are reported as bare numbers. Each one has exactly one likely cause
# on this setup, so say it rather than making the reader search MQL5 documentation.
INIT_ERROR_HINTS: dict[int, str] = {
    -2: "invalid parameters: check MT5_LOGIN, MT5_SERVER and MT5_PATH in .env",
    -4: "terminal not found: check MT5_PATH points at an existing terminal64.exe",
    -5: "terminal version too old: update MetaTrader 5",
    -6: (
        "the terminal is not logged in to a trading account. Most often the account number and the "
        "server do not belong together (an XM account cannot log in to MetaQuotes-Demo, and vice versa) "
        "— open File > Login to Trade Account and pick the server named in your broker's welcome email. "
        "If MT5_LOGIN/MT5_SERVER are set in .env they must match that account exactly"
    ),
    -8: "automated trading is disabled: switch Algo Trading on in the terminal toolbar",
    -10003: "terminal is starting or busy; retry in a few seconds",
}


def describe_retcode(code: int) -> str:
    return RETCODE_DESC.get(code, f"UNKNOWN_{code}")


def describe_init_error(err: Any) -> str:
    """Turn `mt5.last_error()` into something the reader can act on."""
    try:
        code, text = int(err[0]), str(err[1])
    except (TypeError, ValueError, IndexError):
        return str(err)
    hint = INIT_ERROR_HINTS.get(code)
    return f"{text} (code {code}) — {hint}" if hint else f"{text} (code {code})"


def _as_dict(obj: Any) -> dict[str, Any]:
    """Named tuples from MetaTrader5 expose _asdict(); fall back to vars() for fakes."""
    if obj is None:
        return {}
    if hasattr(obj, "_asdict"):
        return dict(obj._asdict())
    try:
        return dict(vars(obj))
    except TypeError:
        return {"value": repr(obj)}


class MT5Broker:
    """Synchronous bridge. In an asyncio core call it through a thread executor."""

    def __init__(
        self,
        *,
        path: str | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        allow_live: bool = False,
        timeout_ms: int = 60_000,
        reference_symbol: str = "EURUSD",
        mt5_module: Any | None = None,
    ) -> None:
        self.path = path
        self.login = login
        self.password = password
        self.server = server
        self.allow_live = allow_live
        self.timeout_ms = timeout_ms
        self.reference_symbol = reference_symbol
        self._mt5 = mt5_module
        self._account: AccountInfo | None = None
        self._digits: dict[str, int] = {}
        self.server_offset = ServerTimeOffset(None, None, False, "not measured yet")
        self.connected = False

    # --- lifecycle ---------------------------------------------------------------

    def _lib(self) -> Any:
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5  # type: ignore[import-not-found]
            except ImportError as e:  # pragma: no cover - depends on platform
                raise BrokerError("MetaTrader5 package not installed: pip install -e '.[mt5]' (Windows only)") from e
            self._mt5 = mt5
        return self._mt5

    def connect(self) -> AccountInfo:
        mt5 = self._lib()
        kwargs: dict[str, Any] = {"timeout": self.timeout_ms}
        if self.path:
            kwargs["path"] = self.path
        if self.login:
            kwargs.update(login=self.login, password=self.password or "", server=self.server or "")
        if not mt5.initialize(**kwargs):
            raise BrokerError(f"mt5.initialize failed: {describe_init_error(mt5.last_error())}")
        try:
            account = self._read_account(mt5)
            enforce_live_guard(account, self.allow_live)
        except Exception:
            mt5.shutdown()
            raise
        self._account = account
        self.connected = True
        # Best effort: an unmeasurable offset (closed market, unknown symbol) must not block connecting.
        try:
            self.refresh_server_offset()
        except BrokerError:
            self.server_offset = ServerTimeOffset(None, None, False, "reference symbol unavailable")
        return account

    def refresh_server_offset(self, symbol: str | None = None) -> ServerTimeOffset:
        """Re-measure the broker clock. Call after a reconnect and around DST changes."""
        mt5 = self._lib()
        sym = symbol or self.reference_symbol
        raw = mt5.symbol_info_tick(sym)
        if raw is None:
            raise BrokerError(f"symbol_info_tick({sym}) failed: {mt5.last_error()}")
        self.server_offset = measure_offset(raw.time)
        return self.server_offset

    def disconnect(self) -> None:
        if self.connected:
            self._lib().shutdown()
        self.connected = False
        self._account = None

    def _read_account(self, mt5: Any) -> AccountInfo:
        info = mt5.account_info()
        if info is None:
            raise BrokerError(f"mt5.account_info returned None: {mt5.last_error()}")
        term = mt5.terminal_info()
        algo = bool(getattr(term, "trade_allowed", False)) if term is not None else False
        return AccountInfo(
            login=int(info.login),
            server=str(info.server),
            mode=_TRADE_MODE.get(int(info.trade_mode), AccountMode.REAL),  # unknown → treat as REAL (safe side)
            balance=float(info.balance),
            equity=float(info.equity),
            currency=str(info.currency),
            leverage=int(info.leverage),
            algo_trading=algo,
        )

    def account(self) -> AccountInfo:
        self._require_connected()
        self._account = self._read_account(self._lib())
        return self._account

    def _require_connected(self) -> None:
        if not self.connected:
            raise BrokerError("not connected; call connect() first")

    # --- market data -------------------------------------------------------------

    def symbol_info(self, symbol: str) -> SymbolInfo:
        self._require_connected()
        mt5 = self._lib()
        info = mt5.symbol_info(symbol)
        if info is None:
            raise BrokerError(f"symbol {symbol} unknown to terminal: {mt5.last_error()}")
        if not getattr(info, "visible", True):
            if not mt5.symbol_select(symbol, True):
                raise BrokerError(f"symbol_select({symbol}) failed: {mt5.last_error()}")
            info = mt5.symbol_info(symbol)
        self._digits[symbol] = int(info.digits)
        return SymbolInfo(
            symbol=symbol,
            digits=int(info.digits),
            point=float(info.point),
            volume_min=float(info.volume_min),
            volume_step=float(info.volume_step),
            stops_level_points=int(info.trade_stops_level),
            spread_points=int(info.spread),
            trade_allowed=int(getattr(info, "trade_mode", 4)) != 0,  # SYMBOL_TRADE_MODE_DISABLED == 0
            tick_size=float(getattr(info, "trade_tick_size", 0.0) or info.point),
            tick_value=float(getattr(info, "trade_tick_value", 0.0)),
            volume_max=float(getattr(info, "volume_max", 0.0)),
            contract_size=float(getattr(info, "trade_contract_size", 0.0)),
        )

    def tick(self, symbol: str) -> Tick:
        self._require_connected()
        mt5 = self._lib()
        t = mt5.symbol_info_tick(symbol)
        if t is None:
            raise BrokerError(f"symbol_info_tick({symbol}) failed: {mt5.last_error()}")
        if symbol not in self._digits:
            self.symbol_info(symbol)
        d = self._digits[symbol]
        # t.time is the broker's wall clock, not UTC (D13). Convert it, and say so when we cannot.
        wall = epoch_to_server_wall(t.time)
        offset = self.server_offset.minutes
        as_utc = server_to_utc(wall, offset) if offset is not None else wall.replace(tzinfo=UTC)
        return Tick(
            symbol=symbol,
            bid=round(float(t.bid), d),
            ask=round(float(t.ask), d),
            time_utc=as_utc,
            time_server=wall,
            server_utc_offset_min=offset,
        )

    def _filling(self, symbol: str) -> int:
        mt5 = self._lib()
        info = mt5.symbol_info(symbol)
        fm = int(getattr(info, "filling_mode", 0)) if info is not None else 0
        if fm & 1:  # SYMBOL_FILLING_FOK
            return int(mt5.ORDER_FILLING_FOK)
        if fm & 2:  # SYMBOL_FILLING_IOC
            return int(mt5.ORDER_FILLING_IOC)
        return int(mt5.ORDER_FILLING_RETURN)

    # --- trading -----------------------------------------------------------------

    def market_order(self, req: OrderRequest) -> OrderResult:
        self._require_connected()
        mt5 = self._lib()
        tick = self.tick(req.symbol)
        is_long = req.side is Side.LONG
        price = tick.ask if is_long else tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": req.symbol,
            "volume": float(req.volume),
            "type": mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL,
            "price": float(price),
            "sl": float(req.stop_price),
            "tp": float(req.take_price or 0.0),
            "deviation": int(req.deviation_points),
            "magic": int(req.magic),
            "comment": req.comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(req.symbol),
        }
        return self._send(request, price_requested=price)

    def modify_sltp(self, ticket: int, sl: float, tp: float | None) -> OrderResult:
        self._require_connected()
        mt5 = self._lib()
        pos = self.position(ticket)
        if pos is None:
            return OrderResult(ok=False, retcode=10036, retcode_desc="POSITION_CLOSED")
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": int(ticket),
            "symbol": pos.symbol,
            "sl": float(sl),
            "tp": float(tp if tp is not None else pos.tp),
        }
        return self._send(request, position_ticket=ticket)

    def close_position(self, ticket: int, deviation_points: int = 20) -> OrderResult:
        self._require_connected()
        mt5 = self._lib()
        pos = self.position(ticket)
        if pos is None:
            return OrderResult(ok=False, retcode=10036, retcode_desc="POSITION_CLOSED")
        tick = self.tick(pos.symbol)
        closing_long = pos.side is Side.LONG
        price = tick.bid if closing_long else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": int(ticket),
            "symbol": pos.symbol,
            "volume": float(pos.volume),
            "type": mt5.ORDER_TYPE_SELL if closing_long else mt5.ORDER_TYPE_BUY,
            "price": float(price),
            "deviation": int(deviation_points),
            "magic": int(pos.magic),
            "comment": "close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(pos.symbol),
        }
        return self._send(request, price_requested=price, position_ticket=ticket)

    def _send(
        self, request: dict[str, Any], *, price_requested: float | None = None, position_ticket: int | None = None
    ) -> OrderResult:
        mt5 = self._lib()
        res = mt5.order_send(request)
        if res is None:
            code, desc = mt5.last_error()
            return OrderResult(
                ok=False, retcode=int(code), retcode_desc=f"SEND_FAILED:{desc}", raw={"request": request}
            )
        retcode = int(res.retcode)
        ok = retcode in RETCODE_OK
        deal = int(res.deal) if getattr(res, "deal", 0) else None
        order = int(res.order) if getattr(res, "order", 0) else None
        pos_ticket = position_ticket
        if ok and pos_ticket is None and request.get("action") == mt5.TRADE_ACTION_DEAL:
            pos_ticket = self._position_id_for_deal(deal) or order
        return OrderResult(
            ok=ok,
            retcode=retcode,
            retcode_desc=describe_retcode(retcode),
            order_ticket=order,
            deal_ticket=deal,
            position_ticket=pos_ticket,
            price_requested=price_requested,
            price_filled=float(res.price) if ok and getattr(res, "price", 0) else None,
            volume=float(res.volume) if ok and getattr(res, "volume", 0) else None,
            raw={"request": request, "result": _as_dict(res)},
        )

    def _position_id_for_deal(self, deal_ticket: int | None) -> int | None:
        if not deal_ticket:
            return None
        mt5 = self._lib()
        deals = mt5.history_deals_get(ticket=deal_ticket)
        if deals:
            pid = getattr(deals[0], "position_id", 0)
            return int(pid) if pid else None
        return None

    # --- positions ---------------------------------------------------------------

    def position(self, ticket: int) -> Position | None:
        self._require_connected()
        rows = self._lib().positions_get(ticket=int(ticket))
        if not rows:
            return None
        return self._to_position(rows[0])

    def positions(self, symbol: str | None = None, magic: int | None = None) -> list[Position]:
        self._require_connected()
        mt5 = self._lib()
        rows = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        out = [self._to_position(r) for r in (rows or ())]
        if magic is not None:
            out = [p for p in out if p.magic == magic]
        return out

    @staticmethod
    def _to_position(p: Any) -> Position:
        return Position(
            ticket=int(p.ticket),
            symbol=str(p.symbol),
            side=Side.LONG if int(p.type) == 0 else Side.SHORT,  # POSITION_TYPE_BUY == 0
            volume=float(p.volume),
            price_open=float(p.price_open),
            sl=float(p.sl),
            tp=float(p.tp),
            profit=float(p.profit),
            magic=int(p.magic),
            comment=str(getattr(p, "comment", "")),
        )

    def wait_position(self, ticket: int, timeout_s: float = 3.0, poll_s: float = 0.2) -> Position | None:
        deadline = time.monotonic() + timeout_s
        while True:
            pos = self.position(ticket)
            if pos is not None or time.monotonic() >= deadline:
                return pos
            time.sleep(poll_s)
