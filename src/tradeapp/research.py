"""Running backtests from the UI, one at a time, without touching the trading loop (P2-07).

The core loop and a backtest have nothing to do with each other: the backtest reads history from
its own SQLite file, builds its own simulated broker, and writes one row to the journal when it
finishes. It never sees the live broker and cannot place an order, which is what makes it safe to
start from a button.

Two rules make it safe to start from a *web* button specifically:

- **one at a time.** A backtest is CPU-bound and the loop shares the machine. Queuing them would
  let a click-happy afternoon starve the thing that is actually trading.
- **it never raises into the loop.** A failed job is a recorded status, not an exception anywhere
  near the core.
"""

from __future__ import annotations

import threading
import traceback
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from tradeapp.config import resolve_data_path

SOURCE = "research"
MAX_MONTE_CARLO = 5_000


@dataclass
class BacktestJob:
    id: int
    params: dict[str, Any]
    status: str = "queued"  # queued | running | done | failed
    started_utc: datetime | None = None
    finished_utc: datetime | None = None
    run_id: int | None = None
    error: str | None = None
    summary: str | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["started_utc"] = self.started_utc.isoformat() if self.started_utc else None
        d["finished_utc"] = self.finished_utc.isoformat() if self.finished_utc else None
        return d


class BacktestRunner:
    """Owns the one worker thread a backtest is allowed to have."""

    def __init__(self, journal_path: str, history_db: str = "data/history.db") -> None:
        self.journal_path = journal_path
        self.history_db = str(resolve_data_path(history_db))
        self._lock = threading.Lock()
        self._jobs: dict[int, BacktestJob] = {}
        self._next_id = 1
        self._thread: threading.Thread | None = None

    # --- queries ------------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def job(self, job_id: int) -> BacktestJob | None:
        return self._jobs.get(job_id)

    def jobs(self, limit: int = 20) -> list[BacktestJob]:
        return sorted(self._jobs.values(), key=lambda j: j.id, reverse=True)[:limit]

    # --- launching ----------------------------------------------------------------

    def start(self, params: dict[str, Any]) -> BacktestJob:
        with self._lock:
            if self.busy:
                raise RuntimeError("a backtest is already running; wait for it to finish")
            job = BacktestJob(id=self._next_id, params=dict(params))
            self._jobs[job.id] = job
            self._next_id += 1
            self._thread = threading.Thread(target=self._run, args=(job,), name=f"backtest-{job.id}", daemon=True)
            self._thread.start()
            return job

    def _run(self, job: BacktestJob) -> None:
        job.status, job.started_utc = "running", datetime.now(UTC)
        try:
            job.run_id, job.summary = self._execute(job.params)
            job.status = "done"
        except Exception as e:  # noqa: BLE001 - a failed backtest is a status, never a crash
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            job.finished_utc = datetime.now(UTC)

    def _execute(self, params: dict[str, Any]) -> tuple[int, str]:
        from tradeapp.backtest import CostModel, gate_report, monte_carlo, run_backtest, save_run
        from tradeapp.contracts import TF
        from tradeapp.data import BarStore
        from tradeapp.journal import Journal
        from tradeapp.strategies import create

        symbol = params.get("symbol", "EURUSD")
        tf = TF(str(params.get("timeframe", "H4")).upper())
        strategy = params.get("strategy", "ema_cross")
        warmup = int(params.get("warmup", 100))
        strategy_params = dict(params.get("params") or {})

        bars = BarStore(self.history_db).load(symbol, tf)
        if len(bars) <= warmup + 1:
            raise ValueError(f"only {len(bars)} bars stored for {symbol} {tf.value}; sync history first")

        costs = CostModel(
            spread_points=int(params.get("spread_points", 20)),
            use_bar_spread=bool(params.get("use_bar_spread", True)),
            slippage_points=float(params.get("slippage_points", 0.3)),
            commission_per_lot_round_trip=float(params.get("commission", 0.0)),
        )
        result = run_backtest(
            bars,
            [create(strategy, **strategy_params)],
            symbol=symbol,
            timeframe=tf,
            costs=costs,
            start_balance=float(params.get("balance", 10_000.0)),
            warmup=warmup,
        )

        wf = None
        if params.get("walk_forward"):
            from datetime import timedelta

            from tradeapp.backtest import walk_forward as run_walk_forward

            # One parameter set, not a grid. This answers "does it hold up out of sample", which
            # is the question worth asking; searching a grid for the best past is the other thing.
            wf = run_walk_forward(
                bars,
                build=lambda prm: [create(strategy, **prm)],
                param_grid=[strategy_params or {}],
                train=timedelta(days=180),
                test=timedelta(days=60),
                step=timedelta(days=60),
                symbol=symbol,
                timeframe=tf,
                costs=costs,
                start_balance=float(params.get("balance", 10_000.0)),
                warmup=warmup,
            )

        mc = gates = None
        if result.stats.trades:
            runs = min(int(params.get("monte_carlo", 1000)), MAX_MONTE_CARLO)
            mc = monte_carlo(result.trades, result.start_balance, runs=runs)
            gates = gate_report(result, mc, wf)

        # Its own Journal on purpose: this runs on a worker thread, and sharing the loop's handle
        # across threads is the kind of thing that works until the day it does not.
        journal = Journal(self.journal_path)
        run_id = save_run(
            journal,
            result,
            strategy=strategy,
            params={"tf": tf.value, "warmup": warmup, **strategy_params},
            costs={
                "spread_points": costs.spread_points,
                "use_bar_spread": costs.use_bar_spread,
                "slippage_points": costs.slippage_points,
                "commission_per_lot_round_trip": costs.commission_per_lot_round_trip,
            },
            label=params.get("label") or "from the UI",
            walk_forward=wf,
            monte_carlo=mc,
            gates=gates,
        )
        journal.event("INFO", SOURCE, f"backtest run #{run_id} finished", {"strategy": strategy, "symbol": symbol})
        return run_id, result.summary()


def _iso(dt) -> str | None:
    """Naive journal timestamps are UTC (D13); the wire format has to say so or browsers guess."""
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def run_dict(run) -> dict:
    """A stored run as the UI wants it. Trades are counted, not shipped: a run can hold hundreds."""
    return {
        "id": run.id,
        "ts_utc": _iso(run.ts_utc),
        "label": run.label,
        "strategy": run.strategy,
        "params": run.params,
        "symbol": run.symbol,
        "timeframe": run.timeframe,
        "data_from": _iso(run.data_from),
        "data_to": _iso(run.data_to),
        "bars": run.bars,
        "start_balance": run.start_balance,
        "end_balance": run.end_balance,
        "costs": run.costs,
        "stats": run.stats,
        "walk_forward": run.walk_forward,
        "monte_carlo": run.monte_carlo,
        "gates": run.gates,
        "killed": run.killed,
        "rejections": run.rejections,
        "trade_count": len(run.trades or []),
    }


LIMIT_REASONS = {
    "risk_pct": "per trade, before confidence and the AI size multiplier",
    "daily_loss_limit_pct": "measured against equity at the start of the broker's day (D20)",
    "max_drawdown_pct": "measured against peak equity, which survives restarts (D21)",
    "max_open_risk_pct": "what every open stop would cost if all were hit at once",
    "max_positions": "across every strategy",
    "max_currency_exposure": "net units of one currency, so three EUR longs are one risk",
    "max_correlated_units": "copies of the same bet, counting correlation (D23)",
    "correlation_floor": "correlations weaker than this are treated as noise",
    "max_margin_use_pct": "share of free margin one order may tie up (D23)",
    "strategy_max_open_risk_pct": "one strategy's share of open risk",
    "strategy_daily_loss_pct": "one strategy's daily loss budget",
    "min_stop_buffer_points": "added to the broker's own minimum stop distance",
    "opposing_bias_size_penalty": "the AI may shrink a position, never enlarge one (D6)",
    "max_size_mult": "hard ceiling on whatever the AI layer asks for",
}


def limits_dict(limits) -> list[dict]:
    """The risk limits as rows, each with the plain-English reason it exists (D3).

    Read-only on purpose. A limit is a decision recorded in DECISIONS.md, and a UI that could edit
    one would turn a deliberate act into a slider you nudge after a bad afternoon.
    """
    rows = [
        {"name": name, "value": getattr(limits, name), "why": reason}
        for name, reason in LIMIT_REASONS.items()
        if getattr(limits, name, None) is not None
    ]
    rows.append(
        {
            "name": "trading_hours_utc",
            "value": f"{limits.trading_start_utc:%H:%M}-{limits.trading_end_utc:%H:%M}",
            "why": "outside this window nothing opens",
        }
    )
    return rows
