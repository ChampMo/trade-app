"""Indicators must agree with MetaTrader, or backtest, live and the chart tell three stories."""

from datetime import UTC, datetime, timedelta

import pytest

from tradeapp.contracts import Bar
from tradeapp.indicators import atr, closes, ema, rsi, sma, true_range, wilder


def bars_from(prices, highs=None, lows=None) -> list[Bar]:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    out = []
    for i, p in enumerate(prices):
        out.append(
            Bar(
                time_utc=t0 + timedelta(hours=i),
                open=p,
                high=highs[i] if highs else p + 1,
                low=lows[i] if lows else p - 1,
                close=p,
            )
        )
    return out


def test_sma_matches_hand_calculation():
    got = sma([1, 2, 3, 4, 5], 3)
    assert got[:2] == [None, None]
    assert got[2:] == [2.0, 3.0, 4.0]


def test_ema_is_seeded_with_the_sma_like_mt5():
    """First value is the SMA of the first `period`, then the usual 2/(n+1) recursion."""
    got = ema([1, 2, 3, 4, 5], 3)
    assert got[:2] == [None, None]
    assert got[2] == pytest.approx(2.0)  # SMA seed
    assert got[3] == pytest.approx(4 * 0.5 + 2.0 * 0.5)  # 3.0
    assert got[4] == pytest.approx(5 * 0.5 + 3.0 * 0.5)  # 4.0


def test_ema_of_a_flat_series_is_that_value():
    assert ema([7.0] * 10, 5)[-1] == pytest.approx(7.0)


def test_series_are_all_none_before_there_is_enough_history():
    assert ema([1, 2], 5) == [None, None]
    assert sma([1, 2], 5) == [None, None]
    assert rsi([1, 2], 5) == [None, None]


def test_period_must_be_positive():
    with pytest.raises(ValueError):
        ema([1, 2, 3], 0)


def test_true_range_uses_the_previous_close():
    bars = bars_from([10, 20], highs=[11, 21], lows=[9, 19])
    tr = true_range(bars)
    assert tr[0] == pytest.approx(2.0)  # first bar: high - low
    assert tr[1] == pytest.approx(11.0)  # max(2, |21-10|, |19-10|) = 11


def test_atr_uses_wilder_smoothing():
    bars = bars_from([10] * 5, highs=[12] * 5, lows=[8] * 5)
    got = atr(bars, 3)
    assert got[:2] == [None, None]
    assert got[2] == pytest.approx(4.0)  # every TR is 4
    assert got[-1] == pytest.approx(4.0)


def test_wilder_differs_from_a_simple_average():
    values = [1, 2, 3, 4, 5, 6]
    w = wilder(values, 3)
    s = sma(values, 3)
    assert w[2] == pytest.approx(s[2])  # same seed
    assert w[-1] != pytest.approx(s[-1])  # then they diverge


def test_rsi_is_100_when_price_only_rises():
    assert rsi(list(range(1, 30)), 14)[-1] == pytest.approx(100.0)


def test_rsi_is_zero_when_price_only_falls():
    assert rsi(list(range(30, 1, -1)), 14)[-1] == pytest.approx(0.0)


def test_rsi_of_a_symmetric_saw_sits_near_fifty():
    values = [10 + (1 if i % 2 else -1) for i in range(60)]
    assert rsi(values, 14)[-1] == pytest.approx(50.0, abs=2.0)


def test_rsi_first_value_appears_one_bar_after_the_period():
    got = rsi(list(range(1, 20)), 14)
    assert got[13] is None and got[14] is not None


def test_closes_extracts_the_close_series():
    assert closes(bars_from([1, 2, 3])) == [1, 2, 3]
