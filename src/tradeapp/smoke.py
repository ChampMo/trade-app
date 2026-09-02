"""Phase 0 smoke flow (P0-05).

Open one small position, prove the stop loss sits at the broker, close it, and journal every step.
It refuses anything but a DEMO account (rule 8), and it goes through the execution layer like
everything else, so the stop verification it proves is literally the code that runs in live trading.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tradeapp.contracts import AccountMode, OrderRequest, Side, SymbolInfo, Tick
from tradeapp.execution import Executor
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
    server_utc_offset_min: int | None = None
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

        offset = getattr(broker, "server_offset", None)
        if offset is not None:
            report.step(offset.describe())
            journal.event(
                "INFO",
                SOURCE,
                "server clock",
                {
                    "server_utc_offset_min": offset.minutes,
                    "tick_age_s": offset.tick_age_s,
                    "confident": offset.confident,
                    "note": offset.note,
                },
            )

        sym = broker.symbol_info(symbol)
        tick = broker.tick(symbol)
        report.server_utc_offset_min = tick.server_utc_offset_min
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
                # every row carrying broker time carries the offset that makes it comparable (D13)
                "tick_time_server": tick.time_server.isoformat() if tick.time_server else None,
                "tick_time_utc": tick.time_utc.isoformat(),
                "server_utc_offset_min": tick.server_utc_offset_min,
            },
        )
        if not sym.trade_allowed:
            raise RuntimeError(f"trading disabled for {symbol}")
        if volume < sym.volume_min:
            raise RuntimeError(f"volume {volume} below symbol minimum {sym.volume_min}")

        executor = Executor(broker, journal)
        sl, tp = compute_stops(side, tick, sym)
        order = OrderRequest(
            symbol=symbol, side=side, volume=volume, stop_price=sl, take_price=tp, magic=magic, comment="smoke"
        )

        opened = executor.send(order, client_ref=ref, point=sym.point)
        report.open_slippage_points = opened.slippage_points
        report.position_ticket = opened.position_ticket
        report.sl_verified = opened.sl_verified
        if not opened.ok:
            raise RuntimeError(f"open failed: {opened.detail}")
        report.step(
            f"opened ticket={opened.position_ticket} fill={opened.result.price_filled} "
            f"slippage={opened.slippage_points}pt attempts={opened.attempts}"
        )
        report.step(f"SL verified at broker sl={opened.position.sl} tp={opened.position.tp}")

        sleep(hold_seconds)
        closed = executor.close(opened.position_ticket, client_ref=ref, point=sym.point, reason="smoke complete")
        report.close_slippage_points = closed.slippage_points
        if not closed.ok:
            raise RuntimeError(f"close rejected: {closed.detail}; position {opened.position_ticket} still open")
        if broker.position(opened.position_ticket) is not None:
            raise RuntimeError(f"position {opened.position_ticket} still open after close")

        after = broker.account()
        report.pnl = round(after.balance - acct.balance, 2)
        report.step(f"closed fill={closed.result.price_filled} slippage={closed.slippage_points}pt pnl={report.pnl}")
        journal.event(
            "INFO",
            SOURCE,
            "smoke done",
            {
                "ref": ref,
                "pnl": report.pnl,
                "open_slippage_points": report.open_slippage_points,
                "close_slippage_points": report.close_slippage_points,
            },
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
