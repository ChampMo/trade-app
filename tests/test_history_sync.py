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
    assert any("history " in n for n in report.notes)


def test_it_does_not_run_again_until_its_timer_is_up(journal: Journal, tmp_path):
    store = BarStore(tmp_path / "history.db")
    clock = Clock()
    core = build(journal, store, clock, every_s=3600)
    core.start()
    core.tick()

    clock.advance(minutes=30)
    assert not any("history " in n for n in core.tick().notes)

    clock.advance(minutes=31)
    assert any("history " in n for n in core.tick().notes)


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
    assert not any("history " in n for n in core.tick().notes)


# --- syncs asked for from the UI ---------------------------------------------------------------
#
# The UI cannot fetch bars itself: MetaTrader5 allows one connection per process and it belongs to
# the loop. So the API leaves a request and the loop does the work, which also means a failed
# request has to be a recorded result rather than an exception on somebody's thread.


def test_a_requested_sync_is_done_on_the_next_tick(journal: Journal, tmp_path):
    store = BarStore(tmp_path / "history.db")
    core = build(journal, store, Clock(), every_s=0.0)  # the timer is off; only the request runs
    core.start()

    queued = core.request_history_sync("EURUSD", TF.M15)
    assert queued["queued"] is True

    report = core.tick()

    assert store.count("EURUSD", TF.M15) > 0
    assert core.history_results["EURUSD M15"]["state"] == "done"
    assert any("EURUSD M15" in n for n in report.notes)
    assert core.history_requests == []


def test_the_same_market_is_not_queued_twice(journal: Journal, tmp_path):
    core = build(journal, BarStore(tmp_path / "history.db"), Clock())
    core.start()

    core.request_history_sync("EURUSD", TF.M15)
    second = core.request_history_sync("EURUSD", TF.M15)

    assert second["queued"] is True and "already in the queue" in second["detail"]
    assert len(core.history_requests) == 1


def test_the_queue_has_a_ceiling(journal: Journal, tmp_path):
    """A page with a button on it must not be able to ask for unbounded work."""
    core = build(journal, BarStore(tmp_path / "history.db"), Clock())
    core.start()

    for i in range(core.MAX_HISTORY_REQUESTS):
        core.request_history_sync(f"PAIR{i}", TF.M15)
    refused = core.request_history_sync("ONEMORE", TF.M15)

    assert refused["queued"] is False and "too many" in refused["detail"]


def test_a_core_with_no_store_refuses_rather_than_inventing_bars(journal: Journal):
    """A simulated broker's bars must never reach the history the research reads (D28)."""
    broker = FakeBroker(behavior=FakeBehavior())
    broker.seed_bars(bars_ending(NOW - timedelta(hours=4)))
    runtime = StrategyRuntime(journal)
    runtime.register(Quiet())
    core = Core(broker, journal, runtime=runtime, config=CoreConfig(), now=lambda: NOW, sleep=NO_SLEEP)

    outcome = core.request_history_sync("EURUSD", TF.M15)

    assert outcome["queued"] is False and "simulated broker" in outcome["detail"]


def test_a_failed_request_is_a_result_not_a_crash(journal: Journal, tmp_path):
    class Grumpy(BarStore):
        def sync_from_broker(self, *a, **kw):
            raise RuntimeError("terminal busy")

    core = build(journal, Grumpy(tmp_path / "history.db"), Clock(), every_s=0.0)
    core.start()
    core.request_history_sync("EURUSD", TF.M5)

    report = core.tick()

    assert core.kill.state.value == "RUNNING"
    assert core.history_results["EURUSD M5"]["state"] == "failed"
    assert any("EURUSD M5" in n for n in report.notes)


def test_only_one_request_is_done_per_tick(journal: Journal, tmp_path):
    """A big pull must not stall the loop, and two of them must not stall it twice as long."""
    core = build(journal, BarStore(tmp_path / "history.db"), Clock(), every_s=0.0)
    core.start()
    core.request_history_sync("EURUSD", TF.M15)
    core.request_history_sync("EURUSD", TF.M5)

    core.tick()
    assert len(core.history_requests) == 1

    core.tick()
    assert core.history_requests == []
