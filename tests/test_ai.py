"""The run-time AI layer: bounded output, a budget, and failure that never stops the bot."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from tradeapp.ai import AnalystView, BudgetExceeded, DeepSeekClient, Pricing
from tradeapp.ai.analyst import Analyst, build_prompt
from tradeapp.ai.schemas import ReviewReport, ScoutReport, extract_json, parse
from tradeapp.calendar import CalendarEvent, CalendarStore, Impact
from tradeapp.context import Context
from tradeapp.contracts import TF, Bar
from tradeapp.journal import Journal

URL = "https://api.deepseek.test/chat/completions"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

GOOD = {"regime": "risk-off", "bias": -0.3, "size_mult": 0.8, "block": False, "valid_minutes": 60, "note": "quiet"}


def reply(content: str, tokens_in: int = 500, tokens_out: int = 60) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
        },
    )


def client(journal: Journal, key: str | None = "sk-test", **kw) -> DeepSeekClient:
    return DeepSeekClient(key, journal, url=URL, now=lambda: NOW, **kw)


def ctx_for(n: int = 80) -> Context:
    bars = [
        Bar(
            time_utc=NOW - timedelta(hours=4 * (n - 1 - i)),
            open=1.1000 + i * 0.0002,
            high=1.1005 + i * 0.0002,
            low=1.0995 + i * 0.0002,
            close=1.1000 + i * 0.0002,
        )
        for i in range(n)
    ]
    return Context(symbol="EURUSD", timeframe=TF.H4, bars=bars, now_utc=NOW)


# --- the schema is the whole safety story --------------------------------------------------


def test_a_valid_view_parses():
    view = parse(AnalystView, '{"regime":"risk-off","bias":-0.3,"size_mult":0.8,"block":false,"valid_minutes":60}')
    assert view.bias == -0.3 and view.block is False


@pytest.mark.parametrize(
    "bad",
    [
        '{"regime":"x","bias":5,"size_mult":1,"block":false,"valid_minutes":60}',  # bias out of range
        '{"regime":"x","bias":0,"size_mult":99,"block":false,"valid_minutes":60}',  # size out of range
        '{"regime":"x","bias":0,"size_mult":1,"block":false,"valid_minutes":99999}',  # silly expiry
        '{"regime":"x","bias":0,"size_mult":1,"block":"maybe","valid_minutes":60}',  # not a bool
        '{"regime":"x","bias":0,"size_mult":1,"block":false}',  # missing expiry
        '{"regime":"x","bias":0,"size_mult":1,"block":false,"valid_minutes":60,"buy_now":true}',  # extra field
    ],
)
def test_out_of_bounds_replies_are_rejected(bad):
    """The model cannot talk its way outside the four numbers it is allowed to move."""
    with pytest.raises(ValueError):
        parse(AnalystView, bad)


def test_json_is_found_inside_a_code_fence_or_prose():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('Sure! Here you go:\n{"a": 1}\nHope that helps.') == '{"a": 1}'
    with pytest.raises(ValueError, match="no JSON object"):
        extract_json("I would rather not.")


def test_the_parse_error_says_what_was_wrong():
    with pytest.raises(ValueError, match="bias"):
        parse(AnalystView, '{"regime":"x","bias":9,"size_mult":1,"block":false,"valid_minutes":60}')


def test_the_other_agents_have_schemas_too():
    parse(ScoutReport, '{"events":[{"title":"NFP","currency":"USD","impact":"HIGH","summary":"jobs"}]}')
    parse(ReviewReport, '{"summary":"quiet day","findings":[{"classification":"variance","note":"normal"}]}')
    with pytest.raises(ValueError):
        parse(ReviewReport, '{"summary":"x","findings":[{"classification":"increase_size","note":"n"}]}')


# --- the client -----------------------------------------------------------------------------


def test_a_call_records_the_raw_prompt_and_reply(journal: Journal):
    from sqlalchemy import select

    from tradeapp.journal.models import AICall

    with respx.mock:
        respx.post(URL).mock(return_value=reply('{"ok": true}'))
        client(journal).ask("analyst", "system text", "user text")

    with journal.session() as s:
        row = s.execute(select(AICall)).scalars().one()
    assert "system text" in row.prompt and "user text" in row.prompt
    assert row.response == '{"ok": true}'
    assert row.tokens_in == 500 and row.tokens_out == 60 and row.cost_usd > 0


def test_spend_accumulates_and_survives_a_new_client(journal: Journal):
    with respx.mock:
        respx.post(URL).mock(return_value=reply('{"ok":true}', tokens_in=1_000_000, tokens_out=0))
        c = client(journal, pricing=Pricing(input_per_m=1.0, output_per_m=1.0))
        c.ask("analyst", "s", "u")
        assert c.spent_today == pytest.approx(1.0)

    assert client(journal).spent_today == pytest.approx(1.0)  # read back from the journal


def test_the_budget_stops_calls_without_stopping_the_system(journal: Journal):
    c = client(journal, daily_budget_usd=0.5, pricing=Pricing(input_per_m=1.0, output_per_m=1.0))
    with respx.mock:
        respx.post(URL).mock(return_value=reply('{"ok":true}', tokens_in=1_000_000, tokens_out=0))
        c.ask("analyst", "s", "u")

    assert c.available is False
    with pytest.raises(BudgetExceeded, match="keeps trading on rules"):
        c.ask("analyst", "s", "u")


def test_no_key_means_unavailable_not_broken(journal: Journal):
    c = client(journal, key=None)
    assert c.available is False
    with pytest.raises(BudgetExceeded, match="no DeepSeek API key"):
        c.ask("analyst", "s", "u")


def test_a_failed_request_is_journaled_and_raised(journal: Journal):
    with respx.mock:
        respx.post(URL).mock(return_value=httpx.Response(500))
        with pytest.raises(RuntimeError, match="DeepSeek call failed"):
            client(journal).ask("analyst", "s", "u")

    from sqlalchemy import select

    from tradeapp.journal.models import AICall

    with journal.session() as s:
        row = s.execute(select(AICall)).scalars().one()
    assert row.schema_ok is False and "HTTPStatusError" in row.error


def test_a_bad_shape_is_flagged_on_the_stored_call(journal: Journal):
    with respx.mock:
        respx.post(URL).mock(return_value=reply('{"regime":"x","bias":50}'))
        with pytest.raises(ValueError):
            client(journal).ask_json("analyst", "s", "u", AnalystView)

    from sqlalchemy import select

    from tradeapp.journal.models import AICall

    with journal.session() as s:
        row = s.execute(select(AICall)).scalars().one()
    assert row.schema_ok is False and "bias" in row.error


def test_a_good_reply_stores_the_parsed_view(journal: Journal):
    import json

    with respx.mock:
        respx.post(URL).mock(return_value=reply(json.dumps(GOOD)))
        view = client(journal).ask_json("analyst", "s", "u", AnalystView)
    assert view.regime == "risk-off"

    from sqlalchemy import select

    from tradeapp.journal.models import AICall

    with journal.session() as s:
        row = s.execute(select(AICall)).scalars().one()
    assert row.schema_ok is True and row.parsed["bias"] == -0.3


# --- the prompt must not carry account data --------------------------------------------------


def test_the_prompt_contains_market_context(tmp_path):
    cal = CalendarStore(tmp_path / "cal.db")
    cal.upsert([CalendarEvent(NOW + timedelta(hours=3), "USD", "Non-Farm Payrolls", Impact.HIGH)])
    prompt = build_prompt(ctx_for(), cal, NOW)
    assert "EURUSD" in prompt and "EMA20" in prompt and "ATR14" in prompt
    assert "Non-Farm Payrolls" in prompt


def test_the_prompt_never_carries_account_data(tmp_path):
    """A model has no use for the balance and there is every reason not to send it anywhere."""
    prompt = build_prompt(ctx_for(), None, NOW).lower()
    for forbidden in ("balance", "equity", "position", "ticket", "lot", "account", "login"):
        assert forbidden not in prompt, f"{forbidden!r} must not be in the prompt"


def test_the_prompt_says_when_nothing_is_scheduled(tmp_path):
    cal = CalendarStore(tmp_path / "cal.db")
    assert "No medium or high impact releases" in build_prompt(ctx_for(), cal, NOW)


# --- the analyst: every failure keeps the previous view -----------------------------------------


def analyst(journal: Journal, key="sk-test", **kw) -> Analyst:
    return Analyst(client(journal, key=key, **kw), journal, now=lambda: NOW)


def test_a_good_reply_becomes_the_view(journal: Journal):
    import json

    a = analyst(journal)
    with respx.mock:
        respx.post(URL).mock(return_value=reply(json.dumps(GOOD)))
        result = a.refresh(ctx_for())

    assert result.used_model is True
    assert a.view.bias == -0.3 and a.view.size_mult == 0.8 and a.view.regime == "risk-off"


def test_with_no_key_the_view_stays_neutral_and_nothing_breaks(journal: Journal):
    a = analyst(journal, key=None)
    result = a.refresh(ctx_for())
    assert result.used_model is False and "no API key" in result.detail
    assert a.view.bias == 0.0 and a.view.block is False


def test_a_network_failure_keeps_the_previous_view(journal: Journal):
    import json

    a = analyst(journal)
    with respx.mock:
        respx.post(URL).mock(return_value=reply(json.dumps(GOOD)))
        a.refresh(ctx_for())
    assert a.view.bias == -0.3

    with respx.mock:
        respx.post(URL).mock(side_effect=httpx.ConnectError("no route"))
        result = a.refresh(ctx_for())

    assert result.used_model is False
    assert a.view.bias == -0.3  # the old view stands rather than going blank mid-session
    assert any("keeping the previous view" in e.message for e in journal.events_where(source="ai"))


def test_a_schema_failure_keeps_the_previous_view(journal: Journal):
    a = analyst(journal)
    with respx.mock:
        respx.post(URL).mock(return_value=reply('{"regime":"x","bias":42}'))
        result = a.refresh(ctx_for())
    assert result.used_model is False
    assert a.view.bias == 0.0 and a.view.block is False


def test_an_old_view_expires_into_neutral(journal: Journal):
    """A dead analyst must not keep vetoing trades with a two hour old opinion."""
    import json

    stale = {**GOOD, "block": True, "valid_minutes": 30}
    a = Analyst(client(journal), journal, now=lambda: NOW)
    with respx.mock:
        respx.post(URL).mock(return_value=reply(json.dumps(stale)))
        a.refresh(ctx_for())
    assert a.view.block is True

    a._now = lambda: NOW + timedelta(hours=2)
    assert a.view.block is False and a.view.bias == 0.0


def test_the_budget_running_out_keeps_the_last_view(journal: Journal):
    import json

    a = analyst(journal, daily_budget_usd=0.0000001, pricing=Pricing(input_per_m=1.0, output_per_m=1.0))
    with respx.mock:
        respx.post(URL).mock(return_value=reply(json.dumps(GOOD), tokens_in=1_000_000))
        a.refresh(ctx_for())
    assert a.view.bias == -0.3

    result = a.refresh(ctx_for())
    assert result.used_model is False and "budget" in result.detail


def test_removing_the_key_leaves_a_working_system(journal: Journal):
    """P3-05: pull the key and everything still trades, on rules alone."""
    from tests.test_risk_engine import ctx as risk_ctx
    from tests.test_risk_engine import long_intent
    from tradeapp.risk import RiskEngine

    a = analyst(journal, key=None)
    a.refresh(ctx_for())
    decision = RiskEngine(journal=journal).evaluate(long_intent(), "trend_h4", risk_ctx(ai=a.view))
    assert decision.approved and decision.size_lots == 0.12
