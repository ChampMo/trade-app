"""Strategy runtime and Context: one broken strategy must never take the others down with it."""

from datetime import UTC, datetime, timedelta

import pytest

from tradeapp.broker.fake import FakeBroker
from tradeapp.context import Context, build_context
from tradeapp.contracts import TF, Bar, Intent, Side
from tradeapp.journal import Journal
from tradeapp.risk.limits import AIContext
from tradeapp.runtime import StrategyRuntime
from tradeapp.strategies import REGISTRY, create, discover, register
from tradeapp.strategies.ema_cross import EmaCross


def make_bars(prices: list[float]) -> list[Bar]:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Bar(time_utc=t0 + timedelta(hours=i), open=p, high=p + 0.0005, low=p - 0.0005, close=p)
        for i, p in enumerate(prices)
    ]


def ctx_for(prices: list[float], symbol: str = "EURUSD", tf: TF = TF.H4) -> Context:
    bars = make_bars(prices)
    return Context(symbol=symbol, timeframe=tf, bars=bars, now_utc=bars[-1].time_utc)


class Quiet:
    id, symbols, timeframe = "quiet", ["EURUSD"], TF.H4

    def on_bar(self, ctx):
        return None


class AlwaysLong:
    id, symbols, timeframe = "always_long", ["EURUSD"], TF.H4

    def on_bar(self, ctx):
        return Intent(
            symbol=ctx.symbol,
            side=Side.LONG,
            confidence=0.5,
            stop_price=ctx.close() - 0.002,
            take_price=None,
            reason="always",
        )


class Exploding:
    id, symbols, timeframe = "exploding", ["EURUSD"], TF.H4

    def on_bar(self, ctx):
        raise ZeroDivisionError("bad maths in a strategy")


class WrongSymbol:
    id, symbols, timeframe = "wrong_symbol", ["EURUSD"], TF.H4

    def on_bar(self, ctx):
        return Intent(symbol="GBPUSD", side=Side.LONG, confidence=0.5, stop_price=1.30, take_price=None, reason="oops")


class WrongType:
    id, symbols, timeframe = "wrong_type", ["EURUSD"], TF.H4

    def on_bar(self, ctx):
        return "buy please"


# --- Context ---------------------------------------------------------------------------


def test_context_exposes_bars_and_indicators():
    ctx = ctx_for([1.10 + i * 0.001 for i in range(60)])
    assert len(ctx) == 60
    assert ctx.close() == pytest.approx(ctx.bars[-1].close)
    assert ctx.close(1) == pytest.approx(ctx.bars[-2].close)
    assert ctx.ema(20) is not None and ctx.atr(14) is not None and ctx.rsi(14) is not None


def test_context_indicator_values_are_cached():
    ctx = ctx_for([1.10 + i * 0.001 for i in range(60)])
    first = ctx.ema(20)
    assert ctx.ema(20) == first
    assert ("ema", 20) in ctx._cache


def test_context_returns_none_while_warming_up():
    ctx = ctx_for([1.10, 1.11, 1.12])
    assert ctx.ema(20) is None
    assert ctx.has_history(20) is False


def test_context_rejects_an_unknown_indicator():
    with pytest.raises(KeyError):
        ctx_for([1.1] * 30)._series("bollinger", 20)


def test_context_defaults_to_a_neutral_ai_view():
    assert ctx_for([1.1] * 30).ai.block is False


def test_context_snapshot_is_journal_ready():
    snap = ctx_for([1.1] * 30).snapshot()
    assert snap["symbol"] == "EURUSD" and snap["timeframe"] == "H4" and snap["bars_available"] == 30


def test_build_context_pulls_from_the_broker():
    b = FakeBroker()
    b.connect()
    ctx = build_context(b, "EURUSD", TF.H4, count=120, ai=AIContext.neutral())
    assert len(ctx) == 120
    assert ctx.tick is not None and ctx.symbol_info is not None
    assert ctx.point == b.point


def test_build_context_can_read_seeded_bars():
    b = FakeBroker()
    b.connect()
    b.seed_bars(make_bars([1.10, 1.11, 1.12]))
    assert len(build_context(b, "EURUSD", TF.H4).bars) == 3


# --- isolation, the reason this class exists -------------------------------------------


def test_one_exploding_strategy_does_not_stop_the_others(journal: Journal):
    rt = StrategyRuntime(journal)
    rt.register(Exploding())
    rt.register(AlwaysLong())
    rt.register(Quiet())

    signals = rt.on_bar(ctx_for([1.10 + i * 0.001 for i in range(60)]))

    assert [s.strategy_id for s in signals] == ["always_long"]
    assert rt.slot("exploding").enabled is False
    assert "ZeroDivisionError" in rt.slot("exploding").error
    assert rt.slot("always_long").enabled and rt.slot("quiet").enabled


def test_a_disabled_strategy_is_journaled_with_a_traceback(journal: Journal):
    rt = StrategyRuntime(journal)
    rt.register(Exploding())
    rt.on_bar(ctx_for([1.1] * 60))

    crit = [e for e in journal.events_where(severity="CRIT", source="strategy")]
    assert crit and "exploding" in crit[0].message
    assert "ZeroDivisionError" in crit[0].data["trace"]


def test_a_disabled_strategy_is_not_called_again(journal: Journal):
    rt = StrategyRuntime(journal)
    rt.register(Exploding())
    ctx = ctx_for([1.1] * 60)
    rt.on_bar(ctx)
    calls_after_first = rt.slot("exploding").calls
    rt.on_bar(ctx)
    assert rt.slot("exploding").calls == calls_after_first


def test_an_intent_for_the_wrong_symbol_disables_the_strategy(journal: Journal):
    rt = StrategyRuntime(journal)
    rt.register(WrongSymbol())
    assert rt.on_bar(ctx_for([1.1] * 60)) == []
    assert rt.slot("wrong_symbol").enabled is False
    assert "GBPUSD" in rt.slot("wrong_symbol").error


def test_returning_the_wrong_type_disables_the_strategy(journal: Journal):
    rt = StrategyRuntime(journal)
    rt.register(WrongType())
    rt.on_bar(ctx_for([1.1] * 60))
    assert rt.slot("wrong_type").enabled is False
    assert "expected Intent or None" in rt.slot("wrong_type").error


def test_a_disabled_strategy_can_be_brought_back_deliberately(journal: Journal):
    rt = StrategyRuntime(journal)
    rt.register(Exploding())
    rt.on_bar(ctx_for([1.1] * 60))
    assert rt.enable("exploding").enabled is True
    assert rt.slot("exploding").error is None


# --- routing ---------------------------------------------------------------------------


def test_strategies_only_see_their_own_symbol_and_timeframe():
    rt = StrategyRuntime()
    rt.register(AlwaysLong())
    assert rt.on_bar(ctx_for([1.1] * 60, symbol="GBPUSD")) == []
    assert rt.on_bar(ctx_for([1.1] * 60, tf=TF.M15)) == []
    assert len(rt.on_bar(ctx_for([1.1] * 60))) == 1


def test_variants_of_one_strategy_run_side_by_side():
    """A/B/C on the same feed is how we find out whether the AI layer earns its keep (D9)."""
    rt = StrategyRuntime()
    rt.register(AlwaysLong(), variant="A")
    rt.register(AlwaysLong(), variant="B")
    signals = rt.on_bar(ctx_for([1.1] * 60))
    assert [s.variant for s in signals] == ["A", "B"]
    assert [s.key for s in signals] == ["always_long·A", "always_long·B"]


def test_the_same_variant_cannot_be_registered_twice():
    rt = StrategyRuntime()
    rt.register(AlwaysLong(), variant="A")
    with pytest.raises(ValueError, match="already registered"):
        rt.register(AlwaysLong(), variant="A")


def test_status_reports_counters(journal: Journal):
    rt = StrategyRuntime(journal)
    rt.register(AlwaysLong())
    rt.register(Exploding())
    rt.on_bar(ctx_for([1.1] * 60))
    status = {s["key"]: s for s in rt.status()}
    assert status["always_long"]["signals"] == 1 and status["always_long"]["enabled"]
    assert status["exploding"]["enabled"] is False


# --- the plugin registry ---------------------------------------------------------------


def test_discovery_finds_the_shipped_strategy():
    found = discover()
    assert "ema_cross" in found and found["ema_cross"] is EmaCross


def test_create_builds_by_id_with_overrides():
    s = create("ema_cross", fast=5, slow=13)
    assert isinstance(s, EmaCross) and s.params["fast"] == 5


def test_create_rejects_an_unknown_id():
    with pytest.raises(KeyError, match="unknown strategy"):
        create("does_not_exist")


def test_register_rejects_an_incomplete_class():
    with pytest.raises(TypeError, match="missing required attribute"):

        @register
        class NoId:
            symbols = ["EURUSD"]
            timeframe = TF.H4

            def on_bar(self, ctx):
                return None


def test_register_rejects_a_class_without_on_bar():
    with pytest.raises(TypeError, match="on_bar"):

        @register
        class NoCallback:
            id, symbols, timeframe = "no_callback", ["EURUSD"], TF.H4


def test_register_rejects_a_duplicate_id():
    try:
        with pytest.raises(ValueError, match="already registered"):

            @register
            class Clash:
                id, symbols, timeframe = "ema_cross", ["EURUSD"], TF.H4

                def on_bar(self, ctx):
                    return None
    finally:
        REGISTRY["ema_cross"] = EmaCross


# --- the reference strategy ------------------------------------------------------------


def test_ema_cross_is_quiet_without_enough_history():
    assert EmaCross().on_bar(ctx_for([1.10] * 10)) is None


def test_ema_cross_signals_long_when_the_fast_line_crosses_up():
    """Fall for a long stretch, then rally hard enough to pull the fast EMA through the slow one."""
    prices = [1.20 - i * 0.001 for i in range(80)] + [1.13 + i * 0.004 for i in range(40)]
    rt = StrategyRuntime()
    rt.register(EmaCross(fast=5, slow=20))
    found = None
    for end in range(60, len(prices)):
        signals = rt.on_bar(ctx_for(prices[:end]))
        if signals:
            found = signals[0].intent
            break
    assert found is not None, "expected a cross somewhere in a falling-then-rising series"
    assert found.side is Side.LONG
    assert found.stop_price < found.take_price
    assert "crossed above" in found.reason


def test_ema_cross_stop_sits_below_the_close_for_a_long():
    prices = [1.20 - i * 0.001 for i in range(80)] + [1.13 + i * 0.004 for i in range(40)]
    s = EmaCross(fast=5, slow=20)
    for end in range(60, len(prices)):
        ctx = ctx_for(prices[:end])
        intent = s.on_bar(ctx)
        if intent:
            assert intent.stop_price < ctx.close()
            assert intent.take_price > ctx.close()
            return
    pytest.fail("no signal produced")


def test_ema_cross_refuses_a_nonsense_configuration():
    with pytest.raises(ValueError, match="fast period"):
        EmaCross(fast=50, slow=20)


# --- the mean-reversion strategy (P2-04) ------------------------------------------------


def test_meanrev_is_quiet_without_enough_history():
    from tradeapp.strategies.meanrev_m15 import MeanReversionM15

    assert MeanReversionM15().on_bar(ctx_for([1.10] * 20, tf=TF.M15)) is None


def test_meanrev_fades_a_stretch_below_the_average():
    """Drift down for a long time, drop hard, then print a bar that turns back up."""
    from tradeapp.strategies.meanrev_m15 import MeanReversionM15

    prices = [1.2000 - i * 0.0001 for i in range(80)] + [1.1900 - i * 0.0020 for i in range(12)]
    bars = make_bars(prices)
    turn = bars[-1]
    bars[-1] = Bar(
        time_utc=turn.time_utc,
        open=turn.close - 0.0030,
        high=turn.close + 0.0005,
        low=turn.close - 0.0035,
        close=turn.close,
    )
    ctx = Context(symbol="EURUSD", timeframe=TF.M15, bars=bars, now_utc=bars[-1].time_utc)

    intent = MeanReversionM15(ma=50, band_atr_mult=1.0, rsi_low=45).on_bar(ctx)
    assert intent is not None and intent.side is Side.LONG
    assert intent.stop_price < ctx.close() < intent.take_price
    assert "fading back to the average" in intent.reason


def test_meanrev_will_not_catch_a_falling_knife():
    """Stretched but still falling is not a reversion signal."""
    from tradeapp.strategies.meanrev_m15 import MeanReversionM15

    prices = [1.2000 - i * 0.0001 for i in range(80)] + [1.1900 - i * 0.0020 for i in range(12)]
    bars = make_bars(prices)
    last = bars[-1]
    bars[-1] = Bar(
        time_utc=last.time_utc,
        open=last.close + 0.0030,
        high=last.close + 0.0035,
        low=last.close - 0.0005,
        close=last.close,
    )
    ctx = Context(symbol="EURUSD", timeframe=TF.M15, bars=bars, now_utc=bars[-1].time_utc)
    assert MeanReversionM15(ma=50, band_atr_mult=1.0, rsi_low=45).on_bar(ctx) is None


def test_meanrev_is_quiet_in_a_calm_market():
    from tradeapp.strategies.meanrev_m15 import MeanReversionM15

    assert MeanReversionM15().on_bar(ctx_for([1.10] * 120, tf=TF.M15)) is None


def test_meanrev_refuses_a_nonsense_configuration():
    from tradeapp.strategies.meanrev_m15 import MeanReversionM15

    with pytest.raises(ValueError, match="must be positive"):
        MeanReversionM15(band_atr_mult=0)


def test_both_strategies_are_discoverable():
    found = discover()
    assert {"ema_cross", "meanrev_m15"} <= set(found)
