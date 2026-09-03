"""Seeing a bigger timeframe without seeing the future (D30).

Multi-timeframe strategies are where most backtests in the world quietly cheat: the M15 bar at
13:15 is allowed to look at the H1 bar that closes at 14:00, because that bar already exists in
the file. Every test here is about the one rule that stops that — a higher-timeframe bar exists
only once its close time is at or before the close of the bar being decided on — and about the
loop and the backtest applying it identically (rule 04).
"""

from datetime import UTC, datetime, timedelta

import pytest

from tradeapp.backtest.broker import BacktestBroker
from tradeapp.context import Context
from tradeapp.contracts import TF, Bar
from tradeapp.indicators import aggregate_bars

T0 = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)  # a Tuesday, no weekend in the way


def m15(n: int, start: datetime = T0) -> list[Bar]:
    """n consecutive M15 bars with a recognisable shape: open = i, close = i + 0.5."""
    return [
        Bar(
            time_utc=start + timedelta(minutes=15 * i),
            open=float(i),
            high=float(i) + 1.0,
            low=float(i) - 1.0,
            close=float(i) + 0.5,
        )
        for i in range(n)
    ]


# --- aggregation ----------------------------------------------------------------------------


def test_four_m15_bars_make_one_h1_bar():
    h1 = aggregate_bars(m15(4), 60, from_minutes=15)
    assert len(h1) == 1
    bar = h1[0]
    assert bar.time_utc == T0
    assert bar.open == 0.0 and bar.close == 3.5  # first open, last close
    assert bar.high == 4.0 and bar.low == -1.0  # highest high, lowest low


def test_a_bucket_that_has_not_closed_is_not_returned():
    """Five M15 bars: one full hour, plus 15 minutes of the next. The next hour does not exist yet."""
    h1 = aggregate_bars(m15(5), 60, from_minutes=15)
    assert len(h1) == 1
    assert h1[0].time_utc == T0


def test_the_last_bucket_appears_exactly_when_it_closes():
    assert len(aggregate_bars(m15(7), 60, from_minutes=15)) == 1
    assert len(aggregate_bars(m15(8), 60, from_minutes=15)) == 2


def test_buckets_align_to_the_clock_not_to_the_first_bar():
    """MT5 starts an H1 bar on the hour. A series that begins at 00:30 has a short first bucket."""
    bars = m15(6, start=T0 + timedelta(minutes=30))  # 00:30 .. 01:45
    h1 = aggregate_bars(bars, 60, from_minutes=15)
    assert [b.time_utc for b in h1] == [T0, T0 + timedelta(hours=1)]
    assert h1[0].open == 0.0 and h1[0].close == 1.5  # two bars in the 00:xx bucket


def test_a_weekend_gap_does_not_merge_friday_into_monday():
    friday = m15(4, start=datetime(2026, 8, 28, 20, 0, tzinfo=UTC))
    monday = m15(4, start=datetime(2026, 8, 31, 0, 0, tzinfo=UTC))
    h1 = aggregate_bars(friday + monday, 60, from_minutes=15)
    assert [b.time_utc.day for b in h1] == [28, 31]


def test_a_timeframe_that_does_not_divide_is_refused():
    with pytest.raises(ValueError):
        aggregate_bars(m15(4), 50, from_minutes=15)


def test_nothing_in_nothing_out():
    assert aggregate_bars([], 60, from_minutes=15) == []


# --- the context applies the rule -----------------------------------------------------------


def ctx_with(bars: list[Bar], higher) -> Context:
    return Context(symbol="EURUSD", timeframe=TF.M15, bars=bars, now_utc=bars[-1].time_utc, higher_bars=higher)


def test_higher_drops_the_bar_that_is_still_forming():
    """At 13:15 the H1 bar for 13:00 exists in the file. It has not closed. It must not be visible."""
    ltf = m15(6)  # last bar opens 01:15, closes 01:30
    h1_in_file = aggregate_bars(m15(8), 60, from_minutes=15)  # two closed H1 bars: 00:00 and 01:00
    assert len(h1_in_file) == 2

    higher = ctx_with(ltf, lambda tf, n: h1_in_file).higher(TF.H1)

    assert [b.time_utc for b in higher.bars] == [T0]  # only 00:00; 01:00 closes at 02:00


def test_higher_shows_the_bar_the_moment_it_has_closed():
    ltf = m15(8)  # last bar opens 01:45, closes 02:00 — so the 01:00 H1 bar has just closed
    h1_in_file = aggregate_bars(m15(12), 60, from_minutes=15)

    higher = ctx_with(ltf, lambda tf, n: h1_in_file).higher(TF.H1)

    assert [b.time_utc for b in higher.bars] == [T0, T0 + timedelta(hours=1)]


def test_higher_is_a_full_context_so_indicators_work_on_it():
    ltf = m15(400)
    h1_in_file = aggregate_bars(ltf, 60, from_minutes=15)
    higher = ctx_with(ltf, lambda tf, n: h1_in_file).higher(TF.H1)

    assert higher.timeframe is TF.H1
    assert higher.ema(20) is not None and higher.atr(14) is not None
    assert higher.symbol == "EURUSD"


def test_asking_for_a_smaller_or_equal_timeframe_is_a_mistake_not_a_result():
    ctx = ctx_with(m15(4), lambda tf, n: [])
    with pytest.raises(ValueError):
        ctx.higher(TF.M15)
    with pytest.raises(ValueError):
        ctx.higher(TF.M5)


def test_a_context_with_no_provider_has_no_higher_view():
    assert Context(symbol="EURUSD", timeframe=TF.M15, bars=m15(4), now_utc=T0).higher(TF.H1) is None


def test_the_higher_view_is_cached_per_call():
    calls = []

    def provider(tf, n):
        calls.append(tf)
        return aggregate_bars(m15(8), 60, from_minutes=15)

    ctx = ctx_with(m15(8), provider)
    ctx.higher(TF.H1)
    ctx.higher(TF.H1)
    assert calls == [TF.H1]


# --- the backtest broker applies the same rule by construction --------------------------------


def test_the_backtest_broker_serves_only_closed_higher_bars():
    bars = m15(40)
    broker = BacktestBroker(bars_all=bars, symbol="EURUSD", timeframe=TF.M15, index=5)  # bar 5 opens 01:15
    h1 = broker.bars("EURUSD", TF.H1, 50)
    assert [b.time_utc for b in h1] == [T0]  # 00:00 closed; 01:00 is still forming at 01:30


def test_backtest_and_live_agree_on_what_the_higher_timeframe_looks_like():
    """Rule 04 for timeframes: the same M15 moment must produce the same H1 view either way."""
    bars = m15(60)
    index = 23  # bar 23 opens 05:45, closes 06:00 — the 05:00 H1 bar just closed
    broker = BacktestBroker(bars_all=bars, symbol="EURUSD", timeframe=TF.M15, index=index)

    from_backtest = broker.bars("EURUSD", TF.H1, 50)

    # "live": the whole H1 file is available (as MT5 would return it) and the Context filters it
    whole_file = aggregate_bars(bars, 60, from_minutes=15)
    live_ctx = ctx_with(bars[: index + 1], lambda tf, n: whole_file)
    from_live = live_ctx.higher(TF.H1).bars

    assert [b.time_utc for b in from_backtest] == [b.time_utc for b in from_live]
    assert from_live[-1].time_utc == T0 + timedelta(hours=5)
    assert [b.close for b in from_backtest] == [b.close for b in from_live]
