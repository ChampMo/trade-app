"""Keeping the stored bars current from inside the loop.

A backtest run tomorrow should cover up to yesterday, and the drift report should compare a live
week against a backtest that has seen the same week. Both quietly stop being true when nobody
remembers to run `data sync`.

Everything here is about the sync failing safely: stale history is a research problem, a stopped
loop is a trading one, and the second must never be caused by the first.
"""

from datetime import UTC, datetime, timedelta

from tests.test_core import NO_SLEEP, NOW, Quiet, bars_ending
from tradeapp.broker.fake import FakeBehavior, FakeBroker
from tradeapp.contracts import TF
from tradeapp.core import Core, CoreConfig
from tradeapp.data import BarStore
from tradeapp.journal import Journal
from tradeapp.runtime import StrategyRuntime


class Clock:
    def __init__(self, start=NOW):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


def build(journal: Journal, store, clock, *, every_s: float = 3600.0) -> Core:
    broker = FakeBroker(behavior=FakeBehavior())
    bars = bars_ending(NOW - timedelta(hours=4))
    broker.seed_bars(bars)
    broker.bid = bars[-1].close
    runtime = StrategyRuntime(journal)
    runtime.register(Quiet())
    return Core(
        broker,
        journal,
        runtime=runtime,
        config=CoreConfig(sync_history_every_s=every_s, sync_history_bars=100),
        history=store,
        now=clock,
        sleep=NO_SLEEP,
    )


def test_the_first_healthy_tick_fills_an_empty_store(journal: Journal, tmp_path):
    store = BarStore(tmp_path / "history.db")
    core = build(journal, store, Clock())
    core.start()

    report = core.tick()

    assert store.count("EURUSD", TF.H4) > 0
    assert any("history:" in n for n in report.notes)


def test_it_does_not_run_again_until_its_timer_is_up(journal: Journal, tmp_path):
    store = BarStore(tmp_path / "history.db")
    clock = Clock()
    core = build(journal, store, clock, every_s=3600)
    core.start()
    core.tick()

    clock.advance(minutes=30)
    assert not any("history:" in n for n in core.tick().notes)

    clock.advance(minutes=31)
    assert any("history:" in n for n in core.tick().notes)


def test_a_broker_that_cannot_answer_does_not_stop_the_loop(journal: Journal, tmp_path):
    """The loop keeps trading on yesterday's history; that is the safe direction to fail in."""

    class Grumpy(BarStore):
        def sync_from_broker(self, *a, **kw):
            raise RuntimeError("terminal busy")

    core = build(journal, Grumpy(tmp_path / "history.db"), Clock())
    core.start()

    report = core.tick()

    assert core.kill.state.value == "RUNNING"
    assert any("history sync failed" in n for n in report.notes)
    assert any("could not sync history" in e.message for e in journal.tail_events(20))


def test_syncing_is_off_unless_it_is_asked_for(journal: Journal, tmp_path):
    """A simulated broker's bars are invented and must never reach the research history."""
    store = BarStore(tmp_path / "history.db")
    core = build(journal, store, Clock(), every_s=0.0)
    core.start()

    core.tick()

    assert store.count("EURUSD", TF.H4) == 0


def test_no_store_means_no_sync(journal: Journal):
    broker = FakeBroker(behavior=FakeBehavior())
    broker.seed_bars(bars_ending(NOW - timedelta(hours=4)))
    runtime = StrategyRuntime(journal)
    runtime.register(Quiet())
    core = Core(
        broker,
        journal,
        runtime=runtime,
        config=CoreConfig(sync_history_every_s=3600),
        now=lambda: NOW,
        sleep=NO_SLEEP,
    )
    core.start()

    assert core._history_due(datetime.now(UTC)) is False
    assert not any("history:" in n for n in core.tick().notes)
