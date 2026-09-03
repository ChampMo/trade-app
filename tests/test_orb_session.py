"""The opening-range breakout on a day built to have one breakout in it."""

from datetime import UTC, datetime, timedelta

from tradeapp.context import Context
from tradeapp.contracts import TF, Bar, SymbolInfo
from tradeapp.strategies.orb_session import OpeningRangeBreakout

DAY = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
EURUSD = SymbolInfo(
    symbol="EURUSD",
    digits=5,
    point=0.00001,
    volume_min=0.01,
    volume_step=0.01,
    stops_level_points=0,
    spread_points=20,
    trade_allowed=True,
    tick_size=0.00001,
    tick_value=1.0,
    volume_max=100.0,
    contract_size=100_000.0,
)


def bar(hour: int, minute: int, o: float, h: float, lo: float, c: float, day: datetime = DAY) -> Bar:
    return Bar(time_utc=day.replace(hour=hour, minute=minute), open=o, high=h, low=lo, close=c)


def a_day(breakout_close: float | None = None, at=(8, 30), range_height: float = 0.0010) -> list[Bar]:
    """Quiet night, a 07:00 hour that sets a range, then bars that either break it or do not."""
    bars = [bar(h, m, 1.1000, 1.1002, 1.0998, 1.1000) for h in range(0, 7) for m in (0, 15, 30, 45)]
    top, bottom = 1.1000 + range_height / 2, 1.1000 - range_height / 2
    bars += [
        bar(7, 0, 1.1000, top, 1.0999, 1.1002),
        bar(7, 15, 1.1002, 1.1003, bottom, 1.0999),
        bar(7, 30, 1.0999, 1.1002, 1.0998, 1.1001),
        bar(7, 45, 1.1001, 1.1002, 1.0999, 1.1000),
    ]
    t = DAY.replace(hour=8, minute=0)
    while (t.hour, t.minute) < at:
        bars.append(bar(t.hour, t.minute, 1.1000, 1.1003, 1.0997, 1.1001))
        t += timedelta(minutes=15)
    if breakout_close is not None:
        bars.append(
            bar(
                at[0],
                at[1],
                1.1001,
                max(breakout_close, 1.1001) + 0.0001,
                min(breakout_close, 1.1001) - 0.0001,
                breakout_close,
            )
        )
    return bars


def ctx_for(bars: list[Bar]) -> Context:
    return Context(symbol="EURUSD", timeframe=TF.M15, bars=bars, now_utc=bars[-1].time_utc, symbol_info=EURUSD)


def test_the_first_close_above_the_range_is_a_long_with_the_stop_under_the_range():
    intent = OpeningRangeBreakout().on_bar(ctx_for(a_day(breakout_close=1.1012)))
    assert intent is not None and intent.side.value == "LONG"
    assert intent.stop_price == round(1.0995 - 0.0002, 5)  # range low minus one spread
    assert intent.take_price > intent.stop_price
    assert "broken up" in intent.reason


def test_a_close_below_the_range_is_a_short():
    intent = OpeningRangeBreakout().on_bar(ctx_for(a_day(breakout_close=1.0988)))
    assert intent is not None and intent.side.value == "SHORT"
    assert intent.stop_price == round(1.1005 + 0.0002, 5)


def test_a_close_inside_the_range_is_nothing():
    assert OpeningRangeBreakout().on_bar(ctx_for(a_day(breakout_close=1.1001))) is None


def test_only_the_first_breakout_of_the_day_counts():
    """Restart-safe: the answer comes from the bars, not from something remembered."""
    bars = a_day(breakout_close=1.1012)  # first breakout at 08:30
    bars.append(bar(8, 45, 1.1012, 1.1016, 1.1010, 1.1015))  # still above: a second close outside
    assert OpeningRangeBreakout().on_bar(ctx_for(bars)) is None


def test_a_range_narrower_than_three_spreads_is_not_traded():
    bars = a_day(breakout_close=1.1012, range_height=0.0004)  # 40 points; three spreads is 60
    assert OpeningRangeBreakout().on_bar(ctx_for(bars)) is None


def test_after_the_window_closes_a_breakout_is_ignored():
    assert OpeningRangeBreakout(until_hour=8).on_bar(ctx_for(a_day(breakout_close=1.1012))) is None


def test_the_target_is_rr_times_the_risk():
    intent = OpeningRangeBreakout(rr=3.0).on_bar(ctx_for(a_day(breakout_close=1.1012)))
    risk = intent.take_price - 1.1012
    assert abs(risk - 3.0 * (1.1012 - intent.stop_price)) < 1e-6


def test_without_a_complete_opening_hour_there_is_no_range():
    bars = [b for b in a_day(breakout_close=1.1012) if not (b.time_utc.hour == 7 and b.time_utc.minute == 45)]
    assert OpeningRangeBreakout().on_bar(ctx_for(bars)) is None


def test_a_wide_spread_bar_is_skipped():
    ctx = ctx_for(a_day(breakout_close=1.1012))
    wide = Context(**{**ctx.__dict__, "symbol_info": SymbolInfo(**{**EURUSD.__dict__, "spread_points": 60})})
    assert OpeningRangeBreakout().on_bar(wide) is None
