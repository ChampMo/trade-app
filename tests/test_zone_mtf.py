"""The zone strategy on a chart built to have exactly one setup in it.

Synthetic bars, so every assertion is about the mechanism: an H1 uptrend with a demand zone that a
strong rally left, then M15 price coming back into that zone and rejecting it. What the strategy
must produce from that is one long, with its stop under the zone and its target at the next
structure, and nothing at all when any one ingredient is missing.
"""

from datetime import UTC, datetime, timedelta

from tradeapp.context import Context
from tradeapp.contracts import TF, Bar, SymbolInfo
from tradeapp.indicators import aggregate_bars
from tradeapp.strategies.zone_mtf import ZoneMTF

T0 = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)  # 00:00 UTC; session windows are 6-9 and 12-16
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


def m15_path(closes: list[float], start: datetime = T0, wick: float = 0.0004) -> list[Bar]:
    """A path of M15 closes, each bar a small candle around its close."""
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        hi, lo = max(prev, c) + wick, min(prev, c) - wick
        out.append(Bar(time_utc=start + timedelta(minutes=15 * i), open=prev, high=hi, low=lo, close=c))
        prev = c
    return out


def chart_with_one_setup() -> list[Bar]:
    """Hours 0-30: quiet base. Hours 30-32: rally (the impulse). Then a rising zigzag so H1 reads
    'up'. The test then brings price back into the base zone."""
    closes: list[float] = []
    # 30 hours of flat base near 1.1000: long enough for the H1 ATR(14) to exist at the base bar,
    # because a zone is measured against the volatility of its own time and None is not a size.
    closes += [1.1000 + (i % 4) * 0.00005 for i in range(120)]
    closes += [1.1000 + 0.0006 * i for i in range(1, 9)]  # 2 hours of rally to ~1.1048
    # a rising zigzag with legs long enough to be fractal swings on H1: six hours up, four down,
    # each cycle ending higher than the last (higher highs and higher lows)
    level = 1.1048
    for _cycle in range(4):
        for _q in range(6 * 4):
            level += 0.00020
            closes.append(level)
        for _q in range(4 * 4):
            level -= 0.00018
            closes.append(level)
    return m15_path(closes)


def ctx_at(bars: list[Bar], last_bar: Bar) -> Context:
    seen = bars + [last_bar]
    return Context(
        symbol="EURUSD",
        timeframe=TF.M15,
        bars=seen,
        now_utc=last_bar.time_utc,
        symbol_info=EURUSD,
        higher_bars=lambda tf, n: aggregate_bars(seen, tf.minutes, from_minutes=15)[-n:],
    )


def rejection_bar_into(zone_low: float, zone_high: float, when: datetime) -> Bar:
    """Dips into the zone, closes back above it, bullish, in the top of its range."""
    return Bar(
        time_utc=when, open=zone_high + 0.0002, high=zone_high + 0.0012, low=zone_low + 0.0001, close=zone_high + 0.0011
    )


def find_zone(strategy: ZoneMTF, bars: list[Bar]):
    ctx = ctx_at(bars[:-1], bars[-1])
    setup = strategy._setup(ctx)
    assert setup is not None, "the chart should read as an uptrend with zones"
    direction, _, zs = setup
    demand = [z for z in zs if z.kind == "demand" and not z.broken]
    assert direction == "up" and demand, (direction, zs)
    return demand[-1]


def test_the_chart_is_read_as_an_uptrend_with_a_demand_zone():
    bars = chart_with_one_setup()
    zone = find_zone(ZoneMTF(), bars)
    assert zone.low < zone.high < 1.1010


def test_a_rejection_inside_the_zone_in_session_is_a_long_with_structural_risk():
    bars = chart_with_one_setup()
    zone = find_zone(ZoneMTF(), bars)
    when = bars[-1].time_utc + timedelta(minutes=15)
    when = when.replace(hour=13, minute=0)  # inside the 12-16 window
    strategy = ZoneMTF(min_rr=0.5)  # the synthetic target is close; the RR gate is tested separately

    intent = strategy.on_bar(ctx_at(bars, rejection_bar_into(zone.low, zone.high, when)))

    assert intent is not None and intent.side.value == "LONG"
    assert intent.stop_price < zone.low  # beyond the far edge of the zone
    assert intent.stop_price == round(zone.low - 20 * 0.00001, 5)  # by exactly one spread
    assert intent.take_price > intent.stop_price
    assert "demand zone" in intent.reason and "H1 trend up" in intent.reason


def test_out_of_session_the_same_bar_does_nothing():
    bars = chart_with_one_setup()
    zone = find_zone(ZoneMTF(), bars)
    when = bars[-1].time_utc.replace(hour=3, minute=0)  # Asia: nothing trades
    assert ZoneMTF(min_rr=0.5).on_bar(ctx_at(bars, rejection_bar_into(zone.low, zone.high, when))) is None


def test_a_target_too_close_for_the_minimum_rr_is_refused_before_it_exists():
    bars = chart_with_one_setup()
    zone = find_zone(ZoneMTF(), bars)
    when = bars[-1].time_utc.replace(hour=13, minute=0)
    assert ZoneMTF(min_rr=50.0).on_bar(ctx_at(bars, rejection_bar_into(zone.low, zone.high, when))) is None


def test_a_bar_that_does_not_reject_the_zone_is_not_a_trigger():
    bars = chart_with_one_setup()
    zone = find_zone(ZoneMTF(), bars)
    when = bars[-1].time_utc.replace(hour=13, minute=0)
    weak = Bar(
        time_utc=when, open=zone.high + 0.0004, high=zone.high + 0.0005, low=zone.low + 0.0001, close=zone.low + 0.0002
    )
    assert ZoneMTF(min_rr=0.5).on_bar(ctx_at(bars, weak)) is None  # closed near its low: not a rejection


def test_a_wide_spread_bar_is_skipped():
    bars = chart_with_one_setup()
    zone = find_zone(ZoneMTF(), bars)
    when = bars[-1].time_utc.replace(hour=13, minute=0)
    ctx = ctx_at(bars, rejection_bar_into(zone.low, zone.high, when))
    wide = Context(**{**ctx.__dict__, "symbol_info": SymbolInfo(**{**EURUSD.__dict__, "spread_points": 60})})
    assert ZoneMTF(min_rr=0.5).on_bar(wide) is None


def test_a_flat_higher_timeframe_means_no_trade():
    flat = m15_path([1.1000 + ((i % 8) - 4) * 0.00005 for i in range(400)])
    assert ZoneMTF()._setup(ctx_at(flat[:-1], flat[-1])) is None


def test_exits_are_off_unless_asked_for():
    assert ZoneMTF().manage(None, None, None) is None
    assert ZoneMTF(break_even_r=1.0).params["break_even_r"] == 1.0
