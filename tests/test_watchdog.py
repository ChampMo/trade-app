"""The watchdog (P4-01): a terminal that drops out is repaired, not waited on.

Two failures are being separated here, and the whole point is that they are treated differently:

- **the terminal is still starting** (-10003) — retry, it will be ready in a moment;
- **anything else** (a wrong password, the wrong server) — never retry, because attempt two will
  fail exactly the same way and repeated logins are how broker accounts get locked.

The third case is the one the loop cares about most: MT5 can hold a live process that has itself
lost the broker. Our own `connected` flag says everything is fine, and only `terminal_info()` knows
better — so that is what gets asked.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.fakes import FakeMT5Module
from tests.test_core import NO_SLEEP, NOW, AlwaysLong, bars_ending
from tradeapp.broker.fake import FakeBehavior, FakeBroker
from tradeapp.broker.mt5_bridge import MT5Broker
from tradeapp.contracts import BrokerError
from tradeapp.core import Core, CoreConfig
from tradeapp.journal import Journal
from tradeapp.risk import RiskLimits
from tradeapp.runtime import StrategyRuntime

BUSY = (-10003, "IPC initialize failed, terminal not started")
WRONG_SERVER = (-6, "Terminal: Authorization failed")


def build_broker(mod, journal=None, **kw) -> tuple[MT5Broker, list[float]]:
    slept: list[float] = []
    broker = MT5Broker(
        mt5_module=mod,
        journal=journal,
        sleep=slept.append,
        connect_backoff_s=0.0,
        **kw,
    )
    return broker, slept


# --- connect: retry the one failure worth retrying ---------------------------------------


def test_a_busy_terminal_is_retried_until_it_answers(journal: Journal):
    mod = FakeMT5Module(trade_mode=0, init_sequence=[False, False, True], last_error=BUSY)
    broker, slept = build_broker(mod, journal)

    account = broker.connect()

    assert account.login == 123
    assert mod.initialize_calls == 3
    assert len(slept) == 2  # it waited between attempts rather than hammering
    assert any("terminal not ready" in e.message for e in journal.tail_events(20))


def test_a_wrong_server_is_never_retried(journal: Journal):
    """Attempt two fails identically, and repeated logins are how accounts get locked."""
    mod = FakeMT5Module(trade_mode=0, init_ok=False, last_error=WRONG_SERVER)
    broker, slept = build_broker(mod, journal)

    with pytest.raises(BrokerError) as e:
        broker.connect()

    assert mod.initialize_calls == 1
    assert slept == []
    assert "Login to Trade Account" in str(e.value)  # the hint, not a bare number


def test_a_terminal_that_never_starts_gives_up_and_says_why():
    mod = FakeMT5Module(trade_mode=0, init_ok=False, last_error=BUSY)
    broker, _ = build_broker(mod, connect_attempts=4)

    with pytest.raises(BrokerError) as e:
        broker.connect()

    assert mod.initialize_calls == 4
    assert "starting or busy" in str(e.value)


# --- ensure_connected: the per-tick check ---------------------------------------------------


def test_a_healthy_terminal_is_left_alone(journal: Journal):
    mod = FakeMT5Module(trade_mode=0)
    broker, _ = build_broker(mod, journal)
    broker.connect()

    assert broker.ensure_connected() is True
    assert mod.initialize_calls == 1  # nothing was reconnected
    assert broker.reconnects == 0


def test_a_terminal_that_lost_the_broker_is_reconnected(journal: Journal):
    """Our own flag still says connected. Only terminal_info() knows the truth."""
    mod = FakeMT5Module(trade_mode=0)
    broker, _ = build_broker(mod, journal)
    broker.connect()
    mod.terminal_connected = False

    def initialize(**kwargs):
        mod.terminal_connected = True  # the reconnect is what fixes it
        return FakeMT5Module.initialize(mod, **kwargs)

    mod.initialize = initialize

    assert broker.ensure_connected() is True
    assert broker.reconnects == 1
    assert mod.initialize_calls == 2
    assert mod.shutdown_calls == 1  # the dead handle was released first
    messages = [e.message for e in journal.tail_events(20)]
    assert any("disconnected from the broker" in m for m in messages)
    assert any("reconnected" in m for m in messages)


def test_a_broker_that_was_never_connected_is_connected(journal: Journal):
    mod = FakeMT5Module(trade_mode=0)
    broker, _ = build_broker(mod, journal)

    assert broker.ensure_connected() is True
    assert broker.connected is True


def test_a_failed_reconnect_reports_false_rather_than_raising(journal: Journal):
    mod = FakeMT5Module(trade_mode=0, init_ok=False, last_error=WRONG_SERVER)
    broker, _ = build_broker(mod, journal)

    assert broker.ensure_connected() is False
    assert broker.connected is False
    assert any(e.severity == "CRIT" for e in journal.tail_events(20))


def test_reconnecting_re_measures_the_broker_clock(journal: Journal):
    """A reconnect can land on the other side of a DST change; a stale offset misreads every bar."""
    mod = FakeMT5Module(trade_mode=0, server_offset_min=180)
    broker, _ = build_broker(mod, journal)
    broker.connect()
    assert broker.server_offset.minutes == 180

    mod._server_offset_min = 120
    mod.terminal_connected = False
    broker.ensure_connected()

    assert broker.server_offset.minutes == 120


# --- the loop uses it ----------------------------------------------------------------------


class Flaky(FakeBroker):
    """A FakeBroker that can be told the terminal is unreachable."""

    def __init__(self, *, reachable: bool = True, **kw):
        super().__init__(**kw)
        self.reachable = reachable
        self.checks = 0

    def ensure_connected(self) -> bool:
        self.checks += 1
        return self.reachable


def build_core(journal: Journal, broker) -> Core:
    bars = bars_ending(NOW - timedelta(hours=4))
    broker.seed_bars(bars)
    broker.bid = bars[-1].close
    runtime = StrategyRuntime(journal)
    runtime.register(AlwaysLong())
    return Core(
        broker,
        journal,
        runtime=runtime,
        config=CoreConfig(),
        limits=RiskLimits(),
        now=lambda: NOW,
        sleep=NO_SLEEP,
    )


def test_every_tick_asks_whether_the_terminal_is_still_there(journal: Journal):
    broker = Flaky(behavior=FakeBehavior())
    core = build_core(journal, broker)
    core.start()

    core.tick()
    core.tick()

    assert broker.checks >= 2


def test_an_unreachable_terminal_opens_nothing(journal: Journal):
    """A tick with no view of the account must not act on a stale position list."""
    broker = Flaky(behavior=FakeBehavior(), reachable=False)
    core = build_core(journal, broker)
    core.start()

    report = core.tick()

    assert report.sent == 0
    assert any("not reachable" in n for n in report.notes)
    assert any("no fresh view" in n for n in report.notes)


def test_a_broker_without_a_watchdog_still_ticks(journal: Journal):
    """The paper and backtest brokers have no terminal to lose; the check is optional."""
    broker = FakeBroker(behavior=FakeBehavior())
    core = build_core(journal, broker)
    core.start()

    report = core.tick()

    assert not any("not reachable" in n for n in report.notes)
    assert isinstance(report.at_utc, datetime) and report.at_utc.tzinfo is UTC


# --- the drill (owner-facing evidence) ------------------------------------------------------


def test_the_watchdog_drill_passes_every_scenario(journal: Journal):
    """`python -m tradeapp drill` is the answer to "does it still recover"; it must stay green."""
    from tradeapp.drill import run_watchdog_drills

    results = run_watchdog_drills(journal)

    assert len(results) == 4
    assert all(r.passed for r in results), [r.name for r in results if not r.passed]
    assert any("watchdog drill: 4/4" in e.message for e in journal.tail_events(50))


def test_the_status_carries_the_watchdog_numbers(journal: Journal):
    """The UI needs to see a climbing reconnect count before anything trips."""
    broker = Flaky(behavior=FakeBehavior())
    broker.reconnects = 4
    core = build_core(journal, broker)
    core.start()
    core.tick()

    status = core.status()
    assert status["reconnects"] == 4
    assert status["last_broker_contact_utc"] is not None
