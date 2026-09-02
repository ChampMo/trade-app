"""Bar store: incremental sync that cannot duplicate, and gaps that mean something."""

from datetime import UTC, datetime, timedelta

from tradeapp.broker.fake import FakeBroker
from tradeapp.contracts import TF, Bar
from tradeapp.data import BarStore

MON = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)  # a Monday


def bars(n: int, start: datetime = MON, hours: int = 4, price: float = 1.1) -> list[Bar]:
    return [
        Bar(
            time_utc=start + timedelta(hours=hours * i),
            open=price,
            high=price + 0.001,
            low=price - 0.001,
            close=price,
            volume=100 + i,
            spread_points=20,
        )
        for i in range(n)
    ]


def store(tmp_path) -> BarStore:
    return BarStore(tmp_path / "history.db")


def test_round_trip(tmp_path):
    s = store(tmp_path)
    assert s.upsert("EURUSD", TF.H4, bars(10)) == 10
    loaded = s.load("EURUSD", TF.H4)
    assert len(loaded) == 10
    assert loaded[0].time_utc == MON
    assert loaded[0].spread_points == 20 and loaded[0].volume == 100


def test_resyncing_the_same_range_adds_nothing(tmp_path):
    s = store(tmp_path)
    s.upsert("EURUSD", TF.H4, bars(10))
    assert s.upsert("EURUSD", TF.H4, bars(10)) == 0
    assert s.count("EURUSD", TF.H4) == 10


def test_overlapping_sync_only_adds_the_new_part(tmp_path):
    s = store(tmp_path)
    s.upsert("EURUSD", TF.H4, bars(10))
    later = bars(10, start=MON + timedelta(hours=4 * 5))  # 5 bars overlap
    assert s.upsert("EURUSD", TF.H4, later) == 5
    assert s.count("EURUSD", TF.H4) == 15


def test_a_revised_bar_overwrites_rather_than_duplicating(tmp_path):
    """Brokers do revise history; the store must end up with one row, not two."""
    s = store(tmp_path)
    s.upsert("EURUSD", TF.H4, bars(3))
    revised = [Bar(time_utc=MON, open=9, high=9, low=9, close=9)]
    s.upsert("EURUSD", TF.H4, revised)
    assert s.count("EURUSD", TF.H4) == 3
    assert s.load("EURUSD", TF.H4)[0].close == 9


def test_symbols_and_timeframes_are_kept_apart(tmp_path):
    s = store(tmp_path)
    s.upsert("EURUSD", TF.H4, bars(5))
    s.upsert("EURUSD", TF.M15, bars(7))
    s.upsert("GBPUSD", TF.H4, bars(3))
    assert s.count("EURUSD", TF.H4) == 5
    assert s.count("EURUSD", TF.M15) == 7
    assert ("GBPUSD", "H4", 3) in s.symbols()


def test_range_and_empty_range(tmp_path):
    s = store(tmp_path)
    assert s.range("EURUSD", TF.H4) == (None, None)
    s.upsert("EURUSD", TF.H4, bars(4))
    first, last = s.range("EURUSD", TF.H4)
    assert first == MON and last == MON + timedelta(hours=12)


def test_loading_a_window(tmp_path):
    s = store(tmp_path)
    s.upsert("EURUSD", TF.H4, bars(20))
    window = s.load("EURUSD", TF.H4, start=MON + timedelta(hours=8), end=MON + timedelta(hours=20))
    assert [b.time_utc for b in window] == [MON + timedelta(hours=h) for h in (8, 12, 16, 20)]


def test_limit_returns_the_most_recent_bars_in_order(tmp_path):
    s = store(tmp_path)
    s.upsert("EURUSD", TF.H4, bars(20))
    recent = s.load("EURUSD", TF.H4, limit=3)
    assert len(recent) == 3
    assert recent[-1].time_utc == MON + timedelta(hours=4 * 19)
    assert recent[0].time_utc < recent[-1].time_utc  # still oldest first


def test_upserting_nothing_is_harmless(tmp_path):
    assert store(tmp_path).upsert("EURUSD", TF.H4, []) == 0


# --- gaps ------------------------------------------------------------------------------


def test_a_clean_series_has_no_gaps(tmp_path):
    s = store(tmp_path)
    s.upsert("EURUSD", TF.H4, bars(30))
    assert s.gaps("EURUSD", TF.H4) == []


def test_a_missing_stretch_is_reported(tmp_path):
    s = store(tmp_path)
    s.upsert("EURUSD", TF.H4, bars(5))
    s.upsert("EURUSD", TF.H4, bars(5, start=MON + timedelta(hours=4 * 12)))
    gaps = s.gaps("EURUSD", TF.H4)
    assert len(gaps) == 1 and gaps[0].missing_bars == 7
    assert "->" in str(gaps[0])


def test_the_weekend_is_the_market_being_shut_not_a_gap(tmp_path):
    """FX closes Friday night and reopens Sunday night. Reporting that buries the real holes."""
    s = store(tmp_path)
    friday_close = datetime(2026, 1, 9, 20, 0, tzinfo=UTC)
    sunday_open = datetime(2026, 1, 11, 22, 0, tzinfo=UTC)
    s.upsert("EURUSD", TF.H4, [bars(1, start=friday_close)[0], bars(1, start=sunday_open)[0]])
    assert s.gaps("EURUSD", TF.H4) == []


def test_a_hole_longer_than_a_weekend_is_still_reported(tmp_path):
    s = store(tmp_path)
    friday = datetime(2026, 1, 9, 20, 0, tzinfo=UTC)
    much_later = friday + timedelta(days=9)
    s.upsert("EURUSD", TF.H4, [bars(1, start=friday)[0], bars(1, start=much_later)[0]])
    assert len(s.gaps("EURUSD", TF.H4)) == 1


# --- sync from a broker ------------------------------------------------------------------


def test_sync_pulls_and_reports(tmp_path):
    broker = FakeBroker()
    broker.connect()
    broker.seed_bars(bars(50))
    s = store(tmp_path)

    report = s.sync_from_broker(broker, "EURUSD", TF.H4, count=50)
    assert report.fetched == 50 and report.stored == 50 and report.total == 50
    assert report.first_utc == MON
    assert "EURUSD H4" in str(report)

    again = s.sync_from_broker(broker, "EURUSD", TF.H4, count=50)
    assert again.stored == 0 and again.total == 50


def test_a_store_survives_being_reopened(tmp_path):
    path = tmp_path / "history.db"
    BarStore(path).upsert("EURUSD", TF.H4, bars(6))
    assert BarStore(path).count("EURUSD", TF.H4) == 6
