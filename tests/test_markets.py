"""Several markets in one loop (the answer to two questions asked on 2026-09-03).

Before this, `CoreConfig` held one symbol and one timeframe. Two consequences, both quiet:

- `ema_cross` (H4) and `meanrev_m15` (M15) were both registered and only the one matching `--tf`
  ever saw a bar. The other was skipped on every tick, forever, without saying anything.
- a second currency pair was not expressible at all, while the Risk Engine had carried currency
  netting and a correlation table for it since D23.

A market is one symbol on one timeframe, and the set comes from what the strategies declare.
"""

from datetime import timedelta

import pytest

from tests.test_core import NO_SLEEP, NOW, AlwaysLong, Quiet, bars_ending
from tradeapp.broker.fake import FakeBehavior, FakeBroker
from tradeapp.contracts import TF
from tradeapp.core import Core, CoreConfig, Market
from tradeapp.journal import Journal
from tradeapp.runtime import StrategyRuntime


class OnM15(AlwaysLong):
    id, symbols, timeframe = "on_m15", ["EURUSD"], TF.M15


class OnGbp(Quiet):
    id, symbols, timeframe = "on_gbp", ["GBPUSD"], TF.H4


class TwoPairs(Quiet):
    id, symbols, timeframe = "two_pairs", ["EURUSD", "GBPUSD"], TF.H4


def markets_for(strategies, symbols=None, timeframe=None, journal=None):
    from tradeapp.__main__ import _markets_for

    runtime = StrategyRuntime(journal)
    for s in strategies:
        runtime.register(s)
    return _markets_for(runtime, symbols, timeframe)


# --- where the market list comes from ----------------------------------------------------------


def test_each_strategy_brings_its_own_timeframe(journal: Journal):
    assert markets_for([AlwaysLong(), OnM15()], journal=journal) == (
        Market("EURUSD", TF.H4),
        Market("EURUSD", TF.M15),
    )


def test_a_strategy_that_declares_two_pairs_makes_two_markets(journal: Journal):
    assert markets_for([TwoPairs()], journal=journal) == (
        Market("EURUSD", TF.H4),
        Market("GBPUSD", TF.H4),
    )


def test_the_same_market_asked_for_twice_is_one_market(journal: Journal):
    assert markets_for([AlwaysLong(), Quiet()], journal=journal) == (Market("EURUSD", TF.H4),)


def test_symbol_narrows_the_set(journal: Journal):
    assert markets_for([TwoPairs()], symbols="GBPUSD", journal=journal) == (Market("GBPUSD", TF.H4),)


def test_timeframe_narrows_the_set(journal: Journal):
    assert markets_for([AlwaysLong(), OnM15()], timeframe=TF.M15, journal=journal) == (Market("EURUSD", TF.M15),)


def test_a_flag_can_never_widen_what_a_strategy_declared(journal: Journal):
    """A strategy knows which symbols it was written and tested for; a flag does not."""
    assert markets_for([AlwaysLong()], symbols="GBPUSD", journal=journal) == ()
    assert markets_for([AlwaysLong()], symbols="EURUSD,GBPUSD,USDJPY", journal=journal) == (Market("EURUSD", TF.H4),)


# --- the loop runs them side by side ------------------------------------------------------------


def build(journal: Journal, strategies, markets) -> Core:
    broker = FakeBroker(behavior=FakeBehavior())
    for market in markets:
        step = timedelta(minutes=market.timeframe.minutes)
        bars = [
            b._replace(time_utc=NOW - step * (60 - i)) if hasattr(b, "_replace") else b
            for i, b in enumerate(bars_ending(NOW - step))
        ]
        broker.seed_bars(bars, market.symbol, market.timeframe)
    broker.bid = 1.1059
    runtime = StrategyRuntime(journal)
    for s in strategies:
        runtime.register(s)
    return Core(
        broker,
        journal,
        runtime=runtime,
        config=CoreConfig(symbol=markets[0].symbol, timeframe=markets[0].timeframe, markets=tuple(markets)),
        now=lambda: NOW,
        sleep=NO_SLEEP,
    )


def test_both_timeframes_get_their_turn_in_one_tick(journal: Journal):
    """The H4 bar used to consume the only slot; M15 never saw one."""
    markets = [Market("EURUSD", TF.H4), Market("EURUSD", TF.M15)]
    core = build(journal, [AlwaysLong(), OnM15()], markets)
    core.start()

    report = core.tick()

    assert report.new_bar
    assert set(core._last_bars) == set(markets)
    assert report.signals == 2  # one from each strategy, on its own timeframe


def test_a_market_with_no_bars_does_not_stop_the_others(journal: Journal):
    markets = [Market("EURUSD", TF.H4), Market("GBPUSD", TF.H4)]
    core = build(journal, [AlwaysLong()], markets)
    core.broker.seed_bars([], "GBPUSD", TF.H4)
    core.start()

    report = core.tick()

    assert any("GBPUSD H4: no bars available" in n for n in report.notes)
    assert Market("EURUSD", TF.H4) in core._last_bars


def test_the_risk_engine_is_told_about_every_symbol_it_holds(journal: Journal):
    """Open risk is computed from all positions; a missing symbol would count as no risk at all."""
    markets = [Market("EURUSD", TF.H4), Market("GBPUSD", TF.H4)]
    core = build(journal, [TwoPairs()], markets)
    core.start()

    assert set(core._symbol_infos()) == {"EURUSD", "GBPUSD"}


def test_the_status_line_names_every_market(journal: Journal):
    markets = [Market("EURUSD", TF.H4), Market("EURUSD", TF.M15)]
    core = build(journal, [AlwaysLong(), OnM15()], markets)
    core.start()
    core.tick()

    rows = core.status()["markets"]
    assert [(r["symbol"], r["timeframe"]) for r in rows] == [("EURUSD", "H4"), ("EURUSD", "M15")]
    assert all(r["last_bar_utc"] for r in rows)


def test_one_market_stays_the_default(journal: Journal):
    """A backtest replays a single market and must keep behaving exactly as it did."""
    config = CoreConfig(symbol="EURUSD", timeframe=TF.H4)
    assert config.market_list == (Market("EURUSD", TF.H4),)


@pytest.mark.parametrize("tf", [TF.M15, TF.H4])
def test_a_market_prints_as_a_person_would_say_it(tf):
    assert str(Market("EURUSD", tf)) == f"EURUSD {tf.value}"
