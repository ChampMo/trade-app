"""Risk Engine: one test per rejection reason, plus the sizing path that produces an order.

Rule 02 says this is the only door to the market, so every way it can say no is worth pinning down.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.test_risk_sizing import EURUSD
from tradeapp.contracts import AccountInfo, AccountMode, Intent, Position, Side, SymbolInfo, Tick
from tradeapp.journal import Journal
from tradeapp.risk import AIContext, EngineState, RejectReason, RiskContext, RiskEngine, RiskLimits, Verdict

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)  # inside 07:00-20:00 UTC
STRAT = "trend_h4"


def account(equity: float = 10_000.0) -> AccountInfo:
    return AccountInfo(
        login=1,
        server="XM-Demo",
        mode=AccountMode.DEMO,
        balance=equity,
        equity=equity,
        currency="USD",
        leverage=500,
        algo_trading=True,
    )


def tick(bid: float = 1.15900, ask: float = 1.15910) -> Tick:
    return Tick(symbol="EURUSD", bid=bid, ask=ask, time_utc=NOW, time_server=None, server_utc_offset_min=180)


def ctx(**over) -> RiskContext:
    equity = over.pop("equity", 10_000.0)
    base = {
        "account": account(equity),
        "symbols": {"EURUSD": EURUSD},
        "tick": tick(),
        "positions": [],
        "now_utc": NOW,
        "day_start_equity": equity,
        "peak_equity": equity,
        "state": EngineState.RUNNING,
        "ai": AIContext.neutral(),
    }
    base.update(over)
    return RiskContext(**base)


def long_intent(stop: float = 1.15710, confidence: float = 1.0, symbol: str = "EURUSD") -> Intent:
    return Intent(
        symbol=symbol,
        side=Side.LONG,
        confidence=confidence,
        stop_price=stop,
        take_price=1.16310,
        reason="EMA cross with pullback",
    )


def position(symbol="EURUSD", side=Side.LONG, magic=999, volume=0.1, entry=1.16000, sl=1.15800) -> Position:
    return Position(
        ticket=magic, symbol=symbol, side=side, volume=volume, price_open=entry, sl=sl, tp=0.0, profit=0.0, magic=magic
    )


# --- the happy path ------------------------------------------------------------------


def test_approves_and_sizes_from_the_risk_budget():
    """0.25% of $10,000 is $25; a 200-point stop costs $200 a lot, so 0.12 lots."""
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx())
    assert d.verdict is Verdict.APPROVED and d.reason is None
    assert d.size_lots == 0.12
    assert d.risk_amount == 24.0  # under the $25 budget, because lots round down
    assert d.order is not None
    assert d.order.stop_price == 1.15710 and d.order.volume == 0.12
    assert d.order.symbol == "EURUSD" and d.order.side is Side.LONG


def test_order_carries_the_strategy_magic_number():
    engine = RiskEngine(magic_base=100_000)
    first = engine.evaluate(long_intent(), "trend_h4", ctx()).order
    second = engine.evaluate(long_intent(), "meanrev_m15", ctx()).order
    assert first.magic != second.magic
    assert engine.magic_for("trend_h4") == first.magic  # stable across calls


def test_confidence_scales_the_position():
    half = RiskEngine().evaluate(long_intent(confidence=0.5), STRAT, ctx())
    assert half.size_lots == 0.06


def test_short_side_prices_from_the_bid():
    intent = Intent(
        symbol="EURUSD", side=Side.SHORT, confidence=1.0, stop_price=1.16100, take_price=1.15500, reason="fade"
    )
    d = RiskEngine().evaluate(intent, STRAT, ctx())
    assert d.approved and d.size_lots == 0.12  # 1.15900 bid to 1.16100 stop is also 200 points


# --- account-wide gates --------------------------------------------------------------


@pytest.mark.parametrize("state", [EngineState.PAUSED, EngineState.KILLED, EngineState.STARTING])
def test_reject_when_engine_is_not_running(state):
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(state=state))
    assert d.reason is RejectReason.ENGINE_NOT_RUNNING
    assert state.value in d.detail


def test_reject_flat_intent():
    flat = Intent(symbol="EURUSD", side=Side.FLAT, confidence=0.0, stop_price=0.0, take_price=None, reason="exit")
    d = RiskEngine().evaluate(flat, STRAT, ctx())
    assert d.reason is RejectReason.FLAT_NOT_SUPPORTED


def test_reject_at_max_drawdown():
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(equity=7_000.0, peak_equity=10_000.0, day_start_equity=7_000.0))
    assert d.reason is RejectReason.MAX_DRAWDOWN
    assert d.facts["drawdown_pct"] == pytest.approx(30.0)


def test_reject_at_daily_loss_limit():
    d = RiskEngine().evaluate(
        long_intent(), STRAT, ctx(equity=9_700.0, day_start_equity=10_000.0, peak_equity=10_000.0)
    )
    assert d.reason is RejectReason.DAILY_LOSS_LIMIT


def test_daily_loss_just_under_the_limit_still_trades():
    d = RiskEngine().evaluate(
        long_intent(), STRAT, ctx(equity=9_800.0, day_start_equity=10_000.0, peak_equity=10_000.0)
    )
    assert d.approved


def test_reject_outside_trading_hours():
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(now_utc=NOW.replace(hour=22)))
    assert d.reason is RejectReason.OUTSIDE_TRADING_HOURS


def test_reject_inside_a_news_window():
    class Calendar:
        def blocked(self, symbol, at):
            return "USD Retail Sales in 12 min"

    d = RiskEngine(news=Calendar()).evaluate(long_intent(), STRAT, ctx())
    assert d.reason is RejectReason.NEWS_BLOCK
    assert "Retail Sales" in d.detail


def test_reject_when_the_ai_layer_vetoes():
    ai = AIContext.valid_for(60, NOW, block=True, source="analyst")
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(ai=ai))
    assert d.reason is RejectReason.AI_BLOCK


# --- symbol and stop -----------------------------------------------------------------


def test_reject_unknown_symbol():
    d = RiskEngine().evaluate(long_intent(symbol="GBPUSD", stop=1.34), STRAT, ctx())
    assert d.reason is RejectReason.UNKNOWN_SYMBOL


def test_reject_when_broker_disabled_the_symbol():
    closed = SymbolInfo(**{**EURUSD.__dict__, "trade_allowed": False})
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(symbols={"EURUSD": closed}))
    assert d.reason is RejectReason.SYMBOL_NOT_TRADEABLE


def test_reject_stop_on_the_wrong_side():
    d = RiskEngine().evaluate(long_intent(stop=1.16100), STRAT, ctx())
    assert d.reason is RejectReason.STOP_WRONG_SIDE


def test_reject_stop_inside_the_brokers_minimum_distance():
    d = RiskEngine().evaluate(long_intent(stop=1.15905), STRAT, ctx())
    assert d.reason is RejectReason.STOP_TOO_CLOSE
    assert d.facts["stop_distance_points"] == pytest.approx(5.0, abs=0.5)


def test_stop_distance_accounts_for_the_spread():
    """A stop clearing the raw stops level but not the spread would be rejected by the broker."""
    wide = SymbolInfo(**{**EURUSD.__dict__, "spread_points": 100, "stops_level_points": 50})
    d = RiskEngine().evaluate(long_intent(stop=1.15810), STRAT, ctx(symbols={"EURUSD": wide}))
    assert d.reason is RejectReason.STOP_TOO_CLOSE  # 100 points away, needs 155


# --- portfolio gates -----------------------------------------------------------------


def test_reject_a_second_position_for_the_same_strategy_and_symbol():
    engine = RiskEngine()
    mine = engine.magic_for(STRAT)
    d = engine.evaluate(long_intent(), STRAT, ctx(positions=[position(magic=mine)]))
    assert d.reason is RejectReason.DUPLICATE_POSITION


def test_reject_beyond_max_positions():
    others = [position(magic=m, symbol=s) for m, s in ((11, "EURUSD"), (12, "GBPUSD"), (13, "AUDUSD"))]
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(positions=others))
    assert d.reason is RejectReason.MAX_POSITIONS


def test_reject_when_currency_exposure_would_stack():
    """Three longs on EUR is one big EUR bet, which is what blows retail accounts up."""
    held = [position(magic=11, symbol="EURUSD"), position(magic=12, symbol="EURGBP")]
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(positions=held))
    assert d.reason is RejectReason.CURRENCY_EXPOSURE
    assert d.facts["exposure"]["EUR"] == 3


def test_opposite_side_does_not_count_against_exposure():
    held = [position(magic=11, symbol="EURUSD"), position(magic=12, symbol="EURUSD", side=Side.SHORT, sl=1.16200)]
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(positions=held))
    assert d.approved  # net EUR exposure is +1, not +3


# --- sizing failures -----------------------------------------------------------------


def test_reject_when_the_budget_cannot_buy_the_minimum_lot():
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(equity=100.0))
    assert d.reason is RejectReason.SIZE_BELOW_MINIMUM
    assert "would exceed the limit" in d.detail


def test_reject_when_the_broker_gives_no_tick_value():
    broken = SymbolInfo(**{**EURUSD.__dict__, "tick_value": 0.0})
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(symbols={"EURUSD": broken}))
    assert d.reason is RejectReason.SIZING_UNAVAILABLE


def test_reject_when_open_stops_already_use_the_risk_ceiling():
    """Max open risk is 1% of equity: $100 here, and one 0.45 lot position already risks $90."""
    held = [position(magic=11, symbol="GBPUSD", volume=0.45, entry=1.35000, sl=1.34800)]
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(positions=held, symbols={"EURUSD": EURUSD, "GBPUSD": EURUSD}))
    assert d.reason is RejectReason.MAX_OPEN_RISK
    assert d.facts["open_risk"] == pytest.approx(90.0)


# --- the AI layer may shrink, never grow ---------------------------------------------


def test_ai_size_multiplier_shrinks_the_position():
    ai = AIContext.valid_for(60, NOW, size_mult=0.5, source="analyst")
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(ai=ai))
    assert d.approved and d.size_lots == 0.06


def test_ai_size_multiplier_is_capped():
    ai = AIContext.valid_for(60, NOW, size_mult=99.0, source="rogue")
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(ai=ai))
    assert d.facts["size_mult"] == 1.5  # RiskLimits.max_size_mult, not 99


def test_opposing_bias_halves_the_position_and_aligned_bias_does_nothing():
    against = AIContext.valid_for(60, NOW, bias=-1.0, source="analyst")
    with_it = AIContext.valid_for(60, NOW, bias=1.0, source="analyst")
    assert RiskEngine().evaluate(long_intent(), STRAT, ctx(ai=against)).size_lots == 0.06
    assert RiskEngine().evaluate(long_intent(), STRAT, ctx(ai=with_it)).size_lots == 0.12


def test_stale_ai_context_is_ignored_entirely():
    """A dead AI layer must not keep vetoing trades with a two hour old opinion."""
    stale = AIContext(block=True, size_mult=0.1, bias=-1.0, valid_until=NOW - timedelta(minutes=1), source="analyst")
    d = RiskEngine().evaluate(long_intent(), STRAT, ctx(ai=stale))
    assert d.approved and d.size_lots == 0.12


# --- journalling ---------------------------------------------------------------------


def test_every_decision_is_journaled_with_its_reason(journal: Journal):
    engine = RiskEngine(journal=journal)
    approved = engine.evaluate(long_intent(), STRAT, ctx(), variant="B")
    rejected = engine.evaluate(long_intent(stop=1.16100), STRAT, ctx(), variant="B")

    assert approved.decision_id and rejected.decision_id
    from sqlalchemy import select

    from tradeapp.journal.models import Decision

    with journal.session() as s:
        rows = list(s.execute(select(Decision).order_by(Decision.id)).scalars())

    assert [r.verdict for r in rows] == ["APPROVED", "REJECTED"]
    assert rows[0].size_lots == 0.12 and rows[0].variant == "B"
    assert rows[0].strategy_id == STRAT and rows[0].reason == "EMA cross with pullback"
    assert "stop_wrong_side" in rows[1].verdict_reason
    assert rows[1].size_lots is None
    # the context that produced the decision is stored, so a post-mortem can replay it
    assert rows[0].context["equity"] == 10_000.0
    assert rows[0].context["server_utc_offset_min"] == 180


def test_journal_records_the_ai_context_that_was_used(journal: Journal):
    ai = AIContext.valid_for(60, NOW, regime="risk-off", bias=-0.3, size_mult=0.8, source="analyst")
    RiskEngine(journal=journal).evaluate(long_intent(), STRAT, ctx(ai=ai))
    from sqlalchemy import select

    from tradeapp.journal.models import Decision

    with journal.session() as s:
        row = s.execute(select(Decision)).scalars().one()
    assert row.ai_regime == "risk-off" and row.ai_bias == -0.3 and row.ai_size_mult == 0.8
    assert row.ai_block is False


# --- limits are configurable, and the defaults are D3 --------------------------------


def test_defaults_match_the_locked_decisions():
    lim = RiskLimits()
    assert (lim.risk_pct, lim.daily_loss_limit_pct, lim.max_drawdown_pct) == (0.25, 3.0, 30.0)
    assert (lim.max_open_risk_pct, lim.max_positions, lim.max_currency_exposure) == (1.0, 3, 2)


def test_trading_window_can_cross_midnight():
    from datetime import time as t

    lim = RiskLimits(trading_start_utc=t(22, 0), trading_end_utc=t(6, 0))
    assert lim.within_trading_hours(NOW.replace(hour=23))
    assert lim.within_trading_hours(NOW.replace(hour=3))
    assert not lim.within_trading_hours(NOW.replace(hour=12))
