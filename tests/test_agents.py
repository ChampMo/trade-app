"""Scout and Reviewer: the two agents that cannot move money (P3-03, D24).

The Analyst has its own tests because it is the one whose output reaches a trade. These two are
defined by what they are *not* allowed to do, and that is what is pinned down here: the Scout
never reaches the calendar, and the Reviewer never reaches a parameter.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from tests.test_ai import GOOD, client, ctx_for, reply
from tradeapp.ai.analyst import Analyst, build_prompt
from tradeapp.ai.reviewer import Reviewer, render
from tradeapp.ai.reviewer import build_prompt as review_prompt
from tradeapp.ai.scout import STATE_KEY, Scout, briefing_lines, load_briefing
from tradeapp.calendar import CalendarStore
from tradeapp.journal import Journal

URL = "https://api.deepseek.test/chat/completions"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

BRIEFING = {
    "events": [
        {"title": "FOMC decision", "currency": "USD", "impact": "HIGH", "summary": "market expects a hold"},
        {"title": "euro area CPI", "currency": "EUR", "impact": "HIGH", "summary": "core is the number to watch"},
        {"title": "yen intervention talk", "currency": "JPY", "impact": "MEDIUM", "summary": "not our pair"},
    ]
}

REVIEW = {
    "summary": "Four trades, three stops. Nothing in the execution looks wrong.",
    "findings": [{"decision_id": None, "classification": "variance", "note": "an ordinary run of losses"}],
    "bias_was_right": None,
}


def scout(journal: Journal, **kw) -> Scout:
    return Scout(client(journal, **kw), journal, symbol="EURUSD", now=lambda: NOW)


def reviewer(journal: Journal, **kw) -> Reviewer:
    return Reviewer(client(journal, **kw), journal, now=lambda: NOW)


# --- the scout ------------------------------------------------------------------------------


@respx.mock
def test_a_briefing_is_stored_and_read_back(journal: Journal):
    import json

    respx.post(URL).mock(return_value=reply(json.dumps(BRIEFING)))
    briefing, detail = scout(journal).refresh(days=7)

    assert briefing is not None and len(briefing.events) == 2  # JPY is not in EURUSD
    assert "1 dropped as out of scope" in detail
    assert load_briefing(journal).events == briefing.events


@respx.mock
def test_only_the_traded_pairs_currencies_survive(journal: Journal):
    import json

    respx.post(URL).mock(return_value=reply(json.dumps(BRIEFING)))
    briefing, _ = scout(journal).refresh()
    assert {e["currency"] for e in briefing.events} == {"USD", "EUR"}


@respx.mock
def test_the_scout_never_writes_to_the_calendar(tmp_path, journal: Journal):
    """D24. A model that invents a release time would block the wrong hour and trade the right one."""
    import json

    calendar = CalendarStore(tmp_path / "calendar.db")
    respx.post(URL).mock(return_value=reply(json.dumps(BRIEFING)))

    scout(journal).refresh()

    assert calendar.count() == 0


@respx.mock
def test_a_bad_reply_keeps_the_previous_briefing(journal: Journal):
    import json

    respx.post(URL).mock(return_value=reply(json.dumps(BRIEFING)))
    first, _ = scout(journal).refresh()

    respx.post(URL).mock(return_value=reply("I cannot help with that."))
    second, detail = scout(journal).refresh()

    assert second.events == first.events
    assert "did not match" in detail or "JSON" in detail


def test_no_api_key_is_a_normal_state(journal: Journal):
    briefing, detail = scout(journal, key=None).refresh()
    assert briefing is None and detail == "no API key"


def test_an_old_briefing_is_not_shown_as_current(journal: Journal):
    journal.set_state(
        STATE_KEY,
        {"written_utc": (NOW - timedelta(hours=48)).isoformat(), "events": BRIEFING["events"]},
    )
    assert briefing_lines(journal, NOW) == []
    assert briefing_lines(journal, NOW - timedelta(hours=24)) != []


def test_a_corrupt_briefing_is_ignored_rather_than_raising(journal: Journal):
    journal.set_state(STATE_KEY, {"nonsense": True})
    assert load_briefing(journal) is None and briefing_lines(journal, NOW) == []


# --- the briefing reaches the analyst, labelled ------------------------------------------------


def test_the_analyst_prompt_labels_the_briefing_as_unverified(journal: Journal):
    lines = ["  USD  HIGH  FOMC decision"]
    prompt = build_prompt(ctx_for(), None, NOW, lines)
    assert "Scout briefing" in prompt and "authoritative" in prompt
    assert "FOMC decision" in prompt


def test_the_analyst_prompt_still_carries_no_account_data(journal: Journal):
    """The scout changed the prompt, so the rule that matters most gets re-checked here (D6)."""
    prompt = build_prompt(ctx_for(), None, NOW, ["  USD  HIGH  FOMC decision"])
    for forbidden in ("balance", "equity", "login", "ticket", "position"):
        assert forbidden not in prompt.lower()


@respx.mock
def test_the_analyst_reads_a_fresh_briefing_and_ignores_a_stale_one(journal: Journal):
    import json

    seen = []

    def capture(request):
        seen.append(json.loads(request.content)["messages"][-1]["content"])
        return reply(json.dumps(GOOD))

    respx.post(URL).mock(side_effect=capture)
    analyst = Analyst(client(journal), journal, now=lambda: NOW)

    journal.set_state(STATE_KEY, {"written_utc": NOW.isoformat(), "events": BRIEFING["events"][:1]})
    analyst.refresh(ctx_for())
    assert "FOMC decision" in seen[-1]

    journal.set_state(
        STATE_KEY,
        {"written_utc": (NOW - timedelta(days=5)).isoformat(), "events": BRIEFING["events"][:1]},
    )
    analyst.refresh(ctx_for())
    assert "FOMC decision" not in seen[-1]


# --- the reviewer ---------------------------------------------------------------------------


def a_day(journal: Journal):
    from tests.test_reports import a_trade
    from tradeapp.reports import build

    a_trade(journal, "r1", entry=1.1000, exit_=1.0980)
    a_trade(journal, "r2", entry=1.1000, exit_=1.0980, slippage=4.0)
    return build(journal, "2026-09-03", now=NOW)


@respx.mock
def test_the_reviewer_comments_and_the_comment_is_journaled(journal: Journal):
    import json

    respx.post(URL).mock(return_value=reply(json.dumps(REVIEW)))
    review, detail = reviewer(journal).review(a_day(journal))

    assert review is not None and review.findings[0].classification == "variance"
    assert detail == "1 finding(s)"
    assert any("reviewer on 2026-09-03" in e.message for e in journal.tail_events(20))


def test_the_reviewer_prompt_carries_facts_and_no_money_in_the_account(journal: Journal):
    prompt = review_prompt(a_day(journal))
    assert "pips" in prompt and "slippage" in prompt
    assert "What the deterministic classifier already decided" in prompt
    for forbidden in ("equity", "balance", "login", "ticket"):
        assert forbidden not in prompt.lower()


def test_the_reviewer_is_told_the_deterministic_answer_is_not_up_for_debate(journal: Journal):
    assert "not up for debate" in review_prompt(a_day(journal))


@respx.mock
def test_the_rendered_section_says_what_the_opinion_is_worth(journal: Journal):
    import json

    respx.post(URL).mock(return_value=reply(json.dumps(REVIEW)))
    review, _ = reviewer(journal).review(a_day(journal))
    text = render(review)

    assert "## What the reviewer said" in text
    assert "carrying no authority" in text and "D11" in text


@respx.mock
def test_a_reviewer_that_fails_is_not_an_error_worth_stopping_for(journal: Journal):
    respx.post(URL).mock(return_value=httpx.Response(500, json={"error": "upstream"}))
    review, detail = reviewer(journal).review(a_day(journal))
    assert review is None and detail


def test_the_reviewer_without_a_key_says_so_plainly(journal: Journal):
    review, detail = reviewer(journal, key=None).review(a_day(journal))
    assert review is None and detail == "no API key"


@pytest.mark.parametrize("forbidden", ["risk_pct", "increase the", "change the parameter"])
def test_the_reviewers_instructions_forbid_proposing_changes(forbidden):
    """The prompt is the only guard here, so its wording is worth a test of its own (D11)."""
    from tradeapp.ai.reviewer import SYSTEM

    assert "Never propose a parameter change" in SYSTEM
    assert forbidden not in SYSTEM.replace("Never propose a parameter change", "")
