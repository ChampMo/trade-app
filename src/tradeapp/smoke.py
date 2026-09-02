"""Phase 0 smoke flow (P0-05).

Open one small position, prove the stop loss sits at the broker, close it, and journal every step.
This is the single documented place outside the Risk Engine that may call `market_order` (rule 02),
and it refuses anything but a DEMO account (rule 8).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tradeapp.contracts import AccountMode, OrderRequest, OrderResult, Position, Side, SymbolInfo, Tick
from tradeapp.journal import Journal

SOURCE = "smoke"


@dataclass
class SmokeReport:
    ok: bool = False
    client_ref: str = ""
    steps: list[str] = field(default_factory=list)
    position_ticket: int | None = None
    sl_verified: bool | None = None
    open_slippage_points: float | None = None
    close_slippage_points: float | None = None
    pnl: float | None = None
    error: str | None = None

    def step(self, text: str) -> None:
        self.steps.append(text)


def compute_stops(side: Side, tick: Tick, sym: SymbolInfo, min_points: int = 200) -> tuple[float, float]:
    """SL at max(broker stops level + spread + margin, min_points) away; TP at twice that (2R)."""
    dist_points = max(sym.stops_level_points + sym.spread_points + 5, min_points)
    dist = dist_points * sym.point
    if side is Side.LONG:
        sl, tp = tick.ask - dist, tick.ask + 2 * dist
    else:
        sl, tp = tick.bid + dist, tick.bid - 2 * dist
    return round(sl, sym.digits), round(tp, sym.digits)


def slippage_points(res: OrderResult, side: Side, point: float, closing: bool = False) -> float | None:
    """Positive = worse than requested for the trader."""
    if res.price_requested is None or res.price_filled is None:
        return None
    diff = (res.price_filled - res.price_requested) / point
    worse_when_higher = (side is Side.LONG) != closing
    return round(diff if worse_when_higher else -diff, 1)


def _order_row(journal: Journal, ref: str, kind: str, req: OrderRequest | None, res: OrderResult, **extra: Any) -> int:
    return journal.order(
        client_ref=ref,
        kind=kind,
        symbol=req.symbol if req else extra.pop("symbol", ""),
        side=req.side.value if req else extra.pop("side", None),
        volume=req.volume if req else res.volume,
        magic=req.magic if req else extra.pop("magic", None),
        comment=req.comment if req else kind,
        price_requested=res.price_requested,
        price_filled=res.price_filled,
        sl=req.stop_price if req else extra.pop("sl", None),
        tp=req.take_price if req else extra.pop("tp", None),
        ok=res.ok,
        retcode=res.retcode,
        retcode_desc=res.retcode_desc,
        order_ticket=res.order_ticket,
        deal_ticket=res.deal_ticket,
        position_ticket=res.position_ticket,
        raw_request=res.raw.get("request") if res.raw else None,
        raw_result=res.raw.get("result") if res.raw else None,
        **extra,
    )


def verify_stop(
    broker: Any, journal: Journal, ref: str, ticket: int, sl: float, tp: float | None
) -> tuple[bool, Position | None]:
    """Rule 03 after the fill: SL must be on the position. Try to set it once; otherwise close immediately."""
    pos = broker.wait_position(ticket) if hasattr(broker, "wait_position") else broker.position(ticket)
    if pos is None:
        journal.event("CRIT", SOURCE, "position not found after fill", {"ticket": ticket})
        return False, None
    if pos.sl > 0:
        journal.event("INFO", SOURCE, "SL verified at broker", {"ticket": ticket, "sl": pos.sl})
        return True, pos
    journal.event("WARN", SOURCE, "position filled without SL; setting it now", {"ticket": ticket, "sl": sl})
    res = broker.modify_sltp(ticket, sl, tp)
    _order_row(journal, ref, "modify", None, res, symbol=pos.symbol, side=pos.side.value, magic=pos.magic, sl=sl, tp=tp)
    pos = broker.position(ticket)
    if res.ok and pos is not None and pos.sl > 0:
        journal.event("INFO", SOURCE, "SL set and verified at broker", {"ticket": ticket, "sl": pos.sl})
        return True, pos
    journal.event(
        "CRIT",
        SOURCE,
        "cannot set SL at broker; closing position now (rule 03)",
        {"ticket": ticket, "retcode": res.retcode_desc},
    )
    close = broker.close_position(ticket)
    _order_row(
        journal,
        ref,
        "close",
        None,
        close,
        symbol=pos.symbol if pos else "",
        side=pos.side.value if pos else None,
        magic=pos.magic if pos else None,
    )
    return False, None


def run_smoke(
    broker: Any,
    journal: Journal,
    *,
    symbol: str = "EURUSD",
    volume: float = 0.01,
    hold_seconds: float = 3.0,
    magic: int = 100_000,
    side: Side = Side.LONG,
    sleep: Callable[[float], None] = time.sleep,
) -> SmokeReport:
    ref = f"smoke-{uuid.uuid4().hex[:8]}"
    report = SmokeReport(client_ref=ref)
    journal.event("INFO", SOURCE, "smoke start", {"ref": ref, "symbol": symbol, "volume": volume, "side": side.value})
    try:
        acct = broker.connect()
        report.step(f"connected {acct.login}@{acct.server} mode={acct.mode.value} equity={acct.equity:.2f}")
        journal.event(
            "INFO",
            SOURCE,
            "connected",
            {
                "login": acct.login,
                "server": acct.server,
                "mode": acct.mode.value,
                "equity": acct.equity,
                "algo_trading": acct.algo_trading,
            },
        )
        if acct.mode is not AccountMode.DEMO:
            raise RuntimeError(f"smoke runs on DEMO only, account mode is {acct.mode.value}")
        if not acct.algo_trading:
            raise RuntimeError("terminal has Algo Trading disabled; enable it on the MT5 toolbar")

        sym = broker.symbol_info(symbol)
        tick = broker.tick(symbol)
        report.step(
            f"{symbol} bid={tick.bid} ask={tick.ask} spread={sym.spread_points}pt stops_level={sym.stops_level_points}pt"
        )
        journal.event(
            "INFO",
            SOURCE,
            "symbol",
            {
                "symbol": symbol,
                "bid": tick.bid,
                "ask": tick.ask,
                "spread_points": sym.spread_points,
                "stops_level_points": sym.stops_level_points,
                "point": sym.point,
                "digits": sym.digits,
            },
        )
        if not sym.trade_allowed:
            raise RuntimeError(f"trading disabled for {symbol}")
        if volume < sym.volume_min:
            raise RuntimeError(f"volume {volume} below symbol minimum {sym.volume_min}")

        sl, tp = compute_stops(side, tick, sym)
        req = OrderRequest(
            symbol=symbol, side=side, volume=volume, stop_price=sl, take_price=tp, magic=magic, comment="smoke"
        )
        res = broker.market_order(req)
        open_slip = slippage_points(res, side, sym.point)
        order_id = _order_row(journal, ref, "open", req, res, slippage_points=open_slip)
        report.open_slippage_points = open_slip
        if not res.ok:
            raise RuntimeError(f"open rejected: {res.retcode_desc}")
        report.position_ticket = res.position_ticket
        report.step(f"opened ticket={res.position_ticket} fill={res.price_filled} slippage={open_slip}pt")

        verified, pos = verify_stop(broker, journal, ref, res.position_ticket, sl, tp)
        report.sl_verified = verified
        journal.update_order(order_id, sl_verified=verified)
        if not verified:
            raise RuntimeError("stop loss could not be verified at broker; position closed")
        report.step(f"SL verified at broker sl={pos.sl} tp={pos.tp}")

        sleep(hold_seconds)
        close = broker.close_position(res.position_ticket)
        close_slip = slippage_points(close, side, sym.point, closing=True)
        _order_row(
            journal, ref, "close", None, close, symbol=symbol, side=side.value, magic=magic, slippage_points=close_slip
        )
        report.close_slippage_points = close_slip
        if not close.ok:
            raise RuntimeError(f"close rejected: {close.retcode_desc}; position {res.position_ticket} still open")
        still_open = broker.position(res.position_ticket)
        if still_open is not None:
            raise RuntimeError(f"position {res.position_ticket} still open after close")
        after = broker.account()
        report.pnl = round(after.balance - acct.balance, 2)
        report.step(f"closed fill={close.price_filled} slippage={close_slip}pt pnl={report.pnl}")
        journal.event(
            "INFO",
            SOURCE,
            "smoke done",
            {"ref": ref, "pnl": report.pnl, "open_slippage_points": open_slip, "close_slippage_points": close_slip},
        )
        report.ok = True
    except Exception as e:  # noqa: BLE001 - the whole point is to journal whatever went wrong
        report.error = str(e)
        report.step(f"FAILED: {e}")
        journal.event("CRIT", SOURCE, "smoke failed", {"ref": ref, "error": str(e), "type": type(e).__name__})
    finally:
        try:
            broker.disconnect()
        except Exception as e:  # noqa: BLE001
            journal.event("WARN", SOURCE, "disconnect failed", {"error": str(e)})
    return report
