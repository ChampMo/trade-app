"""The post-mortem. Its job is to explain, and specifically not to propose (D11)."""

from datetime import UTC, datetime, timedelta

from tradeapp.journal import Journal
from tradeapp.reports import DayReport, ab_table, build, classify, collect_trades, render, write

DAY = "2026-09-03"
NOON = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def a_trade(
    journal: Journal,
    ref: str,
    *,
    entry: float = 1.1000,
    exit_: float | None = 1.0980,
    slippage: float = 0.0,
    close_slippage: float = 0.0,
    sl_verified: bool = True,
    retries: int = 0,
    magic: int = 100_001,
    comment: str = "trend_h4",
    at: datetime = NOON,
) -> None:
    for _ in range(retries):
        journal.order(
            ts_utc=at.replace(tzinfo=None),
            client_ref=ref,
            kind="open",
            symbol="EURUSD",
            side="LONG",
            ok=False,
            retcode=10004,
            retcode_desc="REQUOTE",
            magic=magic,
            comment=comment,
        )
    journal.order(
        ts_utc=at.replace(tzinfo=None),
        client_ref=ref,
        kind="open",
        symbol="EURUSD",
        side="LONG",
        volume=0.1,
        price_filled=entry,
        ok=True,
        retcode=10009,
        retcode_desc="DONE",
        magic=magic,
        comment=comment,
        slippage_points=slippage,
        sl_verified=sl_verified,
        position_ticket=hash(ref) % 10000,
    )
    if exit_ is not None:
        journal.order(
            ts_utc=(at + timedelta(hours=2)).replace(tzinfo=None),
            client_ref=ref,
            kind="close",
            symbol="EURUSD",
            side="LONG",
            volume=0.1,
            price_filled=exit_,
            ok=True,
            retcode=10009,
            retcode_desc="DONE",
            magic=magic,
            slippage_points=close_slippage,
        )


# --- stitching orders back into trades -------------------------------------------------


def test_open_and_close_become_one_trade(journal: Journal):
    a_trade(journal, "r1")
    trades = collect_trades(journal, NOON - timedelta(hours=1), NOON + timedelta(hours=6))
    assert len(trades) == 1
    t = trades[0]
    assert t.entry == 1.1000 and t.exit == 1.0980 and t.closed is True
    assert t.points < 0  # a long that closed lower


def test_a_still_open_trade_is_included_but_not_closed(journal: Journal):
    a_trade(journal, "r1", exit_=None)
    t = collect_trades(journal, NOON - timedelta(hours=1), NOON + timedelta(hours=6))[0]
    assert t.closed is False and t.points is None


def test_a_rejected_open_is_not_a_trade(journal: Journal):
    journal.order(
        ts_utc=NOON.replace(tzinfo=None),
        client_ref="r1",
        kind="open",
        symbol="EURUSD",
        ok=False,
        retcode=10006,
        retcode_desc="REJECT",
    )
    assert collect_trades(journal, NOON - timedelta(hours=1), NOON + timedelta(hours=6)) == []


def test_retries_are_counted(journal: Journal):
    a_trade(journal, "r1", retries=2)
    assert collect_trades(journal, NOON - timedelta(hours=1), NOON + timedelta(hours=6))[0].retries == 2


# --- classification: only two classes can lead anywhere ----------------------------------


def test_an_ordinary_loss_is_variance(journal: Journal):
    a_trade(journal, "r1")
    t = collect_trades(journal, NOON - timedelta(hours=1), NOON + timedelta(hours=6))[0]
    assert classify(t) == ("variance", "an ordinary result")


def test_bad_slippage_is_execution(journal: Journal):
    a_trade(journal, "r1", slippage=3.5)
    kind, why = classify(collect_trades(journal, NOON - timedelta(hours=1), NOON + timedelta(hours=6))[0])
    assert kind == "execution" and "3.5 points" in why


def test_a_retried_order_is_execution(journal: Journal):
    a_trade(journal, "r1", retries=1)
    kind, why = classify(collect_trades(journal, NOON - timedelta(hours=1), NOON + timedelta(hours=6))[0])
    assert kind == "execution" and "2 attempts" in why


def test_an_unverified_stop_is_a_bug(journal: Journal):
    """Rule 03 failing is not bad luck."""
    a_trade(journal, "r1", sl_verified=False)
    kind, why = classify(collect_trades(journal, NOON - timedelta(hours=1), NOON + timedelta(hours=6))[0])
    assert kind == "bug" and "stop" in why


def test_regime_is_never_assigned_automatically(journal: Journal):
    """A rule that guessed would relabel ordinary losing streaks as regime changes (D11)."""
    for i in range(10):
        a_trade(journal, f"r{i}")
    report = build(journal, DAY, now=NOON)
    assert "regime" not in report.classification
    assert "regime" in render(report)  # but the report explains why it is absent


# --- the report ----------------------------------------------------------------------------


def test_winners_are_not_classified(journal: Journal):
    a_trade(journal, "win", entry=1.1000, exit_=1.1050)
    report = build(journal, DAY, now=NOON)
    assert report.classification == {} or all(not v for v in report.classification.values())


def test_actionable_is_only_execution_and_bug(journal: Journal):
    a_trade(journal, "ordinary")
    a_trade(journal, "slipped", slippage=5.0)
    a_trade(journal, "broken", sl_verified=False)
    report = build(journal, DAY, now=NOON)

    assert len(report.actionable) == 2
    assert not any("ordinary" in item for item in report.actionable)


def test_per_strategy_numbers(journal: Journal):
    a_trade(journal, "a", comment="trend_h4", entry=1.10, exit_=1.11)
    a_trade(journal, "b", comment="trend_h4", entry=1.10, exit_=1.09)
    report = build(journal, DAY, now=NOON)
    stats = report.by_strategy["trend_h4"]
    assert stats["trades"] == 2 and stats["wins"] == 1 and stats["win_rate"] == 50.0


def test_rejections_are_counted(journal: Journal):
    journal.decision(
        ts_utc=NOON.replace(tzinfo=None),
        strategy_id="trend_h4",
        symbol="EURUSD",
        verdict="REJECTED",
        verdict_reason="outside_trading_hours: 22:00 UTC is outside 07:00-20:00",
    )
    journal.decision(
        ts_utc=NOON.replace(tzinfo=None),
        strategy_id="trend_h4",
        symbol="EURUSD",
        verdict="REJECTED",
        verdict_reason="news_block: USD NFP",
    )
    report = build(journal, DAY, now=NOON)
    assert report.rejections["outside_trading_hours"] == 1 and report.rejections["news_block"] == 1


def test_ai_spend_is_reported(journal: Journal):
    journal.ai_call(
        ts_utc=NOON.replace(tzinfo=None),
        agent="analyst",
        model="deepseek-chat",
        prompt="p",
        response="{}",
        cost_usd=0.0002,
        schema_ok=True,
    )
    journal.ai_call(
        ts_utc=NOON.replace(tzinfo=None),
        agent="analyst",
        model="deepseek-chat",
        prompt="p",
        response="nope",
        cost_usd=0.0001,
        schema_ok=False,
    )
    report = build(journal, DAY, now=NOON)
    assert report.ai_calls == 2 and report.ai_cost == 0.0003 and report.ai_schema_failures == 1


def test_a_quiet_day_says_so(journal: Journal):
    report = build(journal, DAY, now=NOON)
    assert "no trades today" in report.notes


def test_a_day_of_ordinary_losses_says_nothing_is_actionable(journal: Journal):
    for i in range(5):
        a_trade(journal, f"r{i}")
    report = build(journal, DAY, now=NOON)
    assert any("ordinary variance" in n for n in report.notes)


def test_the_report_proposes_nothing(journal: Journal):
    """The whole point of D11: the normal output is a report, never a parameter change."""
    a_trade(journal, "slipped", slippage=9.0)
    text = render(build(journal, DAY, now=NOON))
    assert "## Proposals" in text and "None." in text
    assert "fixed monthly cadence" in text


def test_the_report_is_written_to_disk(journal: Journal, tmp_path):
    a_trade(journal, "r1")
    path = write(build(journal, DAY, now=NOON), tmp_path)
    assert path.name == f"postmortem-{DAY}.md"
    assert "# Post-mortem" in path.read_text(encoding="utf-8")


def test_rendering_an_empty_report_still_works():
    text = render(DayReport(day=DAY, generated_utc=NOON))
    assert "# Post-mortem" in text and "## Proposals" in text


def test_only_the_requested_day_is_included(journal: Journal):
    a_trade(journal, "today", at=NOON)
    a_trade(journal, "yesterday", at=NOON - timedelta(days=1))
    report = build(journal, DAY, now=NOON)
    assert [t.client_ref for t in report.trades] == ["today"]


# --- A/B (P3-04) -------------------------------------------------------------------------------


def test_variants_are_compared_side_by_side(journal: Journal):
    """The question the AI layer has to justify itself against (D9)."""
    journal.decision(
        ts_utc=NOON.replace(tzinfo=None),
        strategy_id="trend_h4",
        variant="A",
        symbol="EURUSD",
        verdict="APPROVED",
        order_id=1,
    )
    a_trade(journal, "a1", magic=100_001, comment="trend_h4", entry=1.10, exit_=1.11)
    a_trade(journal, "b1", magic=100_002, comment="trend_h4", entry=1.10, exit_=1.09)

    table = ab_table(journal, days=7, now=NOON)
    assert len(table) >= 1
    assert all("trades" in row and "win_rate" in row and "magics" in row for row in table.values())


def test_ab_over_an_empty_journal(journal: Journal):
    assert ab_table(journal, days=7, now=NOON) == {}
