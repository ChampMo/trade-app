"""Swings, trend and zones, tested to the point.

The definitions are simple on purpose (a swing is a fractal, a trend is the last two swings, a
zone is the bar a strong move left from), and the tests are mostly about the property that keeps
them honest: nothing is known before it could have been known.
"""

from datetime import UTC, datetime, timedelta

from tradeapp.contracts import Bar
from tradeapp.structure import Swing, nearest, swings, trend, zones

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def bars_from(highs, lows=None, closes=None) -> list[Bar]:
    lows = lows or [h - 1.0 for h in highs]
    closes = closes or [(h + lo) / 2 for h, lo in zip(highs, lows, strict=True)]
    return [
        Bar(time_utc=T0 + timedelta(hours=i), open=c, high=h, low=lo, close=c)
        for i, (h, lo, c) in enumerate(zip(highs, lows, closes, strict=True))
    ]


# --- swings -------------------------------------------------------------------------------


def test_a_peak_is_a_swing_high_only_once_enough_bars_follow_it():
    highs = [1, 2, 3, 5, 3, 2, 1, 1]  # the 5 at index 3 is the peak
    found = [s for s in swings(bars_from(highs), left=2, right=2) if s.kind == "high"]
    assert [s.index for s in found] == [3]
    assert found[0].confirmed_at == 5  # known two bars later, not on the bar itself
    assert found[0].price == 5


def test_the_last_bars_can_never_be_swings_yet():
    highs = [1, 2, 3, 4, 5]  # rising into the present: the 5 might be a peak, nobody knows
    assert [s for s in swings(bars_from(highs), left=2, right=2) if s.kind == "high"] == []


def test_a_flat_top_is_one_swing_at_its_last_bar():
    """Two equal highs: price turned from the second one, and a plateau must not count twice."""
    highs = [1, 2, 5, 5, 2, 1, 1]
    found = [s for s in swings(bars_from(highs), left=2, right=2) if s.kind == "high"]
    assert [s.index for s in found] == [3]


def test_swing_lows_mirror_swing_highs():
    highs = [5, 4, 3, 2, 3, 4, 5, 5]
    found = [s for s in swings(bars_from(highs), left=2, right=2) if s.kind == "low"]
    assert [s.index for s in found] == [3]
    assert found[0].price == 1.0  # low is high - 1 in this helper


# --- trend ----------------------------------------------------------------------------------


def sw(kind, price, i):
    return Swing(i, i + 2, price, kind, T0 + timedelta(hours=i))


def test_higher_highs_and_higher_lows_are_an_uptrend():
    assert trend([sw("low", 1, 0), sw("high", 3, 2), sw("low", 2, 4), sw("high", 4, 6)]) == "up"


def test_lower_highs_and_lower_lows_are_a_downtrend():
    assert trend([sw("high", 4, 0), sw("low", 2, 2), sw("high", 3, 4), sw("low", 1, 6)]) == "down"


def test_a_higher_high_with_a_lower_low_is_not_a_trend():
    assert trend([sw("low", 2, 0), sw("high", 3, 2), sw("low", 1, 4), sw("high", 4, 6)]) == "flat"


def test_too_few_swings_is_flat_not_a_guess():
    assert trend([sw("low", 1, 0), sw("high", 3, 2)]) == "flat"
    assert trend([]) == "flat"


# --- zones ----------------------------------------------------------------------------------


def test_a_bar_that_a_strong_rally_left_from_is_a_demand_zone():
    #        base  impulse -->
    highs = [10, 10.2, 12.5, 13.0, 13.2, 13.1]
    lows = [9.8, 9.9, 10.5, 12.4, 12.8, 12.7]
    closes = [10.0, 10.1, 12.4, 12.9, 13.0, 13.0]
    atr = [0.5] * 6
    z = zones(bars_from(highs, lows, closes), atr, impulse_mult=2.0)

    demand = [x for x in z if x.kind == "demand"]
    assert demand, "the rally from bar 1 should have left a demand zone"
    zone = demand[0]
    assert zone.base_index == 1 and (zone.low, zone.high) == (9.9, 10.2)
    assert zone.born_index == 2  # known when the impulse bar closed, not before


def test_a_zone_is_born_on_the_impulse_bar_not_on_the_base():
    """The base bar itself does not know it is a base. Only the move away reveals it."""
    highs = [10, 10.2, 12.5, 13.0]
    lows = [9.8, 9.9, 10.5, 12.4]
    closes = [10.0, 10.1, 12.4, 12.9]
    z = zones(bars_from(highs, lows, closes), [0.5] * 4)
    assert all(x.born_index > x.base_index for x in z)


def test_a_zone_breaks_when_price_closes_beyond_its_far_edge():
    highs = [10, 10.2, 12.5, 13.0, 11.0, 9.5]
    lows = [9.8, 9.9, 10.5, 12.4, 9.7, 9.0]
    closes = [10.0, 10.1, 12.4, 12.9, 9.8, 9.2]  # the last close is below the zone low of 9.9
    z = [x for x in zones(bars_from(highs, lows, closes), [0.5] * 6) if x.kind == "demand"]
    assert z and z[0].broken is True


def test_a_touch_is_counted_and_costs_freshness():
    highs = [10, 10.2, 12.5, 13.0, 11.0, 12.0]
    lows = [9.8, 9.9, 10.5, 12.4, 10.0, 11.5]  # bar 4 dips to 10.0, inside the 9.9-10.2 zone
    closes = [10.0, 10.1, 12.4, 12.9, 10.8, 11.9]
    z = [x for x in zones(bars_from(highs, lows, closes), [0.5] * 6) if x.kind == "demand"]
    assert z[0].touches == 1 and z[0].fresh is False and z[0].broken is False


def test_supply_is_the_mirror():
    highs = [13.0, 13.1, 12.6, 10.4, 10.0, 10.1]
    lows = [12.8, 12.9, 10.4, 9.9, 9.7, 9.8]
    closes = [12.9, 13.0, 10.5, 10.0, 9.8, 10.0]
    z = [x for x in zones(bars_from(highs, lows, closes), [0.5] * 6) if x.kind == "supply"]
    assert z and z[0].base_index == 1 and z[0].kind == "supply"


def test_no_atr_no_zone():
    highs = [10, 10.2, 12.5, 13.0]
    assert zones(bars_from(highs), [None] * 4) == []


def test_nearest_demand_is_the_highest_unbroken_one_below_the_price():
    from tradeapp.structure import Zone

    zs = [
        Zone("demand", 9.0, 9.5, 0, 2, T0),
        Zone("demand", 10.0, 10.5, 3, 5, T0, broken=True),
        Zone("demand", 9.6, 9.9, 6, 8, T0),
        Zone("supply", 12.0, 12.5, 9, 11, T0),
    ]
    assert nearest(zs, 11.0, "demand").low == 9.6  # 10.0 is closer but broken
    assert nearest(zs, 11.0, "supply").low == 12.0
    assert nearest(zs, 8.0, "demand") is None  # nothing below 8
