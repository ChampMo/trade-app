"""P0-08: the broker clock is not UTC, and getting that wrong skews every backtest-vs-live comparison."""

from datetime import UTC, datetime, timedelta

from tradeapp.broker.servertime import (
    ServerTimeOffset,
    epoch_to_server_wall,
    measure_offset,
    server_to_utc,
    utc_to_server,
)

NOW = datetime(2026, 9, 2, 16, 36, 0, tzinfo=UTC)


def _epoch_for(offset_min: int, age_s: float = 0.0, now: datetime = NOW) -> float:
    """What MT5 would report for a tick that happened `age_s` ago on a server `offset_min` from UTC."""
    server_wall = now + timedelta(minutes=offset_min) - timedelta(seconds=age_s)
    return server_wall.replace(tzinfo=UTC).timestamp()


def test_fresh_tick_gives_exact_offset():
    for offset in (0, 60, 120, 180, -300, 330):
        got = measure_offset(_epoch_for(offset), NOW)
        assert got.minutes == offset, offset
        assert got.confident and got.known
        assert abs(got.tick_age_s) < 1


def test_recent_tick_still_measures_and_reports_age():
    got = measure_offset(_epoch_for(120, age_s=240), NOW)
    assert got.minutes == 120
    assert got.confident is True
    assert 239 <= got.tick_age_s <= 241


def test_stale_tick_recovers_the_offset_but_is_not_confident():
    """A quiet market must not silently shift the offset to a wrong half hour."""
    got = measure_offset(_epoch_for(180, age_s=20 * 60), NOW)
    assert got.minutes == 180  # ceiling to the next half hour recovers the true value
    assert got.confident is False
    assert "min old" in got.note


def test_weekend_tick_refuses_to_guess():
    got = measure_offset(_epoch_for(180, age_s=2 * 24 * 3600), NOW)
    assert got.minutes is None and got.known is False and got.confident is False
    assert "closed" in got.note


def test_small_clock_skew_does_not_round_up_a_whole_step():
    """Local clock 30 s behind the server must not turn UTC+3 into UTC+3:30."""
    got = measure_offset(_epoch_for(180, age_s=-30), NOW)
    assert got.minutes == 180
    assert got.tick_age_s < 0


def test_conversions_round_trip():
    server_wall = datetime(2026, 9, 2, 19, 36, 0)  # broker showing 19:36 on a UTC+3 server
    as_utc = server_to_utc(server_wall, 180)
    assert as_utc == datetime(2026, 9, 2, 16, 36, tzinfo=UTC)
    assert utc_to_server(as_utc, 180) == server_wall


def test_conversions_handle_negative_offsets_and_naive_input():
    assert server_to_utc(datetime(2026, 1, 1, 0, 0), -120) == datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
    assert utc_to_server(datetime(2026, 1, 1, 2, 0), -120) == datetime(2026, 1, 1, 0, 0)


def test_epoch_to_server_wall_is_naive_and_unshifted():
    wall = epoch_to_server_wall(_epoch_for(180, now=NOW))
    assert wall.tzinfo is None
    assert wall == datetime(2026, 9, 2, 19, 36)


def test_describe_is_readable():
    assert "UTC+03:00" in ServerTimeOffset(180, 0.0, True, "fresh tick").describe()
    assert "UTC-05:30" in ServerTimeOffset(-330, 0.0, True, "fresh tick").describe()
    assert "unknown" in ServerTimeOffset(None, None, False, "market closed").describe()


# --- bridge integration: the offset is measured on connect and rides on every tick ---


def test_bridge_measures_offset_on_connect_and_converts_tick_time():
    from tests.fakes import FakeMT5Module
    from tradeapp.broker.mt5_bridge import MT5Broker

    mod = FakeMT5Module(trade_mode=0, server_offset_min=180)
    b = MT5Broker(mt5_module=mod)
    b.connect()
    assert b.server_offset.minutes == 180 and b.server_offset.confident

    tick = b.tick("EURUSD")
    assert tick.server_utc_offset_min == 180
    # the broker's clock reads three hours ahead of the UTC we store
    assert (tick.time_server.replace(tzinfo=UTC) - tick.time_utc) == timedelta(minutes=180)
    assert abs((tick.time_utc - datetime.now(UTC)).total_seconds()) < 5


def test_bridge_connects_even_when_the_offset_cannot_be_measured():
    from tests.fakes import FakeMT5Module
    from tradeapp.broker.mt5_bridge import MT5Broker

    mod = FakeMT5Module(trade_mode=0, tick=False)
    b = MT5Broker(mt5_module=mod)
    b.connect()  # must not raise: a quiet market is not a connection failure
    assert b.server_offset.known is False
    assert b.connected is True
