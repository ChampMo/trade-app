"""Paper broker: live prices, imaginary fills, and nothing reaching the real broker."""

import pytest

from tradeapp.broker.fake import FakeBehavior, FakeBroker
from tradeapp.broker.paper import PaperBroker, PaperFills
from tradeapp.contracts import OrderRequest, Side


def paper(**fills) -> PaperBroker:
    source = FakeBroker(behavior=FakeBehavior(spread_points=20))
    b = PaperBroker(source, balance=10_000.0, fills=PaperFills(**fills))
    b.connect()
    return b


def an_order(side: Side = Side.LONG, volume: float = 0.1, sl: float = 1.08400, tp: float | None = None):
    return OrderRequest(symbol="EURUSD", side=side, volume=volume, stop_price=sl, take_price=tp, magic=1)


def test_orders_never_reach_the_real_broker():
    """The whole point: the loop runs for real, the market does not hear about it."""
    b = paper()
    b.market_order(an_order())
    assert b.source.sent == []  # the wrapped broker was never asked to trade
    assert b.source.open_tickets == []
    assert len(b.positions()) == 1


def test_market_data_reads_through_to_the_real_broker():
    from tradeapp.contracts import TF

    b = paper()
    assert b.tick("EURUSD").bid == b.source.bid
    assert b.symbol_info("EURUSD").spread_points == 20
    assert len(b.bars("EURUSD", TF.H4, 50)) == 50


def test_the_balance_is_ours_not_the_accounts():
    """Paper trading must not report the demo account's equity as if the imaginary trades happened."""
    b = paper()
    b.source.balance = 999_999.0
    assert b.account().balance == 10_000.0
    assert "(paper)" in b.account().server


def test_a_fill_costs_the_spread_and_slippage():
    b = paper(slippage_points=2)
    res = b.market_order(an_order())
    assert res.price_requested == pytest.approx(b.source.ask)
    assert res.price_filled == pytest.approx(round(b.source.ask + 2 * 0.00001, 5))


def test_a_stop_fills_when_a_tick_reaches_it():
    b = paper()
    res = b.market_order(an_order(sl=1.08600))
    assert b.position(res.position_ticket) is not None

    b.source.move(-200)  # bid falls through the stop
    b.tick("EURUSD")  # a tick arriving is what triggers the check, as in real life

    assert b.positions() == []
    assert b.closed[0]["reason"] == "stop"


def test_a_target_fills_when_a_tick_reaches_it():
    b = paper()
    res = b.market_order(an_order(sl=1.08400, tp=1.08900))
    b.source.move(300)
    b.tick("EURUSD")
    assert b.positions() == [] and b.closed[0]["reason"] == "target"
    assert res.position_ticket == b.closed[0]["ticket"]


def test_a_gap_past_the_stop_fills_at_the_price_that_was_there():
    """No intrabar guessing: it fills where the market actually was, which is what happens for real."""
    b = paper()
    b.market_order(an_order(sl=1.08600))
    b.source.move(-500)  # a jump well beyond the stop
    b.tick("EURUSD")
    assert b.closed[0]["exit"] == pytest.approx(b.source.bid)
    assert b.closed[0]["exit"] < 1.08600


def test_closing_by_signal_is_recorded_separately():
    b = paper()
    res = b.market_order(an_order())
    assert b.close_position(res.position_ticket).ok
    assert b.closed[0]["reason"] == "signal"


def test_the_balance_moves_with_realised_profit_only():
    b = paper()
    res = b.market_order(an_order(volume=0.1))
    b.source.move(100)
    b.tick("EURUSD")
    assert b.account().balance == 10_000.0  # still floating
    assert b.account().equity > 10_000.0

    b.close_position(res.position_ticket)
    assert b.account().balance > 10_000.0


def test_commission_is_charged_on_open():
    b = paper(commission_per_lot_round_trip=7.0)
    b.market_order(an_order(volume=0.5))
    assert b.account().balance == pytest.approx(9_996.5)


def test_modify_updates_the_stop():
    b = paper()
    res = b.market_order(an_order(sl=1.08400))
    assert b.modify_sltp(res.position_ticket, 1.08500, None).ok
    assert b.position(res.position_ticket).sl == 1.08500


def test_closing_a_position_that_is_gone_is_reported_not_raised():
    b = paper()
    assert b.close_position(12345).retcode_desc == "POSITION_CLOSED"


def test_it_refuses_to_trade_before_connecting():
    from tradeapp.contracts import BrokerError

    b = PaperBroker(FakeBroker())
    with pytest.raises(BrokerError):
        b.market_order(an_order())


def test_the_core_loop_runs_against_it(journal):
    """The real proof: the whole loop drives a paper broker without any special casing."""
    from datetime import UTC, datetime, timedelta

    from tradeapp.contracts import TF, Bar, Intent
    from tradeapp.core import Core, CoreConfig
    from tradeapp.runtime import StrategyRuntime

    now = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    bars = [
        Bar(
            time_utc=now - timedelta(hours=4 * (59 - i)),
            open=1.1000 + i * 0.0001,
            high=1.1005 + i * 0.0001,
            low=1.0995 + i * 0.0001,
            close=1.1000 + i * 0.0001,
        )
        for i in range(60)
    ]
    source = FakeBroker()
    source.seed_bars(bars)
    source.bid = bars[-1].close
    broker = PaperBroker(source, balance=10_000.0)

    class AlwaysLong:
        id, symbols, timeframe = "always_long", ["EURUSD"], TF.H4

        def on_bar(self, ctx):
            return Intent(
                symbol=ctx.symbol,
                side=Side.LONG,
                confidence=1.0,
                stop_price=round(ctx.close() - 0.0020, 5),
                take_price=round(ctx.close() + 0.0040, 5),
                reason="paper",
            )

    runtime = StrategyRuntime(journal)
    runtime.register(AlwaysLong())
    core = Core(broker, journal, runtime=runtime, config=CoreConfig(), now=lambda: now, sleep=lambda _s: None)
    core.start()
    report = core.tick()

    assert report.sent == 1
    assert len(broker.positions()) == 1
    assert source.sent == []  # and still nothing reached the market
