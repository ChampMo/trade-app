"""Adding a market from the UI, without turning the ladder into a suggestion (D29).

D28 took the market list from what the strategies declare, because a strategy knows which symbols
it was written and tested for. This is the owner's override on top of that, and the tests here are
mostly about the two conditions that keep it honest: a market with no stored bars cannot be added
at all, and adding one drops the strategy back to `research` — which by D26 keeps it off real
money until it climbs back.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tradeapp.contracts import TF, Bar
from tradeapp.core import Market
from tradeapp.data import BarStore
from tradeapp.journal import Journal
from tradeapp.lifecycle import Lifecycle, LifecycleState
from tradeapp.markets import MarketBook, MarketRefused, declared_markets

DECLARED = {"ema_cross": [Market("EURUSD", TF.H4)], "meanrev_m15": [Market("EURUSD", TF.M15)]}


@pytest.fixture
def store(tmp_path) -> BarStore:
    s = BarStore(tmp_path / "history.db")
    s.upsert(
        "GBPUSD",
        TF.H4,
        [
            Bar(
                time_utc=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=4 * i),
                open=1.3,
                high=1.31,
                low=1.29,
                close=1.3,
            )
            for i in range(20)
        ],
    )
    return s


def book(journal: Journal, store=None) -> MarketBook:
    return MarketBook(journal, store=store)


# --- what the loop sees -------------------------------------------------------------------------


def test_by_default_it_is_exactly_what_the_strategies_declare(journal: Journal):
    assert book(journal).active(DECLARED) == (Market("EURUSD", TF.H4), Market("EURUSD", TF.M15))


def test_turning_a_market_off_takes_it_out_of_the_loop(journal: Journal):
    b = book(journal)
    b.disable("meanrev_m15", "EURUSD", "M15", "too noisy this week")

    assert b.active(DECLARED) == (Market("EURUSD", TF.H4),)
    assert any("will not trade EURUSD M15" in e.message for e in journal.tail_events(10))


def test_turning_it_back_on_restores_it(journal: Journal):
    b = book(journal)
    b.disable("meanrev_m15", "EURUSD", "M15")
    b.enable("meanrev_m15", "EURUSD", "M15")

    assert Market("EURUSD", TF.M15) in b.active(DECLARED)


def test_switching_everything_off_is_allowed_and_means_nothing_trades(journal: Journal):
    """A deliberate 'stop trading but keep watching' is a legitimate thing to want."""
    b = book(journal)
    b.disable("ema_cross", "EURUSD", "H4")
    b.disable("meanrev_m15", "EURUSD", "M15")

    assert b.active(DECLARED) == ()


def test_the_book_survives_a_restart(journal: Journal):
    book(journal).disable("ema_cross", "EURUSD", "H4")
    assert Market("EURUSD", TF.H4) not in book(journal).active(DECLARED)


# --- adding a market the strategy never declared -------------------------------------------------


def test_a_market_with_no_stored_bars_cannot_be_added(journal: Journal, store: BarStore):
    """It could not be backtested, so it could never climb the ladder. Refuse rather than gamble."""
    with pytest.raises(MarketRefused, match="no stored bars"):
        book(journal, store).add("ema_cross", "USDJPY", "H4", known_strategies={"ema_cross"})


def test_a_market_with_history_can_be_added(journal: Journal, store: BarStore):
    row = book(journal, store).add("ema_cross", "GBPUSD", "H4", known_strategies={"ema_cross"})

    assert row.declared is False and row.enabled is True and row.bars == 20
    assert Market("GBPUSD", TF.H4) in book(journal, store).active(DECLARED)


def test_adding_a_market_drops_the_strategy_back_to_research(journal: Journal, store: BarStore):
    """Passing a gate on EURUSD H4 proves nothing about GBPUSD."""
    lifecycle = Lifecycle(journal)
    lifecycle._write("ema_cross", LifecycleState.FORWARD, "for the test")

    book(journal, store).add("ema_cross", "GBPUSD", "H4", known_strategies={"ema_cross"}, lifecycle=lifecycle)

    assert lifecycle.state("ema_cross") is LifecycleState.RESEARCH
    assert any("never written for" in e.message for e in journal.tail_events(10))


def test_an_unknown_strategy_is_refused(journal: Journal, store: BarStore):
    with pytest.raises(MarketRefused, match="no strategy called"):
        book(journal, store).add("nonexistent", "GBPUSD", "H4", known_strategies={"ema_cross"})


def test_an_unknown_timeframe_is_refused(journal: Journal, store: BarStore):
    with pytest.raises(MarketRefused, match="not a timeframe"):
        book(journal, store).add("ema_cross", "GBPUSD", "H7", known_strategies={"ema_cross"})


def test_an_added_market_can_be_removed_again(journal: Journal, store: BarStore):
    b = book(journal, store)
    b.add("ema_cross", "GBPUSD", "H4", known_strategies={"ema_cross"})
    b.remove("ema_cross", "GBPUSD", "H4")

    assert Market("GBPUSD", TF.H4) not in b.active(DECLARED)


# --- what the UI is told -------------------------------------------------------------------------


def test_rows_say_which_markets_were_declared_and_which_were_attached(journal: Journal, store: BarStore):
    b = book(journal, store)
    b.add("ema_cross", "GBPUSD", "H4", known_strategies={"ema_cross"})

    rows = {(r.symbol, r.timeframe): r for r in b.rows(DECLARED)}
    assert rows[("EURUSD", "H4")].declared is True
    assert rows[("GBPUSD", "H4")].declared is False
    assert rows[("GBPUSD", "H4")].bars == 20
    assert rows[("GBPUSD", "H4")].first_utc.startswith("2026-01-01")


def test_the_routing_map_says_which_strategy_may_trade_where(journal: Journal, store: BarStore):
    b = book(journal, store)
    b.add("ema_cross", "GBPUSD", "H4", known_strategies={"ema_cross"})

    routing = b.strategy_markets(DECLARED)
    assert routing["ema_cross"] == {Market("EURUSD", TF.H4), Market("GBPUSD", TF.H4)}
    assert routing["meanrev_m15"] == {Market("EURUSD", TF.M15)}


def test_declared_markets_reads_what_the_strategies_say(journal: Journal):
    from tradeapp.runtime import StrategyRuntime
    from tradeapp.strategies.ema_cross import EmaCross

    runtime = StrategyRuntime(journal)
    runtime.register(EmaCross())

    assert declared_markets(runtime) == {"ema_cross": [Market("EURUSD", TF.H4)]}
