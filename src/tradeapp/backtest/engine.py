"""The backtest, which is the live system fed from a file.

`run_backtest` builds the real `Core`, the real `RiskEngine`, the real `Executor` and the real
`StrategyRuntime`, and points them at a broker made of history. Nothing about the decision path is
re-implemented, so a number that comes out of here is a claim about the system that will trade the
account, not about a research script that resembles it.

The kill switch is live during a backtest, and that is on purpose. If a run stops halfway because
drawdown passed 30%, that is not the harness failing — it is the answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from tradeapp.backtest import stats as stats_mod
from tradeapp.backtest.broker import BacktestBroker, ClosedTrade
from tradeapp.backtest.costs import CostModel
from tradeapp.contracts import TF, Bar
from tradeapp.core import Core, CoreConfig
from tradeapp.journal import Journal
from tradeapp.risk import RiskLimits
from tradeapp.runtime import StrategyRuntime


@dataclass
class BacktestResult:
    symbol: str
    timeframe: TF
    start_utc: datetime | None
    end_utc: datetime | None
    bars: int
    start_balance: float
    end_balance: float
    trades: list[ClosedTrade]
    equity_curve: list[tuple[datetime, float]]
    stats: stats_mod.Stats
    killed: str | None = None
    rejections: dict[str, int] = field(default_factory=dict)
    strategy_status: list[dict] = field(default_factory=list)

    @property
    def stopped_early(self) -> bool:
        return self.killed is not None

    def summary(self) -> str:
        head = f"{self.symbol} {self.timeframe.value}  {self.bars} bars  {self.stats.summary()}"
        return f"{head}\nSTOPPED EARLY: {self.killed}" if self.killed else head

    def per_variant(self) -> dict[int, stats_mod.Stats]:
        return {
            magic: stats_mod.compute(group, self.equity_curve, self.start_balance)
            for magic, group in stats_mod.by_magic(self.trades).items()
        }


def on_symbol(strategy, symbol: str):
    """Point a strategy at the symbol being replayed, for the same reason as `on_timeframe`.

    A strategy declares the pairs it was written for; a backtest asking "what would this idea do
    on GBPUSD" is a legitimate question, and the symbol is stored with the run so the answer can
    never be mistaken for one on the declared pair. The loop never does this (D28): there, a
    market a strategy did not declare needs the owner to attach it, with the gate attached (D29).
    """
    strategy.symbols = [symbol]
    return strategy


def on_timeframe(strategy, timeframe):
    """Point a strategy at the timeframe being replayed.

    A strategy declares the timeframe it was written for, and the runtime skips it on any other —
    which is right in the loop and wrong in a backtest, where "what would this idea do on M1" is
    exactly the question being asked. The replayed timeframe is stored with the run, so a result
    can never be mistaken for one on the strategy's own timeframe.
    """
    strategy.timeframe = timeframe
    return strategy


def run_backtest(
    bars: list[Bar],
    strategies,
    *,
    symbol: str = "EURUSD",
    timeframe: TF = TF.H4,
    costs: CostModel | None = None,
    limits: RiskLimits | None = None,
    start_balance: float = 10_000.0,
    warmup: int = 60,
    history_bars: int = 300,
    stop_on_kill: bool = True,
    reconcile_every_s: float = 86_400.0,
    journal: Journal | None = None,
) -> BacktestResult:
    """Replay `bars` through the live decision path.

    `warmup` is how many bars pass before strategies are asked anything, so indicators are not
    consulted while they are still returning None.
    """
    if len(bars) <= warmup + 1:
        raise ValueError(f"need more than {warmup + 1} bars to backtest; got {len(bars)}")

    broker = BacktestBroker(
        bars_all=bars,
        symbol=symbol,
        timeframe=timeframe,
        costs=costs or CostModel(),
        balance=start_balance,
        index=warmup,
    )
    journal = journal or Journal(":memory:")
    runtime = StrategyRuntime(journal)
    for entry in strategies:
        if isinstance(entry, tuple):
            runtime.register(entry[0], variant=entry[1])
        else:
            runtime.register(entry)

    core = Core(
        broker,
        journal,
        runtime=runtime,
        config=CoreConfig(
            symbol=symbol,
            timeframe=timeframe,
            history_bars=history_bars,
            # Simulated time, so this is "once a trading day" rather than once a minute. Reconcile
            # in a backtest only keeps the ledger tidy — the Risk Engine reads positions from the
            # broker — and running it on every bar was a third of the runtime.
            reconcile_every_s=reconcile_every_s,
            tick_interval_s=0.0,
        ),
        limits=limits or RiskLimits(),
        now=broker.now,
        sleep=lambda _s: None,
    )
    core.start()

    killed: str | None = None
    rejections: dict[str, int] = {}
    while True:
        report = core.tick()
        for note in report.notes:
            if ":" in note:
                reason = note.split(":", 1)[1].strip()
                if reason in _REJECTION_REASONS:
                    rejections[reason] = rejections.get(reason, 0) + 1
        if report.killed:
            killed = report.notes[0] if report.notes else "kill switch tripped"
            if stop_on_kill:
                break
        if not broker.advance():
            break

    broker.close_all_at_end()
    core.shutdown()

    return BacktestResult(
        symbol=symbol,
        timeframe=timeframe,
        start_utc=bars[warmup].time_utc,
        end_utc=bars[-1].time_utc,
        bars=len(bars) - warmup,
        start_balance=start_balance,
        end_balance=round(broker.balance, 2),
        trades=broker.trades,
        equity_curve=broker.equity_curve,
        stats=stats_mod.compute(broker.trades, broker.equity_curve, start_balance),
        killed=killed,
        rejections=rejections,
        strategy_status=runtime.status(),
    )


_REJECTION_REASONS = {
    "engine_not_running",
    "flat_not_supported",
    "max_drawdown",
    "daily_loss_limit",
    "outside_trading_hours",
    "news_block",
    "ai_block",
    "symbol_not_tradeable",
    "unknown_symbol",
    "stop_wrong_side",
    "stop_too_close",
    "duplicate_position",
    "max_positions",
    "currency_exposure",
    "size_below_minimum",
    "max_open_risk",
    "sizing_unavailable",
}
