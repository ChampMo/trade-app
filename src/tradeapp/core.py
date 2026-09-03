"""The core loop: the one place where every other part is wired together.

Order matters here more than anywhere else in the codebase, and the order is safety first:

1. read the account and positions from the broker
2. reconcile, on a timer and always at startup
3. build a health snapshot and give the kill switch its chance to fire
4. only then, if the engine is RUNNING and reconcile is not frozen, look for a new closed bar
5. strategies produce Intents, the Risk Engine sizes or refuses them, the executor sends them

A tick that finds trouble never reaches step 5. That is the whole point: the checks are not
sprinkled through the trading path, they gate it.

The loop is driven one `tick()` at a time so tests can run a whole trading day in milliseconds
without patching the clock everywhere. `run()` is a thin wrapper that calls `tick()` and sleeps.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from tradeapp.broker.servertime import server_to_utc, utc_to_server
from tradeapp.context import Context
from tradeapp.contracts import TF, AccountInfo, Position
from tradeapp.execution import Executor
from tradeapp.journal import Journal
from tradeapp.reconcile import Reconciler
from tradeapp.risk import AIContext, EngineState, RiskContext, RiskEngine, RiskLimits
from tradeapp.risk.killswitch import KillLimits, KillSwitch, SystemHealth
from tradeapp.runtime import StrategyRuntime

SOURCE = "core"

PEAK_EQUITY = "peak_equity"
DAY_START_EQUITY = "day_start_equity"
DAY_KEY = "day_key"


@dataclass
class CoreConfig:
    symbol: str = "EURUSD"
    timeframe: TF = TF.H4
    history_bars: int = 300
    reconcile_every_s: float = 60.0
    tick_interval_s: float = 5.0
    calendar_db: str = "data/calendar.db"


@dataclass
class TickReport:
    at_utc: datetime
    state: EngineState
    equity: float = 0.0
    reconciled: bool = False
    killed: bool = False
    frozen: bool = False
    new_bar: bool = False
    signals: int = 0
    approved: int = 0
    rejected: int = 0
    sent: int = 0
    notes: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.notes.append(text)


class Core:
    def __init__(
        self,
        broker,
        journal: Journal,
        *,
        runtime: StrategyRuntime,
        config: CoreConfig | None = None,
        limits: RiskLimits | None = None,
        magic_base: int = 100_000,
        news=None,
        analyst=None,
        notifier=None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.broker = broker
        self.journal = journal
        self.runtime = runtime
        self.config = config or CoreConfig()
        self.limits = limits or RiskLimits()
        self._now = now
        self._sleep = sleep

        self.engine = RiskEngine(
            self.limits,
            journal=journal,
            news=news,
            magic_base=magic_base,
            # The real bridge can ask MT5 what an order would tie up. Brokers that cannot say
            # (fake, paper, backtest) leave this None and the engine falls back to arithmetic.
            margin_required=getattr(broker, "margin_required", None),
        )
        self.executor = Executor(broker, journal, now=now, sleep=sleep)
        self.reconciler = Reconciler(broker, journal, now=now)
        self.analyst = analyst
        self.kill = KillSwitch(KillLimits.from_risk(self.limits), journal=journal, notifier=notifier)

        self.account: AccountInfo | None = None
        self.positions: list[Position] = []
        # Equity marks are held in memory and written through to the journal. The journal is still
        # the durable record (D21); reading it on every tick was costing four SQL round trips a bar,
        # which is most of a backtest's runtime and buys nothing.
        self._peak_equity: float = 0.0
        self._day_start_equity: float = 0.0
        self._day_key: str | None = None
        self.last_bar_utc: datetime | None = None
        self.last_reconcile_at: datetime | None = None
        self.started = False

    # --- lifecycle ----------------------------------------------------------------

    def start(self) -> AccountInfo:
        """Connect, take stock of the account, and reconcile before touching anything."""
        self.account = self.broker.connect()
        self.journal.event(
            "INFO",
            SOURCE,
            f"core starting on {self.account.login}@{self.account.server}",
            {
                "mode": self.account.mode.value,
                "equity": self.account.equity,
                "algo_trading": self.account.algo_trading,
                "symbol": self.config.symbol,
                "timeframe": self.config.timeframe.value,
                "strategies": [s.key for s in self.runtime.slots],
            },
        )
        # Fix magic numbers up front so they are stable across restarts and reconcile can name them.
        for slot in self.runtime.slots:
            self.engine.magic_for(slot.key)

        self._load_equity_marks()
        self._refresh_equity_marks(self.account.equity)
        self._reconcile(force=True)
        self.kill.state = EngineState.RUNNING
        self.started = True
        return self.account

    def shutdown(self) -> None:
        """Stop the loop. Open positions are left alone: their stops live at the broker (rule 03)."""
        self.journal.event(
            "INFO",
            SOURCE,
            "core stopping",
            {"open_positions": len(self.positions), "state": self.kill.state.value},
        )
        try:
            self.broker.disconnect()
        except Exception as e:  # noqa: BLE001
            self.journal.event("WARN", SOURCE, "disconnect failed", {"error": str(e)})
        self.started = False

    def run(self, should_stop: Callable[[], bool] = lambda: False, max_ticks: int | None = None) -> int:
        if not self.started:
            self.start()
        ticks = 0
        try:
            while not should_stop() and (max_ticks is None or ticks < max_ticks):
                self.tick()
                ticks += 1
                self._sleep(self.config.tick_interval_s)
        finally:
            self.shutdown()
        return ticks

    # --- one iteration ------------------------------------------------------------

    def tick(self) -> TickReport:
        now = self._now()
        report = TickReport(at_utc=now, state=self.kill.state)

        # 1. is the terminal still there, and what does it say
        fresh = True
        reconnect = getattr(self.broker, "ensure_connected", None)
        if reconnect is not None and not reconnect():
            fresh = False
            report.note("terminal is not reachable; will retry next tick")
        try:
            self.account = self.broker.account()
            self.positions = self.broker.positions()
            self.executor.last_broker_contact_utc = now
        except Exception as e:  # noqa: BLE001 - losing the broker is a condition, not a crash
            fresh = False
            report.note(f"broker unreadable: {e}")
            self.journal.event("WARN", SOURCE, "could not read the account this tick", {"error": str(e)})
            # Fall through: the kill switch still evaluates, and stale contact time is a trigger.

        equity = self.account.equity if self.account else 0.0
        report.equity = equity
        self._refresh_equity_marks(equity)

        # 2. reconcile on its own timer, and always when we have never done it
        if self._reconcile_due(now):
            self._reconcile()
            report.reconciled = True
        report.frozen = self.reconciler.frozen

        # 3. the brake gets its chance before anything can open
        killed = self.kill.check_and_trip(self._health(now, equity), self.broker)
        report.state = self.kill.state
        if killed is not None:
            report.killed = True
            report.note(killed.summary())
            return report

        if self.kill.state is not EngineState.RUNNING:
            report.note(f"engine is {self.kill.state.value}; not trading")
            return report
        if self.reconciler.frozen:
            report.note(f"frozen by reconcile: {self.reconciler.freeze_reason}")
            return report
        if not fresh:
            # Every portfolio limit — position count, netting, open risk — is computed from the
            # position list. Opening against a stale one is worse than missing a bar.
            report.note("no fresh view of the account; not opening anything this tick")
            return report

        # 4. is there a new closed bar to act on
        ctx = self._context(now)
        if ctx is None or not ctx.bars:
            report.note("no bars available")
            return report
        if self.last_bar_utc is not None and ctx.bar.time_utc <= self.last_bar_utc:
            return report  # same bar as last time; strategies decide once per closed bar
        self.last_bar_utc = ctx.bar.time_utc
        report.new_bar = True

        # A new closed bar is exactly when a fresh view is worth paying for, and the only moment
        # it can change anything. Refreshing on a wall clock would spend the budget on bars that
        # nobody is going to trade.
        if self.analyst is not None:
            outcome = self.analyst.refresh(ctx)
            if not outcome.used_model:
                report.note(f"ai: {outcome.detail}")
            ctx = replace(ctx, ai=self.analyst.view)

        # 5. strategies -> risk engine -> broker
        signals = self.runtime.on_bar(ctx)
        report.signals = len(signals)
        if not signals:
            return report

        risk_ctx = RiskContext(
            account=self.account,
            symbols={self.config.symbol: ctx.symbol_info},
            tick=ctx.tick,
            positions=self.positions,
            now_utc=now,
            day_start_equity=self.day_start_equity,
            peak_equity=self.peak_equity,
            state=self.kill.state,
            ai=ctx.ai,
            strategy_day_pnl=self._strategy_day_pnl(now, ctx.symbol_info),
        )
        for signal in signals:
            decision = self.engine.evaluate(signal.intent, signal.key, risk_ctx, variant=signal.variant)
            if not decision.approved:
                report.rejected += 1
                report.note(f"{signal.key}: {decision.reason.value}")
                continue
            report.approved += 1
            result = self.executor.send(
                decision.order,
                decision_id=decision.decision_id,
                point=ctx.symbol_info.point if ctx.symbol_info else None,
            )
            if result.ok:
                report.sent += 1
                report.note(f"{signal.key}: {decision.size_lots} lots at {result.result.price_filled}")
                try:
                    # Re-read so the next signal in this same tick sizes against reality.
                    self.positions = self.broker.positions()
                except Exception as e:  # noqa: BLE001
                    self.journal.event("WARN", SOURCE, "could not re-read positions after a fill", {"error": str(e)})
                    return report
            else:
                report.note(f"{signal.key}: execution failed, {result.detail}")
        return report

    # --- health and equity marks --------------------------------------------------

    def _health(self, now: datetime, equity: float) -> SystemHealth:
        return SystemHealth(
            now_utc=now,
            equity=equity,
            day_start_equity=self.day_start_equity,
            peak_equity=self.peak_equity,
            last_broker_contact_utc=self.executor.last_broker_contact_utc,
            consecutive_rejects=self.executor.consecutive_rejects,
            **self.reconciler.health_inputs(),
        )

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    @property
    def day_start_equity(self) -> float:
        return self._day_start_equity

    def _load_equity_marks(self) -> None:
        """Read what a previous run left behind. Called once, at start."""
        self._peak_equity = float(self.journal.get_state(PEAK_EQUITY, 0.0) or 0.0)
        self._day_start_equity = float(self.journal.get_state(DAY_START_EQUITY, 0.0) or 0.0)
        self._day_key = self.journal.get_state(DAY_KEY)

    def _broker_day(self, now: datetime) -> str:
        """The broker's trading day, not ours (D20): its statements and swaps roll at its midnight."""
        offset = getattr(self.broker, "server_offset", None)
        minutes = offset.minutes if offset is not None and offset.minutes is not None else 0
        return utc_to_server(now, minutes).strftime("%Y-%m-%d")

    def _refresh_equity_marks(self, equity: float) -> None:
        """Peak and day-start survive restarts, or the drawdown limit would reset with the process."""
        if equity <= 0:
            return
        if equity > self._peak_equity:
            self._peak_equity = equity
            self.journal.set_state(PEAK_EQUITY, equity)

        today = self._broker_day(self._now())
        if self._day_key != today:
            self._day_key = today
            self._day_start_equity = equity
            self.journal.set_state(DAY_KEY, today)
            self.journal.set_state(DAY_START_EQUITY, equity)
            self.journal.event("INFO", SOURCE, f"new trading day {today} (broker time)", {"day_start_equity": equity})

    def _day_start_utc(self, now: datetime) -> datetime:
        """Midnight on the broker's clock (D20), expressed in UTC so the journal can be queried."""
        offset = getattr(self.broker, "server_offset", None)
        minutes = offset.minutes if offset is not None and offset.minutes is not None else 0
        midnight = utc_to_server(now, minutes).replace(hour=0, minute=0, second=0, microsecond=0)
        return server_to_utc(midnight, minutes)

    def _strategy_day_pnl(self, now: datetime, symbol_info) -> dict[str, float]:
        """What each strategy has actually made or lost today, for its own daily budget.

        Read at the only moment it can matter — a bar with signals on it — rather than every tick,
        because it is a query over the journal and most ticks have nothing to decide.
        """
        if symbol_info is None:
            return {}
        try:
            from tradeapp.reports import realized_pnl_by_strategy

            return realized_pnl_by_strategy(
                self.journal, self._day_start_utc(now), now, {self.config.symbol: symbol_info}
            )
        except Exception as e:  # noqa: BLE001 - a missing budget number must not stop trading
            self.journal.event("WARN", SOURCE, "could not read today's PnL by strategy", {"error": str(e)})
            return {}

    # --- helpers ------------------------------------------------------------------

    def _reconcile_due(self, now: datetime) -> bool:
        if self.last_reconcile_at is None:
            return True
        return (now - self.last_reconcile_at).total_seconds() >= self.config.reconcile_every_s

    def _reconcile(self, force: bool = False) -> None:
        self.reconciler.run()
        self.last_reconcile_at = self._now()

    def _context(self, now: datetime) -> Context | None:
        try:
            bars = self.broker.bars(self.config.symbol, self.config.timeframe, self.config.history_bars)
            return Context(
                symbol=self.config.symbol,
                timeframe=self.config.timeframe,
                bars=bars,
                now_utc=now,
                tick=self.broker.tick(self.config.symbol),
                symbol_info=self.broker.symbol_info(self.config.symbol),
                ai=self.analyst.view if self.analyst is not None else AIContext.neutral(),
            )
        except Exception as e:  # noqa: BLE001
            self.journal.event("WARN", SOURCE, "could not build a context this tick", {"error": str(e)})
            return None

    def status(self) -> dict:
        return {
            "state": self.kill.state.value,
            "frozen": self.reconciler.frozen,
            "freeze_reason": self.reconciler.freeze_reason,
            "equity": self.account.equity if self.account else None,
            "day_start_equity": self.day_start_equity,
            "peak_equity": self.peak_equity,
            "open_positions": len(self.positions),
            "last_bar_utc": self.last_bar_utc.isoformat() if self.last_bar_utc else None,
            "consecutive_rejects": self.executor.consecutive_rejects,
            # The watchdog's two numbers. Silence is what the kill switch counts, and a climbing
            # reconnect count is a terminal that needs looking at even while nothing has tripped.
            "last_broker_contact_utc": (
                self.executor.last_broker_contact_utc.isoformat() if self.executor.last_broker_contact_utc else None
            ),
            "reconnects": getattr(self.broker, "reconnects", 0),
            "strategies": self.runtime.status(),
        }
