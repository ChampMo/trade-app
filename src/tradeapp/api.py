"""Local HTTP + WebSocket API. The UI talks to the core only through this (D7).

Binding is localhost by design and there is no authentication, because there is no network path
to reach it: the UI and the core run on the same machine. That assumption is load-bearing, so the
serve helper refuses a non-loopback host rather than letting a typo expose a kill switch to a LAN.

Everything that changes state goes through `CoreService`, which owns the only lock.
"""

from __future__ import annotations

import asyncio
import ipaddress
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tradeapp.contracts import TF
from tradeapp.journal import Journal
from tradeapp.lifecycle import Lifecycle
from tradeapp.research import BacktestRunner, limits_dict, run_dict
from tradeapp.service import CoreService


class ReasonBody(BaseModel):
    """Every state change carries a reason; it goes straight into the journal."""

    reason: str = Field(default="", max_length=500)


class BacktestBody(BaseModel):
    """What the Research page may ask for. Bounded on purpose: this endpoint starts real work."""

    strategy: str = Field(default="ema_cross", max_length=32)
    symbol: str = Field(default="EURUSD", max_length=16)
    timeframe: str = Field(default="H4", max_length=4)
    balance: float = Field(default=10_000.0, gt=0, le=10_000_000)
    warmup: int = Field(default=100, ge=10, le=1000)
    spread_points: int = Field(default=20, ge=0, le=500)
    use_bar_spread: bool = True
    slippage_points: float = Field(default=0.3, ge=0, le=100)
    commission: float = Field(default=0.0, ge=0, le=100)
    monte_carlo: int = Field(default=1000, ge=0, le=5000)
    label: str = Field(default="", max_length=64)


def _iso(dt) -> str | None:
    """Journal rows are naive UTC (D13). Say so in the wire format.

    Without the offset a browser reads `2026-09-03T03:48:10` as local time and every timestamp in
    the UI silently shifts by the machine's zone — seven hours, in Bangkok, under a column headed
    UTC. The journal is the record; it must not need a reader's timezone to be read correctly.
    """
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def _event_dict(e) -> dict:
    return {
        "id": e.id,
        "ts_utc": _iso(e.ts_utc),
        "severity": e.severity,
        "source": e.source,
        "message": e.message,
        "data": e.data,
    }


def _decision_dict(d) -> dict:
    return {
        "id": d.id,
        "ts_utc": _iso(d.ts_utc),
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
        "ts_utc": _iso(o.ts_utc),
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


def create_app(
    service: CoreService,
    journal: Journal | None = None,
    runner: BacktestRunner | None = None,
) -> FastAPI:
    journal = journal or service.core.journal
    lifecycle = Lifecycle(journal)
    # Research runs on its own thread and its own journal handle; it can read history and write a
    # result row, and it has no route to the broker at all.
    runner = runner or BacktestRunner(str(journal.path) if journal.path else ":memory:")
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

    # --- research (P2-07) -----------------------------------------------------------

    @app.get("/risk/limits")
    def risk_limits() -> dict:
        """Read-only. A limit is a decision (D3, CLAUDE.md rule 7), not a slider."""
        return {
            "state": service.engine_state.value,
            "editable": False,
            "why_not": "limits are decisions recorded in DECISIONS.md; changing one is an edit and a commit",
            "limits": limits_dict(service.core.limits),
        }

    @app.get("/backtest/runs")
    def backtest_runs(limit: int = 25, strategy: str | None = None) -> list[dict]:
        return [run_dict(r) for r in journal.backtest_runs(min(limit, 200), strategy)]

    @app.get("/backtest/runs/{run_id}")
    def backtest_run(run_id: int) -> dict:
        run = journal.backtest_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no backtest run #{run_id}")
        return {**run_dict(run), "trades": run.trades or []}

    @app.get("/backtest/runs/{run_id}/drift")
    def backtest_drift(run_id: int, days: int = 30, point: float = 0.00001) -> dict:
        from tradeapp import reports

        try:
            report = reports.build_drift(journal, run_id, days=min(days, 365), point=point)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {
            "run_id": report.run_id,
            "strategy": report.strategy,
            "days": report.days,
            "live_trades": report.live_trades,
            "backtest_trades": report.backtest_trades,
            "meaningful": report.meaningful,
            "live_slippage": report.live_slippage,
            "metrics": [
                {"name": m.name, "backtest": m.backtest, "live": m.live, "gap": m.gap, "worse": m.worse, "note": m.note}
                for m in report.metrics
            ],
            "diverging": [m.name for m in report.diverging],
            "notes": report.notes,
            "markdown": reports.render_drift(report),
        }

    @app.get("/backtest/options")
    def backtest_options() -> dict:
        """What can actually be replayed: the registered strategies, and the bars that exist.

        The Research form is built from this rather than from free text. A backtest of a symbol
        with no stored history is not a typo to be corrected after a failed run — it is a choice
        that should not have been offered.
        """
        from tradeapp.data import BarStore
        from tradeapp.strategies import discover

        store = BarStore(runner.history_db)
        data = []
        for symbol, timeframe, bars in store.symbols():
            try:
                tf = TF(timeframe)
            except ValueError:
                continue
            first, last = store.range(symbol, tf)
            data.append(
                {
                    "symbol": symbol,
                    "timeframe": tf.value,
                    "bars": bars,
                    "from": first.isoformat() if first else None,
                    "to": last.isoformat() if last else None,
                }
            )
        return {"strategies": sorted(discover()), "data": data}

    @app.get("/bars")
    def bars(
        symbol: str = "EURUSD",
        timeframe: str = "H4",
        start: str | None = None,
        end: str | None = None,
        limit: int = 400,
    ) -> dict:
        """Stored bars for charting. Read-only, from the history file the backtests replay.

        A window, never the whole store: 20,000 bars is megabytes of JSON and no chart can show
        them. The caller asks for the range it wants to draw.
        """
        from tradeapp.data import BarStore

        try:
            tf = TF(timeframe.upper())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"unknown timeframe {timeframe}") from e

        def moment(raw: str | None) -> datetime | None:
            if not raw:
                return None
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"cannot read the time {raw!r}") from e

        rows = BarStore(runner.history_db).load(symbol, tf, moment(start), moment(end), min(limit, 2000))
        return {
            "symbol": symbol,
            "timeframe": tf.value,
            "bars": [
                {
                    "t": b.time_utc.isoformat(),
                    "o": b.open,
                    "h": b.high,
                    "l": b.low,
                    "c": b.close,
                }
                for b in rows
            ],
        }

    @app.get("/backtest/jobs")
    def backtest_jobs(limit: int = 20) -> dict:
        return {"busy": runner.busy, "jobs": [j.as_dict() for j in runner.jobs(min(limit, 100))]}

    @app.get("/backtest/jobs/{job_id}")
    def backtest_job(job_id: int) -> dict:
        job = runner.job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"no backtest job #{job_id}")
        return job.as_dict()

    @app.post("/backtest")
    def start_backtest(body: BacktestBody) -> dict:
        """Starts real work, so it answers immediately with a job to poll rather than blocking."""
        return runner.start(body.model_dump()).as_dict()

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
