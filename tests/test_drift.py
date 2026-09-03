"""Stored backtests, and the weekly drift report that compares live against one (P4-04).

The report exists to answer one question: is the live system doing what the backtest said it would?
Everything about it is built to stop that question being answered too early. A strategy that wins
30% of the time will show a 0% win rate for its first five trades, and a report that called that
drift would get a working strategy changed for no reason (D11).
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.test_reports import a_trade
from tradeapp.journal import Journal
from tradeapp.reports import DRIFT_MIN_TRADES, build_drift, render_drift, write_drift

NOON = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
POINT = 0.00001


def stored_run(
    journal: Journal,
    *,
    strategy: str = "trend_h4",
    trades: list[dict] | None = None,
    label: str | None = None,
) -> int:
    """A backtest run as the journal holds it, without spending a minute replaying history."""
    if trades is None:
        trades = []
        for i in range(50):
            win = i % 3 == 0  # 1 in 3, like the real ema_cross
            opened = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i)
            trades.append(
                {
                    "opened_utc": opened.isoformat(),
                    "closed_utc": (opened + timedelta(hours=2)).isoformat(),
                    "side": "LONG",
                    "volume": 0.1,
                    "entry": 1.1000,
                    "exit": 1.1060 if win else 1.0980,
                    "net": 60.0 if win else -20.0,
                    "exit_reason": "target" if win else "stop",
                }
            )
    return journal.backtest(
        label=label,
        strategy=strategy,
        params={"tf": "H4"},
        symbol="EURUSD",
        timeframe="H4",
        data_from=datetime(2026, 1, 1),
        data_to=datetime(2026, 3, 1),
        bars=1000,
        start_balance=10_000.0,
        end_balance=10_400.0,
        costs={"slippage_points": 0.3},
        stats={"trades": len(trades), "net": 400.0},
        trades=trades,
    )


# --- storage -----------------------------------------------------------------------------


def test_a_run_survives_a_round_trip(journal: Journal):
    run_id = stored_run(journal, label="baseline")
    run = journal.backtest_run(run_id)
    assert run.strategy == "trend_h4" and run.label == "baseline"
    assert run.stats["trades"] == 50 and len(run.trades) == 50
    assert run.costs["slippage_points"] == 0.3


def test_the_most_recent_run_for_a_strategy_is_findable(journal: Journal):
    stored_run(journal, strategy="trend_h4", label="old")
    newest = stored_run(journal, strategy="trend_h4", label="new")
    stored_run(journal, strategy="meanrev_m15", label="other")

    assert journal.latest_backtest("trend_h4").id == newest
    assert journal.latest_backtest("trend_h4").label == "new"
    assert journal.latest_backtest("nothing_here") is None


def test_runs_are_listed_newest_first(journal: Journal):
    first = stored_run(journal)
    second = stored_run(journal)
    assert [r.id for r in journal.backtest_runs(limit=10)] == [second, first]


# --- the comparison ------------------------------------------------------------------------


def many_live_trades(journal: Journal, n: int, *, wins: int, strategy: str = "trend_h4", slippage: float = 0.0) -> None:
    """n closed live trades, `wins` of them profitable, spread over the last three weeks."""
    for i in range(n):
        won = i < wins
        a_trade(
            journal,
            f"{strategy}-{i}",
            comment=strategy,
            entry=1.1000,
            exit_=1.1060 if won else 1.0980,
            slippage=slippage,
            at=NOON - timedelta(days=i % 20),
        )


def test_a_handful_of_live_trades_refuses_to_conclude_anything(journal: Journal):
    run_id = stored_run(journal)
    many_live_trades(journal, 5, wins=0)

    report = build_drift(journal, run_id, days=30, point=POINT, now=NOON)

    assert report.live_trades == 5 and report.meaningful is False
    assert report.diverging == []  # a 0% win rate over five trades is not drift
    assert "not enough to conclude" in render_drift(report)


def test_no_live_trades_at_all_says_so(journal: Journal):
    run_id = stored_run(journal)
    report = build_drift(journal, run_id, days=30, point=POINT, now=NOON)
    assert report.live_trades == 0
    assert any("nothing to compare yet" in n for n in report.notes)


def test_a_live_win_rate_far_below_the_backtest_is_flagged(journal: Journal):
    """The backtest wins a third of the time; live wins a tenth over a real sample."""
    run_id = stored_run(journal)
    many_live_trades(journal, 30, wins=3)

    report = build_drift(journal, run_id, days=30, point=POINT, now=NOON)

    assert report.meaningful is True
    assert any(m.name.startswith("win rate") and m.worse for m in report.metrics)
    assert "⚠" in render_drift(report)


def test_live_matching_the_backtest_says_there_is_nothing_to_look_at(journal: Journal):
    run_id = stored_run(journal)
    many_live_trades(journal, 30, wins=10)  # the same one-in-three

    report = build_drift(journal, run_id, days=30, point=POINT, now=NOON)

    assert report.diverging == []
    assert any("tracking the backtest" in n for n in report.notes)


def test_another_strategys_trades_are_not_counted(journal: Journal):
    run_id = stored_run(journal, strategy="trend_h4")
    many_live_trades(journal, 25, wins=0, strategy="meanrev_m15")

    report = build_drift(journal, run_id, days=30, point=POINT, now=NOON)

    assert report.live_trades == 0


def test_a_variant_counts_as_the_same_strategy(journal: Journal):
    run_id = stored_run(journal, strategy="trend_h4")
    many_live_trades(journal, 25, wins=8, strategy="trend_h4:B")

    assert build_drift(journal, run_id, days=30, point=POINT, now=NOON).live_trades == 25


def test_slippage_is_called_out_because_it_is_actionable(journal: Journal):
    run_id = stored_run(journal)
    many_live_trades(journal, 25, wins=8, slippage=4.0)

    report = build_drift(journal, run_id, days=30, point=POINT, now=NOON)

    assert report.live_slippage == 4.0
    assert any("slippage" in n and "actionable" in n for n in report.notes)


def test_an_unknown_run_is_an_error_not_an_empty_report(journal: Journal):
    with pytest.raises(KeyError):
        build_drift(journal, 999, days=30, point=POINT, now=NOON)


def test_the_drift_report_proposes_nothing(journal: Journal):
    run_id = stored_run(journal)
    many_live_trades(journal, 30, wins=1)
    text = render_drift(build_drift(journal, run_id, days=30, point=POINT, now=NOON))
    assert "## Proposals" in text and "None, and deliberately so" in text


def test_the_drift_report_can_be_written_to_disk(journal: Journal, tmp_path):
    run_id = stored_run(journal)
    path = write_drift(build_drift(journal, run_id, days=30, point=POINT, now=NOON), tmp_path)
    assert path.name == "drift-trend_h4-2026-09-03.md"
    assert "# Drift" in path.read_text(encoding="utf-8")


def test_the_bar_for_a_meaningful_sample_is_documented():
    """If this number changes it is a decision, not a tweak."""
    assert DRIFT_MIN_TRADES == 20
