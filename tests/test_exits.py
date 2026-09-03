"""Exit management: the only change allowed to a position after the fill.

The rule the whole feature rests on is one-directional: **a stop may move towards the price, never
away from it.** Tightening can only reduce what a position can lose; widening turns a bounded loss
into a larger one, on the say-so of a strategy that is currently wrong about the market. So the
direction is checked in one place that has no override, and these tests are mostly about that.
"""

from datetime import UTC, datetime

import pytest

from tests.test_risk_sizing import EURUSD
from tradeapp.contracts import Position, Side, Tick
from tradeapp.exits import atr_trail, best_of, break_even, step_trail
from tradeapp.risk.stops import StopRefusal, min_distance, risk_now, validate_move

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def long_position(entry=1.16000, sl=1.15800, ticket=1) -> Position:
    return Position(
        ticket=ticket,
        symbol="EURUSD",
        side=Side.LONG,
        volume=0.1,
        price_open=entry,
        sl=sl,
        tp=1.16400,
        profit=0.0,
        magic=100_001,
    )


def short_position(entry=1.16000, sl=1.16200, ticket=2) -> Position:
    return Position(
        ticket=ticket,
        symbol="EURUSD",
        side=Side.SHORT,
        volume=0.1,
        price_open=entry,
        sl=sl,
        tp=1.15600,
        profit=0.0,
        magic=100_001,
    )


def tick(bid=1.16300, ask=1.16310) -> Tick:
    return Tick(symbol="EURUSD", bid=bid, ask=ask, time_utc=NOW, time_server=None, server_utc_offset_min=180)


# --- the one rule -----------------------------------------------------------------------------


def test_a_long_may_tighten_its_stop_upwards():
    v = validate_move(long_position(), 1.16000, EURUSD, tick())
    assert v.ok and v.price == 1.16000


def test_a_long_may_not_lower_its_stop():
    v = validate_move(long_position(), 1.15600, EURUSD, tick())
    assert v.refused and v.reason is StopRefusal.WOULD_WIDEN_RISK


def test_a_short_may_tighten_its_stop_downwards():
    v = validate_move(short_position(), 1.16000, EURUSD, tick(bid=1.15700, ask=1.15710))
    assert v.ok and v.price == 1.16000


def test_a_short_may_not_raise_its_stop():
    v = validate_move(short_position(), 1.16500, EURUSD, tick(bid=1.15700, ask=1.15710))
    assert v.refused and v.reason is StopRefusal.WOULD_WIDEN_RISK


def test_the_same_price_is_not_an_improvement():
    v = validate_move(long_position(sl=1.15800), 1.15800, EURUSD, tick())
    assert v.refused and v.reason is StopRefusal.NOT_AN_IMPROVEMENT


def test_a_stop_cannot_be_put_on_the_winning_side_of_the_price():
    """A long's stop above the bid would close the position at once, at the market."""
    v = validate_move(long_position(), 1.16400, EURUSD, tick(bid=1.16300))
    assert v.refused and v.reason is StopRefusal.WRONG_SIDE_OF_PRICE


def test_a_stop_inside_the_brokers_minimum_distance_is_refused():
    """XM measured 20 points of spread; a stop 1 point from the bid comes back INVALID_STOPS."""
    v = validate_move(long_position(), 1.16299, EURUSD, tick(bid=1.16300))
    assert v.refused and v.reason is StopRefusal.TOO_CLOSE_TO_PRICE


def test_a_position_with_no_stop_is_reconciles_problem_not_ours():
    v = validate_move(long_position(sl=0.0), 1.16000, EURUSD, tick())
    assert v.refused and v.reason is StopRefusal.NO_STOP_TO_MOVE


def test_the_minimum_distance_includes_the_spread_and_a_buffer():
    assert min_distance(EURUSD, buffer_points=5) == pytest.approx((0 + 10 + 5) * EURUSD.point)


def test_risk_turns_negative_once_the_stop_passes_the_entry():
    assert risk_now(long_position(entry=1.16, sl=1.158)) == pytest.approx(0.002)
    assert risk_now(long_position(entry=1.16, sl=1.161)) == pytest.approx(-0.001)


# --- the helpers a strategy composes ----------------------------------------------------------


def test_break_even_waits_for_the_trade_to_be_one_r_in_front():
    pos = long_position(entry=1.16000, sl=1.15800)  # 200 points of risk
    assert break_even(pos, 1.16100, 1.15800) is None  # only half an R
    assert break_even(pos, 1.16200, 1.15800) == pytest.approx(1.16000)


def test_break_even_can_cover_the_spread_rather_than_landing_exactly_on_entry():
    pos = long_position(entry=1.16000, sl=1.15800)
    assert break_even(pos, 1.16200, 1.15800, offset_points=20, point=0.00001) == pytest.approx(1.16020)


def test_break_even_for_a_short_goes_the_other_way():
    pos = short_position(entry=1.16000, sl=1.16200)
    assert break_even(pos, 1.15800, 1.16200, offset_points=20) == pytest.approx(1.15980)


def test_break_even_needs_a_real_initial_risk():
    assert break_even(long_position(), 1.17, initial_stop=1.16000, trigger_r=1.0) is None


def test_the_atr_trail_follows_the_price_and_never_leads_it():
    assert atr_trail(long_position(), 1.16300, atr=0.00100, multiple=2.0) == pytest.approx(1.16100)
    assert atr_trail(short_position(), 1.15700, atr=0.00100, multiple=2.0) == pytest.approx(1.15900)


def test_a_missing_atr_proposes_nothing_rather_than_guessing():
    assert atr_trail(long_position(), 1.16300, atr=None) is None
    assert atr_trail(long_position(), 1.16300, atr=0.0) is None


def test_the_step_trail_is_a_fixed_distance_behind():
    assert step_trail(long_position(), 1.16300, points=150) == pytest.approx(1.16150)
    assert step_trail(short_position(), 1.15700, points=150) == pytest.approx(1.15850)


def test_the_tightest_proposal_wins():
    assert best_of(1.1600, 1.1610, None, side=Side.LONG) == 1.1610
    assert best_of(1.1600, 1.1610, None, side=Side.SHORT) == 1.1600
    assert best_of(None, None, side=Side.LONG) is None


# --- through the executor and the loop --------------------------------------------------------


def test_the_executor_moves_a_stop_and_journals_it(journal, fake_broker):
    from tradeapp.execution import Executor

    fake_broker.connect()
    pos = fake_broker.seed_position(sl=1.15800)
    fake_broker.bid = 1.16300
    executor = Executor(fake_broker, journal)

    verdict = executor.move_stop(pos, 1.16000, EURUSD, tick(), reason="break even")

    assert verdict.ok
    assert fake_broker.position(pos.ticket).sl == pytest.approx(1.16000)
    assert any("stop moved" in e.message for e in journal.tail_events(20))
    assert [o.kind for o in journal.orders_recent(5) if o.kind == "modify"]


def test_a_refused_move_never_reaches_the_broker(journal, fake_broker):
    """A trail proposes on every bar; most proposals are not improvements. That is normal traffic."""
    from tradeapp.execution import Executor

    fake_broker.connect()
    pos = fake_broker.seed_position(sl=1.15800)
    fake_broker.bid = 1.16300
    executor = Executor(fake_broker, journal)

    verdict = executor.move_stop(pos, 1.15000, EURUSD, tick())

    assert verdict.refused and verdict.reason is StopRefusal.WOULD_WIDEN_RISK
    assert fake_broker.position(pos.ticket).sl == pytest.approx(1.15800)  # untouched
    assert not any(o.kind == "modify" for o in journal.orders_recent(10))
    assert any(d.verdict == "REJECTED" for d in journal.recent_decisions(5))


def test_the_original_stop_survives_a_trail_and_a_restart(journal):
    """Break-even measures R from the stop the position opened with, not the one it has now."""
    journal.order(
        client_ref="r1",
        kind="open",
        symbol="EURUSD",
        side="LONG",
        ok=True,
        retcode=10009,
        retcode_desc="DONE",
        position_ticket=555,
        sl=1.15800,
        price_filled=1.16000,
    )
    journal.order(
        client_ref="stop-555",
        kind="modify",
        symbol="EURUSD",
        side="LONG",
        ok=True,
        retcode=10009,
        retcode_desc="DONE",
        position_ticket=555,
        sl=1.16000,
    )
    assert journal.original_stop(555) == pytest.approx(1.15800)
    assert journal.original_stop(999) is None


def test_a_strategy_that_manages_nothing_is_left_alone(journal):
    from tradeapp.runtime import StrategyRuntime
    from tradeapp.strategies.meanrev_m15 import MeanReversionM15

    runtime = StrategyRuntime(journal)
    slot = runtime.register(MeanReversionM15())
    assert runtime.manage(slot.key, None, long_position(), 1.158) is None


def test_a_manage_hook_that_raises_disables_the_strategy_rather_than_the_loop(journal):
    from tradeapp.contracts import TF
    from tradeapp.runtime import StrategyRuntime

    class Exploding:
        id, symbols, timeframe = "boom", ["EURUSD"], TF.H4

        def on_bar(self, ctx):
            return None

        def manage(self, ctx, position, initial_stop):
            raise RuntimeError("bad maths")

    runtime = StrategyRuntime(journal)
    slot = runtime.register(Exploding())

    assert runtime.manage(slot.key, None, long_position(), 1.158) is None
    assert slot.enabled is False


def test_a_manage_hook_that_returns_nonsense_is_refused(journal):
    from tradeapp.contracts import TF
    from tradeapp.runtime import StrategyRuntime

    class Confused:
        id, symbols, timeframe = "confused", ["EURUSD"], TF.H4

        def on_bar(self, ctx):
            return None

        def manage(self, ctx, position, initial_stop):
            return "1.1600"

    runtime = StrategyRuntime(journal)
    slot = runtime.register(Confused())

    assert runtime.manage(slot.key, None, long_position(), 1.158) is None
    assert slot.enabled is False


def test_ema_cross_manages_nothing_until_it_is_configured_to():
    """Turning an exit on is a parameter change, so the default must not change any result."""
    from tradeapp.strategies.ema_cross import EmaCross

    assert EmaCross().manage(None, long_position(), 1.15800) is None
    assert EmaCross(break_even_r=1.0).params["break_even_r"] == 1.0
