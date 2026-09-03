"""The lifecycle ladder as a runtime rule (D26).

`lifecycle.py` could promote, refuse and report from the day it was written, and none of it
reached the loop: `_build_core` registered every strategy it could find, whatever stage it was at.
The gates described a ladder that nothing was made to climb.
"""

import pytest

from tradeapp.journal import Journal
from tradeapp.lifecycle import Lifecycle, LifecycleState, below_forward, may_trade
from tradeapp.runtime import StrategyRuntime


@pytest.mark.parametrize("state", list(LifecycleState))
def test_only_the_last_two_stages_may_touch_real_money(state):
    allowed = may_trade(state, real_money=True)
    assert allowed == (state in (LifecycleState.LIVE_SMALL, LifecycleState.LIVE))


def test_retired_trades_nowhere_at_all():
    """That is what retiring means; demo is not a retirement home."""
    assert may_trade(LifecycleState.RETIRED, real_money=False) is False
    assert may_trade(LifecycleState.RETIRED, real_money=True) is False


@pytest.mark.parametrize("state", [s for s in LifecycleState if s is not LifecycleState.RETIRED])
def test_demo_stays_open_so_a_strategy_can_earn_its_evidence(state):
    """A rule that refused research on demo would leave no way to ever reach forward."""
    assert may_trade(state, real_money=False) is True


def test_below_forward_names_the_stages_that_have_proved_nothing_yet():
    assert below_forward(LifecycleState.RESEARCH)
    assert below_forward(LifecycleState.BACKTESTED)
    assert not below_forward(LifecycleState.FORWARD)
    assert not below_forward(LifecycleState.LIVE)


# --- what the loop actually registers ---------------------------------------------------------


class Settings:
    def __init__(self, live_enabled: bool):
        self.live_enabled = live_enabled


def register(journal: Journal, wanted, *, live: bool, fake: bool = False):
    from tradeapp.__main__ import _register_allowed

    runtime = StrategyRuntime(journal)
    allowed = _register_allowed(runtime, wanted, Settings(live), journal, fake=fake)
    return allowed, runtime


def test_a_research_strategy_never_reaches_a_live_account(journal: Journal):
    allowed, runtime = register(journal, ["ema_cross"], live=True)

    assert allowed == []
    assert runtime.slots == []
    events = journal.tail_events(10)
    assert any(e.severity == "CRIT" and "refusing to run ema_cross" in e.message for e in events)


def test_the_same_strategy_runs_on_demo_and_says_what_stage_it_is_at(journal: Journal):
    allowed, runtime = register(journal, ["ema_cross"], live=False)

    assert allowed == ["ema_cross"]
    assert len(runtime.slots) == 1
    assert any(e.severity == "WARN" and "below the forward gate" in e.message for e in journal.tail_events(10))


def test_a_promoted_strategy_reaches_real_money_without_a_warning(journal: Journal):
    Lifecycle(journal)._write("ema_cross", LifecycleState.LIVE_SMALL, "promoted in a test")

    allowed, _ = register(journal, ["ema_cross"], live=True)

    assert allowed == ["ema_cross"]
    assert not any("refusing" in e.message for e in journal.tail_events(10))


def test_a_retired_strategy_is_dropped_even_on_a_simulated_broker(journal: Journal):
    Lifecycle(journal).retire("ema_cross", "edge gone")

    allowed, runtime = register(journal, ["ema_cross", "meanrev_m15"], live=False, fake=True)

    assert allowed == ["meanrev_m15"]
    assert [s.key for s in runtime.slots] == ["meanrev_m15"]


def test_an_empty_result_is_loud_rather_than_a_quiet_idle_loop(journal: Journal):
    Lifecycle(journal).retire("ema_cross", "edge gone")

    allowed, _ = register(journal, ["ema_cross"], live=False)

    assert allowed == []
    assert any("no strategy is allowed to trade here" in e.message for e in journal.tail_events(10))
