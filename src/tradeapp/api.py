"""Local HTTP + WebSocket API. The UI talks to the core only through this (D7).

Binding is localhost by design and there is no authentication, because there is no network path
to reach it: the UI and the core run on the same machine. That assumption is load-bearing, so the
serve helper refuses a non-loopback host rather than letting a typo expose a kill switch to a LAN.

Everything that changes state goes through `CoreService`, which owns the only lock.
"""

from __future__ import annotations

import asyncio
import ipaddress

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tradeapp.journal import Journal
from tradeapp.lifecycle import Lifecycle
from tradeapp.service import CoreService


class ReasonBody(BaseModel):
    """Every state change carries a reason; it goes straight into the journal."""

    reason: str = Field(default="", max_length=500)


def _event_dict(e) -> dict:
    return {
        "id": e.id,
        "ts_utc": e.ts_utc.isoformat(),
        "severity": e.severity,
        "source": e.source,
        "message": e.message,
        "data": e.data,
    }


def _decision_dict(d) -> dict:
    return {
        "id": d.id,
        "ts_utc": d.ts_utc.isoformat(),
        "strategy_id": d.strategy_id,
        "variant": d.variant,
        "symbol": d.symbol,
        "side": d.side,
        "confidence": d.confidence,
        "stop_price": d.stop_price,
        "take_price": d.take_price,
        "reason": d.reason,
        "verdict": d.verdict,
        "verdict_reason": d.verdict_reason,
        "size_lots": d.size_lots,
        "order_id": d.order_id,
        "tag": d.tag,
        "context": d.context,
        "ai": {
            "regime": d.ai_regime,
            "bias": d.ai_bias,
            "size_mult": d.ai_size_mult,
            "block": d.ai_block,
        },
    }


def _order_dict(o) -> dict:
    return {
        "id": o.id,
        "ts_utc": o.ts_utc.isoformat(),
        "client_ref": o.client_ref,
        "kind": o.kind,
        "symbol": o.symbol,
        "side": o.side,
        "volume": o.volume,
        "price_requested": o.price_requested,
        "price_filled": o.price_filled,
        "sl": o.sl,
        "tp": o.tp,
        "ok": o.ok,
        "retcode_desc": o.retcode_desc,
        "position_ticket": o.position_ticket,
        "slippage_points": o.slippage_points,
        "sl_verified": o.sl_verified,
    }


def create_app(service: CoreService, journal: Journal | None = None) -> FastAPI:
    journal = journal or service.core.journal
    lifecycle = Lifecycle(journal)
    app = FastAPI(title="trade-app core", version="0.1", docs_url="/docs")

    @app.exception_handler(RuntimeError)
    async def _runtime_error(_request, exc: RuntimeError) -> JSONResponse:
        # The state machine says no by raising; that is a 409, not a crash.
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    # --- reading ------------------------------------------------------------------

    @app.get("/status")
    def status() -> dict:
        return service.status()

    @app.get("/positions")
    def positions() -> list[dict]:
        return service.positions()

    @app.get("/events")
    def events(after_id: int = 0, limit: int = 200) -> list[dict]:
        return [_event_dict(e) for e in journal.events_since(after_id, min(limit, 1000))]

    @app.get("/decisions")
    def decisions(limit: int = 100) -> list[dict]:
        return [_decision_dict(d) for d in journal.recent_decisions(min(limit, 1000))]

    @app.get("/orders")
    def orders(limit: int = 100) -> list[dict]:
        return [_order_dict(o) for o in journal.orders_recent(min(limit, 1000))]

    @app.get("/strategies")
    def strategies() -> list[dict]:
        states = lifecycle.all_states()
        out = []
        for row in service.core.runtime.status():
            tf = row.get("timeframe")
            out.append(
                {
                    **row,
                    "timeframe": tf.value if hasattr(tf, "value") else tf,
                    "lifecycle": states.get(row["key"], "research"),
                }
            )
        return out

    @app.get("/ticks")
    def ticks() -> list[dict]:
        return service.recent_ticks()

    # --- control ------------------------------------------------------------------

    @app.post("/control/kill")
    def kill(body: ReasonBody = ReasonBody()) -> dict:
        return service.kill(body.reason.strip() or "manual kill from the UI")

    @app.post("/control/unlock")
    def unlock(body: ReasonBody = ReasonBody()) -> dict:
        reason = body.reason.strip()
        if not reason:
            raise HTTPException(status_code=400, detail="unlock requires a reason; it goes in the journal")
        return service.unlock(reason)

    @app.post("/control/pause")
    def pause(body: ReasonBody = ReasonBody()) -> dict:
        return service.pause(body.reason.strip())

    @app.post("/control/resume")
    def resume() -> dict:
        return service.resume()

    # --- live events ----------------------------------------------------------------

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        """Walks the journal forward by id. No pub/sub layer: the journal is already the truth."""
        await ws.accept()
        try:
            # Start from now, not from the beginning of time: a client that wants history asks
            # /events for it. Streaming a year of rows on connect would be a denial of service.
            latest = journal.tail_events(1)
            after = latest[-1].id if latest else 0
            await ws.send_json({"type": "hello", "after_id": after})
            while True:
                rows = journal.events_since(after, 200)
                if rows:
                    after = rows[-1].id
                    for e in rows:
                        await ws.send_json({"type": "event", **_event_dict(e)})
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            return
        except RuntimeError:
            return  # socket closed underneath us

    return app


def serve(service: CoreService, host: str = "127.0.0.1", port: int = 8001) -> None:
    """Run the API. Refuses a non-loopback host: there is no auth, so there must be no network."""
    import uvicorn

    if not _is_loopback(host):
        raise ValueError(
            f"refusing to bind {host}: this API has no authentication and exposes a kill switch. "
            "Keep it on 127.0.0.1 and reach it through an SSH tunnel or a reverse proxy if you need it remotely."
        )
    uvicorn.run(create_app(service), host=host, port=port, log_level="warning")


def _is_loopback(host: str) -> bool:
    if host in {"localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
