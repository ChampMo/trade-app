"""Lifecycle gates: the discipline lives in code so it cannot be talked past at 2am."""

import pytest

from tradeapp.backtest import ZERO_COSTS, monte_carlo, run_backtest
from tradeapp.journal import Journal
from tradeapp.lifecycle import (
    Evidence,
    GateLimits,
    Lifecycle,
    LifecycleState,
    PromotionRefused,
    evaluate,
    evidence_from_backtest,
    next_state,
)


def full_backtest_evidence() -> Evidence:
    return Evidence(
        backtest_trades=50, backtest_costs_modelled=True, backtest_stopped_early=False, backtest_profit_factor=1.4
    )


def full_forward_evidence() -> Evidence:
    return Evidence(walk_forward_efficiency=0.7, monte_carlo_p95_drawdown_pct=9.0)


def full_live_small_evidence() -> Evidence:
    return Evidence(
        forward_days=95,
        forward_trades=210,
        forward_params_unchanged=True,
        forward_max_drawdown_pct=8.0,
        kill_drills_passed=3,
        news_events_survived=4,
    )


# --- the ladder ------------------------------------------------------------------------


def test_the_order_of_states():
    assert next_state(LifecycleState.RESEARCH) is LifecycleState.BACKTESTED
    assert next_state(LifecycleState.BACKTESTED) is LifecycleState.FORWARD
    assert next_state(LifecycleState.FORWARD) is LifecycleState.LIVE_SMALL
    assert next_state(LifecycleState.LIVE_SMALL) is LifecycleState.LIVE
    assert next_state(LifecycleState.LIVE) is None
    assert next_state(LifecycleState.RETIRED) is None


def test_a_new_strategy_starts_in_research(journal: Journal):
    assert Lifecycle(journal).state("trend_h4") is LifecycleState.RESEARCH


# --- research -> backtested --------------------------------------------------------------


def test_a_backtest_with_too_few_trades_is_refused():
    result = evaluate(
        LifecycleState.RESEARCH,
        Evidence(backtest_trades=5, backtest_costs_modelled=True, backtest_profit_factor=2.0),
    )
    assert not result.passed
    assert [g.name for g in result.failures] == ["backtest trades"]


def test_a_backtest_without_costs_is_refused():
    """A result with no spread modelled is not evidence of anything (D18)."""
    result = evaluate(
        LifecycleState.RESEARCH,
        Evidence(backtest_trades=100, backtest_costs_modelled=False, backtest_profit_factor=2.0),
    )
    assert "costs modelled" in [g.name for g in result.failures]


def test_a_backtest_the_kill_switch_stopped_is_refused():
    result = evaluate(
        LifecycleState.RESEARCH,
        Evidence(
            backtest_trades=100,
            backtest_costs_modelled=True,
            backtest_stopped_early=True,
            backtest_profit_factor=2.0,
        ),
    )
    assert "backtest completed" in [g.name for g in result.failures]


def test_a_complete_backtest_passes():
    assert evaluate(LifecycleState.RESEARCH, full_backtest_evidence()).passed


def test_a_losing_backtest_is_refused():
    """It need not be good yet, but it must at least have made money on the data it was fitted to."""
    ev = full_backtest_evidence()
    result = evaluate(LifecycleState.RESEARCH, Evidence(**{**ev.__dict__, "backtest_profit_factor": 0.77}))
    assert "profit factor" in [g.name for g in result.failures]


# --- backtested -> forward ----------------------------------------------------------------


def test_curve_fitting_is_caught_by_walk_forward():
    """An edge that only exists in sample is exactly what this gate is for."""
    result = evaluate(
        LifecycleState.BACKTESTED, Evidence(walk_forward_efficiency=0.1, monte_carlo_p95_drawdown_pct=5.0)
    )
    assert "walk-forward efficiency" in [g.name for g in result.failures]


def test_a_missing_walk_forward_counts_as_not_proven():
    result = evaluate(LifecycleState.BACKTESTED, Evidence(monte_carlo_p95_drawdown_pct=5.0))
    assert not result.passed
    assert "nothing" in str(result.failures[0])


def test_monte_carlo_drawdown_beyond_half_the_account_limit_is_refused():
    result = evaluate(
        LifecycleState.BACKTESTED, Evidence(walk_forward_efficiency=0.9, monte_carlo_p95_drawdown_pct=20.0)
    )
    assert "Monte Carlo p95 drawdown" in [g.name for g in result.failures]


# --- forward -> live_small: the gates that guard real money -------------------------------


def test_every_forward_gate_is_checked():
    result = evaluate(LifecycleState.FORWARD, Evidence())
    assert len(result.gates) == 6
    assert not result.passed


def test_a_short_forward_test_is_refused():
    ev = full_live_small_evidence()
    result = evaluate(LifecycleState.FORWARD, Evidence(**{**ev.__dict__, "forward_days": 45}))
    assert "forward test length" in [g.name for g in result.failures]


def test_touching_the_parameters_during_the_forward_test_is_refused():
    """D3's most-skipped gate: 90 boring days only mean something if nothing was tuned."""
    ev = full_live_small_evidence()
    result = evaluate(LifecycleState.FORWARD, Evidence(**{**ev.__dict__, "forward_params_unchanged": False}))
    assert "parameters untouched" in [g.name for g in result.failures]


def test_too_few_forward_trades_is_refused():
    ev = full_live_small_evidence()
    result = evaluate(LifecycleState.FORWARD, Evidence(**{**ev.__dict__, "forward_trades": 120}))
    assert "forward trades" in [g.name for g in result.failures]


def test_missing_kill_drills_are_refused():
    ev = full_live_small_evidence()
    result = evaluate(LifecycleState.FORWARD, Evidence(**{**ev.__dict__, "kill_drills_passed": 1}))
    assert "kill switch drills" in [g.name for g in result.failures]


def test_a_complete_forward_test_passes():
    assert evaluate(LifecycleState.FORWARD, full_live_small_evidence()).passed


# --- live_small -> live -------------------------------------------------------------------


def test_live_needs_a_month_that_matches_demo():
    assert not evaluate(LifecycleState.LIVE_SMALL, Evidence(live_small_days=10)).passed
    assert evaluate(LifecycleState.LIVE_SMALL, Evidence(live_small_days=35, live_vs_demo_divergence_pct=12.0)).passed


def test_live_that_diverges_from_demo_goes_no_further():
    result = evaluate(LifecycleState.LIVE_SMALL, Evidence(live_small_days=40, live_vs_demo_divergence_pct=80.0))
    assert "live matches demo" in [g.name for g in result.failures]


# --- promotion through the store ----------------------------------------------------------


def test_promotion_is_refused_and_says_why(journal: Journal):
    lc = Lifecycle(journal)
    with pytest.raises(PromotionRefused) as excinfo:
        lc.promote("trend_h4", Evidence(backtest_trades=2))
    assert "backtest trades" in str(excinfo.value)
    assert lc.state("trend_h4") is LifecycleState.RESEARCH  # unmoved


def test_a_refused_promotion_is_journaled(journal: Journal):
    with pytest.raises(PromotionRefused):
        Lifecycle(journal).promote("trend_h4", Evidence())
    warned = [e for e in journal.events_where(severity="WARN", source="lifecycle")]
    assert warned and "promotion refused" in warned[0].message


def test_a_strategy_climbs_one_step_at_a_time(journal: Journal):
    lc = Lifecycle(journal)
    assert lc.promote("trend_h4", full_backtest_evidence()) is LifecycleState.BACKTESTED
    assert lc.promote("trend_h4", full_forward_evidence()) is LifecycleState.FORWARD
    assert lc.promote("trend_h4", full_live_small_evidence()) is LifecycleState.LIVE_SMALL
    assert lc.state("trend_h4") is LifecycleState.LIVE_SMALL


def test_evidence_for_the_wrong_step_does_not_skip_ahead(journal: Journal):
    """Forward-test evidence cannot be used to jump out of research."""
    lc = Lifecycle(journal)
    with pytest.raises(PromotionRefused):
        lc.promote("trend_h4", full_live_small_evidence())
    assert lc.state("trend_h4") is LifecycleState.RESEARCH


def test_there_is_nowhere_to_go_from_live(journal: Journal):
    lc = Lifecycle(journal)
    lc._write("trend_h4", LifecycleState.LIVE, "test setup")
    with pytest.raises(PromotionRefused):
        lc.promote("trend_h4", full_live_small_evidence())


def test_state_survives_a_new_lifecycle_object(journal: Journal):
    Lifecycle(journal).promote("trend_h4", full_backtest_evidence())
    assert Lifecycle(journal).state("trend_h4") is LifecycleState.BACKTESTED


def test_history_records_every_move(journal: Journal):
    lc = Lifecycle(journal)
    lc.promote("trend_h4", full_backtest_evidence())
    lc.promote("trend_h4", full_forward_evidence())
    history = lc.record("trend_h4")["history"]
    assert [h["to"] for h in history] == ["backtested", "forward"]
    assert all(h["reason"] for h in history)


# --- stopping is never gated ---------------------------------------------------------------


def test_retiring_is_always_allowed(journal: Journal):
    lc = Lifecycle(journal)
    lc.promote("trend_h4", full_backtest_evidence())
    assert lc.retire("trend_h4", "edge disappeared after the 2026 regime change") is LifecycleState.RETIRED


def test_retiring_needs_a_reason(journal: Journal):
    with pytest.raises(ValueError, match="needs a reason"):
        Lifecycle(journal).retire("trend_h4", "  ")


def test_changing_parameters_sends_it_back_to_the_start(journal: Journal):
    """D3: a parameter change restarts the clock. Otherwise 90 untouched days means nothing."""
    lc = Lifecycle(journal)
    lc.promote("trend_h4", full_backtest_evidence())
    lc.promote("trend_h4", full_forward_evidence())
    assert lc.demote_to_research("trend_h4", "changed sl_atr_mult to 2.0") is LifecycleState.RESEARCH


def test_all_states_lists_what_the_ui_needs(journal: Journal):
    lc = Lifecycle(journal)
    lc.promote("trend_h4", full_backtest_evidence())
    lc.retire("old_idea", "superseded")
    assert lc.all_states() == {"trend_h4": "backtested", "old_idea": "retired"}


# --- the bridge from a real backtest --------------------------------------------------------


def test_evidence_is_read_from_a_backtest_not_typed_by_hand(journal: Journal):
    from tests.test_backtest import OpenOnce, rising_bars

    result = run_backtest(rising_bars(200), [OpenOnce()], costs=ZERO_COSTS, warmup=60)
    mc = monte_carlo(result.trades, result.start_balance, runs=100)
    ev = evidence_from_backtest(result, monte_carlo=mc)

    assert ev.backtest_trades == result.stats.trades
    assert ev.backtest_stopped_early is False
    assert ev.monte_carlo_p95_drawdown_pct == mc.drawdown_p95


def test_limits_can_be_tightened_but_the_defaults_are_d3():
    lim = GateLimits()
    assert (lim.min_forward_days, lim.min_forward_trades) == (90, 200)
    assert lim.required_kill_drills == 3
    strict = GateLimits(min_backtest_trades=500)
    assert not evaluate(LifecycleState.RESEARCH, full_backtest_evidence(), strict).passed
