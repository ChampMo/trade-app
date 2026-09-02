"""The core loop: safety checks gate the trading path, not the other way round.

The tests that matter are the ones proving a tick that finds trouble never reaches the point of
opening a position.
"""

from datetime import UTC, datetime, timedelta

from tradeapp.broker.fake import FakeBehavior, FakeBroker
from tradeapp.contracts import TF, Bar, Intent, Side
from tradeapp.core import DAY_START_EQUITY, PEAK_EQUITY, Core, CoreConfig
from tradeapp.journal import Journal
from tradeapp.risk import EngineState, RiskLimits
from tradeapp.runtime import StrategyRuntime

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
NO_SLEEP = lambda s: None  # noqa: E731


class AlwaysLong:
    id, symbols, timeframe = "always_long", ["EURUSD"], TF.H4

    def on_bar(self, ctx):
        return Intent(
            symbol=ctx.symbol,
            side=Side.LONG,
            confidence=1.0,
            stop_price=round(ctx.close() - 0.0020, 5),
            take_price=round(ctx.close() + 0.0040, 5),
            reason="test signal",
        )


class Quiet:
    id, symbols, timeframe = "quiet", ["EURUSD"], TF.H4

    def on_bar(self, ctx):
        return None


def bars_ending(at: datetime, n: int = 60) -> list[Bar]:
    return [
        Bar(
            time_utc=at - timedelta(hours=4 * (n - 1 - i)),
            open=1.1000 + i * 0.0001,
            high=1.1005 + i * 0.0001,
            low=1.0995 + i * 0.0001,
            close=1.1000 + i * 0.0001,
        )
        for i in range(n)
    ]


def build(journal: Journal, strategies=None, *, behavior=None, clock=None, **cfg) -> Core:
    broker = FakeBroker(behavior=behavior or FakeBehavior())
    bars = bars_ending(NOW - timedelta(hours=4))
    broker.seed_bars(bars)
    broker.bid = bars[-1].close  # keep the tick and the bars telling the same story
    runtime = StrategyRuntime(journal)
    for s in strategies if strategies is not None else (AlwaysLong(),):
        runtime.register(s)
    return Core(
        broker,
        journal,
        runtime=runtime,
        config=CoreConfig(**cfg) if cfg else CoreConfig(),
        limits=RiskLimits(),
        now=clock or (lambda: NOW),
        sleep=NO_SLEEP,
    )


# --- startup ---------------------------------------------------------------------------


def test_start_connects_reconciles_and_records_the_marks(journal: Journal):
    core = build(journal)
    acct = core.start()

    assert acct.mode.value == "demo"
    assert core.kill.state is EngineState.RUNNING
    assert core.peak_equity == acct.equity
    assert core.day_start_equity == acct.equity
    assert core.reconciler.last is not None  # reconciled before anything could trade
    assert any("core starting" in e.message for e in journal.events_where(source="core"))


def test_equity_marks_survive_a_restart(journal: Journal):
    """Without this, closing the app would silently erase the drawdown history (D21)."""
    core = build(journal)
    core.start()
    core.broker.balance = 12_000.0
    core.tick()
    assert core.peak_equity == 12_000.0

    core.broker.balance = 9_000.0
    core.tick()
    assert core.peak_equity == 12_000.0  # the high-water mark does not fall

    restarted = build(journal)
    restarted.start()
    assert restarted.peak_equity == 12_000.0
    assert journal.get_state(PEAK_EQUITY) == 12_000.0


def test_a_new_broker_day_resets_the_daily_mark(journal: Journal):
    clock = [NOW]
    core = build(journal, clock=lambda: clock[0])
    core.start()
    first_day = journal.get_state(DAY_START_EQUITY)

    core.broker.balance = 10_500.0
    clock[0] = NOW + timedelta(days=1)
    core.tick()

    assert journal.get_state(DAY_START_EQUITY) == 10_500.0 != first_day
    assert any("new trading day" in e.message for e in journal.events_where(source="core"))


# --- the happy path --------------------------------------------------------------------


def test_a_signal_becomes_a_sized_position(journal: Journal):
    core = build(journal)
    core.start()
    report = core.tick()

    assert report.new_bar and report.signals == 1
    assert report.approved == 1 and report.sent == 1
    assert len(core.broker.open_tickets) == 1

    pos = core.broker.positions()[0]
    assert pos.sl > 0  # rule 03 held all the way through
    # 0.25% of 10k is $25; the stop is 212 points away at $1 a point per lot, so 0.11 lots
    assert pos.volume == 0.11
    assert pos.volume * (pos.price_open - pos.sl) / core.broker.point <= 25.0


def test_strategies_only_act_once_per_closed_bar(journal: Journal):
    core = build(journal)
    core.start()
    assert core.tick().new_bar is True
    second = core.tick()
    assert second.new_bar is False and second.signals == 0
    assert len(core.broker.open_tickets) == 1


def test_a_quiet_strategy_does_nothing(journal: Journal):
    core = build(journal, strategies=(Quiet(),))
    core.start()
    report = core.tick()
    assert report.new_bar and report.signals == 0 and report.sent == 0
    assert core.broker.open_tickets == []


def test_a_rejection_is_reported_and_nothing_is_sent(journal: Journal):
    core = build(journal)
    core.limits = RiskLimits(max_positions=0)
    core.engine.limits = core.limits
    core.start()
    report = core.tick()
    assert report.signals == 1 and report.rejected == 1 and report.sent == 0
    assert core.broker.open_tickets == []


# --- safety gates the trading path -----------------------------------------------------


def test_a_kill_stops_the_tick_before_any_strategy_runs(journal: Journal):
    core = build(journal)
    core.start()
    core.broker.balance = 9_000.0  # 10% down on the day, past the 3% limit

    report = core.tick()

    assert report.killed and report.state is EngineState.KILLED
    assert report.signals == 0 and report.sent == 0
    assert core.broker.open_tickets == []


def test_a_killed_engine_keeps_refusing_afterwards(journal: Journal):
    core = build(journal)
    core.start()
    core.broker.balance = 9_000.0
    core.tick()
    core.broker.balance = 10_000.0  # equity recovers; the lock does not lift itself

    report = core.tick()
    assert report.state is EngineState.KILLED and report.sent == 0


def test_an_orphan_escalates_to_a_kill(journal: Journal):
    """Reconcile freezes and flags a mismatch; in the loop that mismatch is a kill trigger (D12a).

    Money at risk that nothing is managing gets flattened, not merely noted.
    """
    core = build(journal, reconcile_every_s=0.0)
    core.start()
    core.broker.seed_position()  # a position the ledger never recorded

    report = core.tick()

    assert report.frozen is True
    assert report.killed is True and report.state is EngineState.KILLED
    assert report.signals == 0 and report.sent == 0
    assert core.broker.open_tickets == []  # the orphan is closed too


def test_the_freeze_lifts_once_the_account_agrees_again(journal: Journal):
    core = build(journal, reconcile_every_s=0.0)
    core.start()
    core.broker.seed_position()
    assert core.tick().killed is True

    core.kill.unlock("investigated: an order timed out and had filled")
    core.kill.resume()
    core.last_bar_utc = None

    report = core.tick()
    assert report.frozen is False and report.sent == 1


def test_a_position_without_a_stop_kills(journal: Journal):
    """Reconcile spots it, the kill switch acts on it (rule 03, D12a)."""
    core = build(journal, reconcile_every_s=0.0)
    core.start()
    core.broker.seed_position(sl=0.0)

    report = core.tick()
    assert report.killed is True
    assert core.broker.open_tickets == []  # flattened


def test_an_unreadable_broker_stops_the_tick_from_opening_anything(journal: Journal):
    """Every portfolio limit is computed from the position list, so a stale one must not be traded on."""
    core = build(journal)
    core.start()
    core.broker.behavior.raise_on_positions = True

    report = core.tick()
    assert report.sent == 0 and report.signals == 0
    assert core.broker.open_tickets == []
    assert "no fresh view of the account" in report.notes[-1]
    assert any("could not read the account" in e.message for e in journal.events_where(source="core"))


def test_paused_engine_does_not_trade(journal: Journal):
    core = build(journal)
    core.start()
    core.kill.pause("lunch")

    report = core.tick()
    assert report.state is EngineState.PAUSED and report.sent == 0
    assert core.broker.open_tickets == []


def test_the_full_kill_unlock_resume_cycle(journal: Journal):
    """P1-05b: RUNNING -> KILLED -> PAUSED -> RUNNING, driven through the loop."""
    core = build(journal, reconcile_every_s=0.0)
    core.start()
    assert core.kill.state is EngineState.RUNNING

    core.broker.balance = 9_000.0
    assert core.tick().killed is True
    assert core.kill.state is EngineState.KILLED

    core.kill.unlock("drill: reviewed the journal")
    assert core.kill.state is EngineState.PAUSED

    # The loss that caused the kill is still on the books. A switch that re-evaluated while
    # PAUSED would slam shut again here and the operator could never get back in.
    paused = core.tick()
    assert paused.killed is False and paused.sent == 0
    assert core.kill.state is EngineState.PAUSED

    core.broker.balance = 10_000.0
    core.journal.set_state("day_start_equity", 10_000.0)
    core.kill.resume()
    assert core.kill.state is EngineState.RUNNING

    core.last_bar_utc = None  # a fresh bar arrives
    assert core.tick().sent == 1


# --- reconcile timer -------------------------------------------------------------------


def test_reconcile_runs_at_startup_and_then_on_its_timer(journal: Journal):
    clock = [NOW]
    core = build(journal, clock=lambda: clock[0], reconcile_every_s=60.0)
    core.start()
    first = core.last_reconcile_at

    core.tick()
    assert core.last_reconcile_at == first  # too soon

    clock[0] = NOW + timedelta(seconds=61)
    assert core.tick().reconciled is True
    assert core.last_reconcile_at != first


# --- run() and status ------------------------------------------------------------------


def test_run_stops_after_the_requested_ticks_and_disconnects(journal: Journal):
    core = build(journal)
    assert core.run(max_ticks=3) == 3
    assert core.broker.connected is False
    assert any("core stopping" in e.message for e in journal.events_where(source="core"))


def test_run_honours_a_stop_signal(journal: Journal):
    core = build(journal)
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 2

    assert core.run(should_stop=should_stop) == 2


def test_status_is_a_readable_snapshot(journal: Journal):
    core = build(journal)
    core.start()
    core.tick()
    status = core.status()
    assert status["state"] == "RUNNING" and status["open_positions"] == 1
    assert status["peak_equity"] > 0 and status["frozen"] is False
    assert status["strategies"][0]["key"] == "always_long"
