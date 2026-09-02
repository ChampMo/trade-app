"""Economic calendar and news blocking. The highest-value AI feature, with no model in it."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from tradeapp.calendar import CalendarEvent, CalendarStore, Impact, NewsWindows, load_file, parse_events

NFP = datetime(2026, 9, 4, 12, 30, tzinfo=UTC)


def store(tmp_path) -> CalendarStore:
    return CalendarStore(tmp_path / "calendar.db")


def event(at: datetime, currency="USD", title="Non-Farm Payrolls", impact=Impact.HIGH) -> CalendarEvent:
    return CalendarEvent(time_utc=at, currency=currency, title=title, impact=impact, source="test")


# --- the store ---------------------------------------------------------------------------


def test_round_trip(tmp_path):
    s = store(tmp_path)
    assert s.upsert([event(NFP)]) == 1
    rows = s.between(NFP - timedelta(hours=1), NFP + timedelta(hours=1))
    assert len(rows) == 1 and rows[0].title == "Non-Farm Payrolls"
    assert rows[0].impact is Impact.HIGH


def test_reimporting_the_same_events_adds_nothing(tmp_path):
    s = store(tmp_path)
    s.upsert([event(NFP)])
    assert s.upsert([event(NFP)]) == 0
    assert s.count() == 1


def test_a_revised_event_replaces_rather_than_duplicating(tmp_path):
    s = store(tmp_path)
    s.upsert([event(NFP, impact=Impact.MEDIUM)])
    s.upsert([event(NFP, impact=Impact.HIGH)])
    assert s.count() == 1
    assert s.between(NFP - timedelta(minutes=1), NFP + timedelta(minutes=1))[0].impact is Impact.HIGH


def test_filtering_by_currency_and_impact(tmp_path):
    s = store(tmp_path)
    s.upsert(
        [
            event(NFP, "USD", "NFP", Impact.HIGH),
            event(NFP, "EUR", "Retail Sales", Impact.MEDIUM),
            event(NFP, "GBP", "Some Speech", Impact.LOW),
        ]
    )
    window = (NFP - timedelta(hours=1), NFP + timedelta(hours=1))
    assert len(s.between(*window)) == 3
    assert len(s.between(*window, min_impact=Impact.MEDIUM)) == 2
    assert len(s.between(*window, min_impact=Impact.HIGH)) == 1
    assert len(s.between(*window, currencies=["EUR"])) == 1


def test_upcoming_looks_forward_only(tmp_path):
    s = store(tmp_path)
    s.upsert([event(NFP - timedelta(days=1)), event(NFP + timedelta(hours=2))])
    assert len(s.upcoming(NFP, hours=24)) == 1


def test_range_of_an_empty_store(tmp_path):
    assert store(tmp_path).range() == (None, None)


# --- parsing ------------------------------------------------------------------------------


def test_parses_this_projects_shape():
    rows = parse_events([{"time_utc": "2026-09-04T12:30:00Z", "currency": "USD", "title": "NFP", "impact": "High"}])
    assert len(rows) == 1 and rows[0].time_utc == NFP and rows[0].impact is Impact.HIGH


def test_parses_the_common_forexfactory_weekly_shape():
    """So a file grabbed from the usual place just works."""
    rows = parse_events(
        [
            {
                "title": "Non-Farm Employment Change",
                "country": "USD",
                "date": "2026-09-04T12:30:00+00:00",
                "impact": "High",
            }
        ]
    )
    assert len(rows) == 1 and rows[0].currency == "USD" and rows[0].impact is Impact.HIGH


def test_parses_an_events_wrapper_and_epoch_times():
    rows = parse_events({"events": [{"time": NFP.timestamp(), "currency": "EUR", "title": "CPI", "impact": 3}]})
    assert rows[0].time_utc == NFP and rows[0].impact is Impact.HIGH


def test_a_naive_time_is_read_as_utc():
    rows = parse_events([{"time_utc": "2026-09-04 12:30:00", "currency": "USD", "title": "NFP", "impact": "high"}])
    assert rows[0].time_utc == NFP


def test_one_bad_row_does_not_lose_the_whole_feed():
    """A single malformed entry must not leave the bot with no calendar at all."""
    rows = parse_events(
        [
            {"time_utc": "not a date", "currency": "USD", "title": "Broken"},
            {"currency": "USD", "title": "No time"},
            {"time_utc": "2026-09-04T12:30:00Z", "currency": "USD", "title": "NFP", "impact": "high"},
            "not even a dict",
        ]
    )
    assert len(rows) == 1 and rows[0].title == "NFP"


def test_impact_words_and_numbers_both_work():
    assert Impact.parse("High") is Impact.HIGH
    assert Impact.parse(3) is Impact.HIGH
    assert Impact.parse("red") is Impact.HIGH
    assert Impact.parse("Medium") is Impact.MEDIUM
    assert Impact.parse("holiday") is Impact.LOW
    assert Impact.parse(None) is Impact.LOW


def test_importing_a_file(tmp_path):
    path = tmp_path / "week.json"
    path.write_text(
        json.dumps([{"time_utc": "2026-09-04T12:30:00Z", "currency": "USD", "title": "NFP", "impact": "High"}]),
        encoding="utf-8",
    )
    s = store(tmp_path)
    assert load_file(path, s) == 1
    assert s.count() == 1


# --- the blocker the Risk Engine asks -------------------------------------------------------


def test_blocked_inside_the_window(tmp_path):
    s = store(tmp_path)
    s.upsert([event(NFP)])
    blocker = NewsWindows(s)

    assert blocker.blocked("EURUSD", NFP - timedelta(minutes=45)) is None
    assert blocker.blocked("EURUSD", NFP - timedelta(minutes=15)) is not None
    assert blocker.blocked("EURUSD", NFP) is not None
    assert blocker.blocked("EURUSD", NFP + timedelta(minutes=15)) is not None
    assert blocker.blocked("EURUSD", NFP + timedelta(minutes=45)) is None


def test_the_reason_says_what_and_when(tmp_path):
    s = store(tmp_path)
    s.upsert([event(NFP)])
    reason = NewsWindows(s).blocked("EURUSD", NFP - timedelta(minutes=12))
    assert "USD Non-Farm Payrolls" in reason and "HIGH" in reason and "in 12 min" in reason


def test_only_the_symbols_own_currencies_matter(tmp_path):
    s = store(tmp_path)
    s.upsert([event(NFP, currency="JPY", title="BoJ Rate")])
    blocker = NewsWindows(s)
    assert blocker.blocked("EURUSD", NFP) is None
    assert blocker.blocked("USDJPY", NFP) is not None


def test_low_impact_releases_do_not_stop_trading(tmp_path):
    """Blocking on every number would stop trading most of the day and teach you to switch it off."""
    s = store(tmp_path)
    s.upsert([event(NFP, title="Minor Survey", impact=Impact.MEDIUM)])
    assert NewsWindows(s).blocked("EURUSD", NFP) is None
    assert NewsWindows(s, min_impact=Impact.MEDIUM).blocked("EURUSD", NFP) is not None


def test_the_window_is_configurable(tmp_path):
    s = store(tmp_path)
    s.upsert([event(NFP)])
    wide = NewsWindows(s, before_min=120, after_min=120)
    assert wide.blocked("EURUSD", NFP - timedelta(minutes=90)) is not None


def test_an_empty_calendar_blocks_nothing(tmp_path):
    assert NewsWindows(store(tmp_path)).blocked("EURUSD", NFP) is None


def test_windows_list_is_ready_for_a_chart(tmp_path):
    s = store(tmp_path)
    s.upsert([event(NFP), event(NFP + timedelta(hours=6), title="FOMC")])
    rows = NewsWindows(s).windows("EURUSD", NFP - timedelta(hours=1), NFP + timedelta(hours=8))
    assert len(rows) == 2
    start, end, label = rows[0]
    assert end - start == timedelta(minutes=60) and "Non-Farm" in label


def test_the_cache_still_answers_as_time_moves(tmp_path):
    s = store(tmp_path)
    s.upsert([event(NFP), event(NFP + timedelta(hours=20), title="CPI")])
    blocker = NewsWindows(s, cache_hours=6)
    assert blocker.blocked("EURUSD", NFP) is not None
    assert blocker.blocked("EURUSD", NFP + timedelta(hours=20)) is not None  # cache refreshed forward
    assert blocker.blocked("EURUSD", NFP) is not None  # and still right when asked about the past


# --- wired into the Risk Engine ---------------------------------------------------------------


def test_the_risk_engine_refuses_a_trade_inside_a_news_window(tmp_path, journal):
    from tests.test_risk_engine import ctx, long_intent
    from tradeapp.risk import RejectReason, RiskEngine

    s = store(tmp_path)
    s.upsert([event(datetime(2026, 9, 3, 12, 15, tzinfo=UTC))])  # 15 min after the test's NOW
    engine = RiskEngine(news=NewsWindows(s), journal=journal)

    decision = engine.evaluate(long_intent(), "trend_h4", ctx())
    assert decision.reason is RejectReason.NEWS_BLOCK
    assert "Non-Farm Payrolls" in decision.detail


def test_without_a_release_nearby_the_engine_trades_normally(tmp_path, journal):
    from tests.test_risk_engine import ctx, long_intent
    from tradeapp.risk import RiskEngine

    s = store(tmp_path)
    s.upsert([event(datetime(2026, 9, 3, 20, 0, tzinfo=UTC))])  # hours away
    engine = RiskEngine(news=NewsWindows(s), journal=journal)
    assert engine.evaluate(long_intent(), "trend_h4", ctx()).approved


def test_fetch_url_needs_a_url_the_owner_chose():
    """There is deliberately no default feed; picking one is a decision with consequences."""
    import inspect

    from tradeapp.calendar import fetch_url

    params = inspect.signature(fetch_url).parameters
    assert params["url"].default is inspect.Parameter.empty


def test_fetching_from_a_feed(tmp_path):
    import httpx
    import respx

    payload = [{"time_utc": "2026-09-04T12:30:00Z", "currency": "USD", "title": "NFP", "impact": "High"}]
    with respx.mock:
        respx.get("https://example.invalid/cal.json").mock(return_value=httpx.Response(200, json=payload))
        from tradeapp.calendar import fetch_url

        s = store(tmp_path)
        assert fetch_url("https://example.invalid/cal.json", s) == 1
    assert s.count() == 1


def test_a_failing_feed_raises_rather_than_silently_emptying_the_calendar(tmp_path):
    import httpx
    import respx

    with respx.mock:
        respx.get("https://example.invalid/cal.json").mock(return_value=httpx.Response(500))
        from tradeapp.calendar import fetch_url

        with pytest.raises(httpx.HTTPStatusError):
            fetch_url("https://example.invalid/cal.json", store(tmp_path))
