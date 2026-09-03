"""The three limits deliberately left out of Risk Engine v0 (P1-04b, D23).

Each one exists for a failure the v0 gates cannot see:

- **free margin** - currency netting and open-risk caps say nothing about whether the broker will
  accept the order at all. At 0.25% risk on 1:500 this never binds, which is exactly why it has to
  be written down now rather than discovered on a live account with different leverage.
- **correlation** - netting stops three EUR longs counting as three risks. It does nothing about
  two pairs with no shared currency that move together anyway.
- **per-strategy budgets** - the account can be perfectly healthy while one strategy is having a
  disastrous day, and the account-wide limits will happily let it keep going.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.test_risk_engine import STRAT, account, ctx, long_intent, position
from tests.test_risk_sizing import EURUSD
from tradeapp.contracts import Side, SymbolInfo
from tradeapp.journal import Journal
from tradeapp.reports import realized_pnl_by_strategy
from tradeapp.risk import RejectReason, RiskEngine, RiskLimits, Verdict
from tradeapp.risk.correlation import correlated_units, correlation
from tradeapp.risk.sizing import estimate_margin

USDJPY = SymbolInfo(
    symbol="USDJPY",
    digits=3,
    point=0.001,
    volume_min=0.01,
    volume_step=0.01,
    stops_level_points=0,
    spread_points=10,
    trade_allowed=True,
    tick_size=0.001,
    tick_value=0.68,
    volume_max=100.0,
    contract_size=100_000.0,
    currency_base="USD",
    currency_profit="JPY",
)
EURGBP = SymbolInfo(**{**EURUSD.__dict__, "symbol": "EURGBP", "currency_base": "EUR", "currency_profit": "GBP"})


def with_account(**fields):
    base = account()
    return type(base)(**{**base.__dict__, **fields})


def two_symbols() -> dict[str, SymbolInfo]:
    return {"EURUSD": EURUSD, "GBPUSD": SymbolInfo(**{**EURUSD.__dict__, "symbol": "GBPUSD"})}


# --- free margin ---------------------------------------------------------------------------


def test_margin_for_a_pair_quoted_in_the_account_currency():
    """0.12 lots of EURUSD at 1.159 is 13,908 USD of notional; on 1:500 that is 27.82 USD."""
    assert estimate_margin(0.12, EURUSD, 1.15910, "USD", 500) == pytest.approx(27.82, abs=0.01)


def test_margin_for_a_pair_whose_base_is_the_account_currency():
    """USDJPY notional is already in USD, so the price does not enter the calculation."""
    assert estimate_margin(0.10, USDJPY, 147.5, "USD", 500) == pytest.approx(20.0)


def test_margin_is_unknown_when_neither_leg_is_the_account_currency():
    """EURGBP on a USD account needs a cross rate the engine does not have. Say so, do not guess."""
    assert estimate_margin(0.10, EURGBP, 0.855, "USD", 500) is None


def test_an_order_that_would_eat_the_free_margin_is_refused():
    thin = with_account(margin_free=50.0, leverage=1)
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(account=thin))
    assert d.verdict is Verdict.REJECTED and d.reason is RejectReason.INSUFFICIENT_MARGIN
    assert "free" in d.detail


def test_plenty_of_free_margin_changes_nothing():
    assert RiskEngine().evaluate(long_intent(), STRAT, ctx(account=with_account(margin_free=9_000.0))).approved


def test_an_unknown_free_margin_skips_the_check_rather_than_blocking():
    """None means the broker did not say. Refusing every trade over a missing number is worse."""
    assert RiskEngine().evaluate(long_intent(), STRAT, ctx()).approved


def test_the_brokers_own_number_beats_our_arithmetic():
    engine = RiskEngine(margin_required=lambda symbol, side, lots, price: 9_999.0)
    d = engine.evaluate(long_intent(), STRAT, ctx(account=with_account(margin_free=100.0)))
    assert d.reason is RejectReason.INSUFFICIENT_MARGIN and "9999" in d.detail.replace(",", "")


def test_a_broken_margin_quote_falls_back_instead_of_raising():
    def explode(symbol, side, lots, price):
        raise RuntimeError("terminal busy")

    engine = RiskEngine(margin_required=explode)
    assert engine.evaluate(long_intent(), STRAT, ctx(account=with_account(margin_free=9_000.0))).approved


# --- correlation ---------------------------------------------------------------------------


def test_correlation_is_symmetric_and_unknown_pairs_are_zero():
    assert correlation("EURUSD", "GBPUSD") == correlation("GBPUSD", "EURUSD") == 0.85
    assert correlation("EURUSD", "EURUSD.raw") == 1.0  # broker suffixes are the same symbol
    assert correlation("EURUSD", "USDSGD") == 0.0  # not in the table: assume nothing


def test_two_correlated_longs_are_nearly_two_copies_of_one_bet():
    units, contributors = correlated_units([position(symbol="GBPUSD")], "EURUSD", Side.LONG)
    assert units == pytest.approx(1.85)
    assert contributors == [("GBPUSD", 0.85)]


def test_a_negative_correlation_in_the_opposite_direction_is_the_same_bet():
    """Short USDCHF is long EURUSD wearing a different hat."""
    units, _ = correlated_units([position(symbol="USDCHF", side=Side.SHORT)], "EURUSD", Side.LONG)
    assert units == pytest.approx(1.90)


def test_a_genuinely_opposing_position_subtracts():
    units, _ = correlated_units([position(symbol="GBPUSD", side=Side.SHORT)], "EURUSD", Side.LONG)
    assert units == pytest.approx(0.15)


def test_weak_correlations_are_ignored():
    """Counting noise would refuse trades for no reason."""
    units, contributors = correlated_units([position(symbol="USDJPY")], "EURUSD", Side.LONG, floor=0.5)
    assert units == 1.0 and contributors == []


def test_the_third_copy_of_the_same_bet_is_refused():
    positions = [position(symbol="GBPUSD", magic=1), position(symbol="AUDUSD", magic=2)]
    limits = RiskLimits(max_positions=5, max_currency_exposure=9)
    d = RiskEngine(limits).evaluate(long_intent(), STRAT, ctx(positions=positions))
    assert d.verdict is Verdict.REJECTED and d.reason is RejectReason.CORRELATED_EXPOSURE
    assert "copies of the same bet" in d.detail


def test_an_uncorrelated_second_position_is_fine():
    limits = RiskLimits(max_positions=5, max_currency_exposure=9)
    d = RiskEngine(limits).evaluate(long_intent(), STRAT, ctx(positions=[position(symbol="USDSGD")]))
    assert d.approved


# --- per-strategy budgets --------------------------------------------------------------------


def test_a_strategy_that_spent_its_daily_budget_stops_while_the_account_carries_on():
    """The account is down 1.5% of its 3%. It is the strategy that is finished, not the day."""
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(strategy_day_pnl={STRAT: -150.0}))
    assert d.verdict is Verdict.REJECTED and d.reason is RejectReason.STRATEGY_DAILY_LOSS
    assert "the account is still fine" in d.detail


def test_another_strategy_is_unaffected_by_a_neighbours_bad_day():
    d = RiskEngine().evaluate(long_intent(), "meanrev_m15", ctx(strategy_day_pnl={STRAT: -900.0}))
    assert d.approved


def test_a_strategy_still_inside_its_budget_trades():
    assert RiskEngine().evaluate(long_intent(), STRAT, ctx(strategy_day_pnl={STRAT: -40.0})).approved


def test_one_strategy_cannot_spend_the_whole_accounts_open_risk():
    """0.5% of $10,000 is $50 for this strategy; the account ceiling of $100 is not the binding one."""
    engine = RiskEngine(RiskLimits(max_positions=5))
    magic = engine.magic_for(STRAT)
    held = position(symbol="EURUSD", magic=magic, volume=0.2, entry=1.16000, sl=1.15800)  # $40 at risk
    d = engine.evaluate(long_intent(symbol="GBPUSD"), STRAT, ctx(positions=[held], symbols=two_symbols()))
    assert d.verdict is Verdict.REJECTED and d.reason is RejectReason.STRATEGY_OPEN_RISK


def test_another_strategys_positions_do_not_count_against_this_one():
    engine = RiskEngine(RiskLimits(max_positions=5))
    other = position(symbol="EURUSD", magic=777_777, volume=0.2, entry=1.16000, sl=1.15800)
    d = engine.evaluate(long_intent(symbol="GBPUSD"), STRAT, ctx(positions=[other], symbols=two_symbols()))
    assert d.approved


# --- what the daily budget is measured against ------------------------------------------------


def test_realised_pnl_is_grouped_by_strategy(journal: Journal):
    from tests.test_reports import a_trade

    a_trade(journal, "win", comment="trend_h4", entry=1.1000, exit_=1.1020)
    a_trade(journal, "loss", comment="trend_h4", entry=1.1000, exit_=1.0990)
    a_trade(journal, "other", comment="meanrev_m15", entry=1.1000, exit_=1.0995)

    start = datetime(2026, 9, 3, tzinfo=UTC)
    pnl = realized_pnl_by_strategy(journal, start, start + timedelta(days=1), {"EURUSD": EURUSD})

    assert pnl["trend_h4"] == pytest.approx(10.0)  # +200 and -100 ticks at $1 a tick on 0.1 lots
    assert pnl["meanrev_m15"] == pytest.approx(-5.0)


def test_a_symbol_with_no_info_is_skipped_rather_than_guessed(journal: Journal):
    from tests.test_reports import a_trade

    a_trade(journal, "win", comment="trend_h4", entry=1.1000, exit_=1.1020)
    start = datetime(2026, 9, 3, tzinfo=UTC)
    assert realized_pnl_by_strategy(journal, start, start + timedelta(days=1), {}) == {}


def test_an_open_trade_does_not_count_yet(journal: Journal):
    from tests.test_reports import a_trade

    a_trade(journal, "still_open", comment="trend_h4", exit_=None)
    start = datetime(2026, 9, 3, tzinfo=UTC)
    assert realized_pnl_by_strategy(journal, start, start + timedelta(days=1), {"EURUSD": EURUSD}) == {}
