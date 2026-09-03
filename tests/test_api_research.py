"""The Research and Risk endpoints (P2-07).

Two things are being pinned down. The first is that research can be driven from the UI at all:
runs are listed, one is readable, a backtest can be launched and polled, and a launch never blocks
the request or the trading loop. The second matters more: **the limits are readable and not
writable**. A limit is a decision recorded in DECISIONS.md, and an endpoint that could change one
would turn a deliberate act into a slider you nudge after a bad afternoon.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.test_api import build_service
from tests.test_drift import many_live_trades, stored_run
from tradeapp.api import create_app
from tradeapp.journal import Journal
from tradeapp.research import BacktestRunner
from tradeapp.service import wait_until


@pytest.fixture
def client(journal: Journal):
    service = build_service(journal)
    service.start()
    wait_until(lambda: service.state.ticks > 0)
    runner = BacktestRunner(journal_path=":memory:", history_db="data/history.db")
    with TestClient(create_app(service, journal, runner)) as c:
        c.service, c.runner = service, runner
        yield c
    service.stop()


# --- risk limits are read-only ---------------------------------------------------------------


def test_the_limits_are_readable_with_the_reason_each_one_exists(client):
    body = client.get("/risk/limits").json()
    names = {row["name"] for row in body["limits"]}
    assert {"risk_pct", "max_drawdown_pct", "max_correlated_units", "trading_hours_utc"} <= names
    assert all(row["why"] for row in body["limits"])
    assert body["state"] == "RUNNING"


def test_the_limits_say_plainly_that_they_cannot_be_edited(client):
    body = client.get("/risk/limits").json()
    assert body["editable"] is False
    assert "DECISIONS" in body["why_not"]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_there_is_no_way_to_write_a_limit(client, method):
    """Not "the UI does not offer it" - there is no route at all."""
    assert client.request(method, "/risk/limits").status_code == 405


# --- stored runs -----------------------------------------------------------------------------


def test_runs_are_listed_without_shipping_every_trade(client, journal: Journal):
    stored_run(journal, label="baseline")
    rows = client.get("/backtest/runs").json()
    assert rows[0]["label"] == "baseline"
    assert rows[0]["trade_count"] == 50
    assert "trades" not in rows[0]  # a list of runs is not a list of hundreds of trades


def test_one_run_carries_its_trades(client, journal: Journal):
    run_id = stored_run(journal)
    body = client.get(f"/backtest/runs/{run_id}").json()
    assert len(body["trades"]) == 50 and body["stats"]["trades"] == 50


def test_an_unknown_run_is_a_404(client):
    assert client.get("/backtest/runs/999").status_code == 404


def test_runs_can_be_filtered_by_strategy(client, journal: Journal):
    stored_run(journal, strategy="trend_h4")
    stored_run(journal, strategy="meanrev_m15")
    rows = client.get("/backtest/runs?strategy=meanrev_m15").json()
    assert [r["strategy"] for r in rows] == ["meanrev_m15"]


# --- drift through the API ---------------------------------------------------------------------


def test_drift_comes_back_as_numbers_and_as_the_report(client, journal: Journal):
    run_id = stored_run(journal)
    body = client.get(f"/backtest/runs/{run_id}/drift?days=30").json()
    assert body["strategy"] == "trend_h4" and body["backtest_trades"] == 50
    assert any(m["name"].startswith("win rate") for m in body["metrics"])
    assert "# Drift" in body["markdown"]


def test_drift_over_a_small_live_sample_says_it_means_nothing(client, journal: Journal):
    run_id = stored_run(journal)
    many_live_trades(journal, 5, wins=0)
    body = client.get(f"/backtest/runs/{run_id}/drift?days=3650").json()
    assert body["meaningful"] is False and body["diverging"] == []


def test_drift_for_an_unknown_run_is_a_404(client):
    assert client.get("/backtest/runs/999/drift").status_code == 404


# --- launching a backtest ------------------------------------------------------------------


class SlowRunner(BacktestRunner):
    """Stands in for a real replay: the point is the job machinery, not the arithmetic."""

    def __init__(self, seconds: float = 0.2, fail: bool = False):
        super().__init__(journal_path=":memory:")
        self.seconds, self.fail = seconds, fail
        self.calls: list[dict] = []

    def _execute(self, params):
        import time

        self.calls.append(params)
        time.sleep(self.seconds)
        if self.fail:
            raise ValueError("only 3 bars stored for EURUSD H4; sync history first")
        return 7, "EURUSD H4  1000 bars  12 trades  net +42.00"


@pytest.fixture
def job_client(journal: Journal):
    service = build_service(journal)
    service.start()
    runner = SlowRunner()
    with TestClient(create_app(service, journal, runner)) as c:
        c.runner = runner
        yield c
    service.stop()


def test_a_backtest_is_launched_and_polled_rather_than_awaited(job_client):
    """The request answers at once; a replay can take minutes and must not hold the connection."""
    job = job_client.post("/backtest", json={"strategy": "ema_cross", "label": "from a test"}).json()
    assert job["status"] in {"queued", "running"} and job["run_id"] is None

    wait_until(lambda: job_client.get(f"/backtest/jobs/{job['id']}").json()["status"] == "done", timeout=5)
    done = job_client.get(f"/backtest/jobs/{job['id']}").json()
    assert done["status"] == "done" and done["run_id"] == 7
    assert "net +42.00" in done["summary"]
    assert job_client.runner.calls[0]["label"] == "from a test"


def test_only_one_backtest_runs_at_a_time(job_client):
    """A CPU-bound job shares the machine with the thing that is actually trading."""
    job_client.post("/backtest", json={})
    second = job_client.post("/backtest", json={})
    assert second.status_code == 409 and "already running" in second.json()["detail"]


def test_a_failed_backtest_is_a_status_not_a_crash(journal: Journal):
    service = build_service(journal)
    service.start()
    runner = SlowRunner(seconds=0.01, fail=True)
    with TestClient(create_app(service, journal, runner)) as c:
        job = c.post("/backtest", json={}).json()
        wait_until(lambda: c.get(f"/backtest/jobs/{job['id']}").json()["status"] == "failed", timeout=5)
        body = c.get(f"/backtest/jobs/{job['id']}").json()
        assert body["status"] == "failed" and "sync history first" in body["error"]
        assert c.get("/status").json()["service"]["running"] is True  # the loop never noticed
    service.stop()


def test_jobs_are_listed_newest_first(job_client):
    job_client.post("/backtest", json={})
    wait_until(lambda: job_client.get("/backtest/jobs").json()["busy"] is False, timeout=5)
    job_client.post("/backtest", json={})
    body = job_client.get("/backtest/jobs").json()
    assert [j["id"] for j in body["jobs"]] == [2, 1]


def test_an_unknown_job_is_a_404(job_client):
    assert job_client.get("/backtest/jobs/999").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"monte_carlo": 999_999},  # a click must not be able to ask for an hour of shuffling
        {"balance": -1},
        {"warmup": 5},
        {"slippage_points": -2},
    ],
)
def test_the_request_is_bounded(job_client, payload):
    assert job_client.post("/backtest", json=payload).status_code == 422


# --- timestamps on the wire -------------------------------------------------------------------


def test_every_timestamp_says_it_is_utc(client, journal: Journal):
    """A bare `2026-09-03T03:48:10` is read as *local* time by every browser.

    The journal is naive UTC by decision (D13). Without the offset the UI silently shifted every
    journal timestamp by the machine's zone — seven hours here, under a column headed UTC.
    """
    from datetime import UTC, datetime

    stored_run(journal)
    journal.event("INFO", "test", "hello")
    journal.order(client_ref="r1", kind="open", symbol="EURUSD", ok=True, retcode=10009, retcode_desc="DONE")
    journal.decision(strategy_id="trend_h4", symbol="EURUSD", verdict="APPROVED")

    stamps = [
        client.get("/events").json()[-1]["ts_utc"],
        client.get("/orders").json()[0]["ts_utc"],
        client.get("/decisions").json()[0]["ts_utc"],
        client.get("/backtest/runs").json()[0]["ts_utc"],
    ]
    for stamp in stamps:
        assert stamp.endswith("+00:00"), stamp
        assert datetime.fromisoformat(stamp).tzinfo is not None

    # And the reading itself is untouched: the offset is added, the clock is not shifted.
    stored = journal.tail_events(1)[-1].ts_utc
    assert datetime.fromisoformat(stamps[0]) == stored.replace(tzinfo=UTC)


# --- what the form is allowed to offer -------------------------------------------------------


def test_the_form_is_built_from_what_can_actually_be_replayed(client, tmp_path):
    """A symbol with no stored bars is not a typo to correct after a failed run (D25)."""
    from tradeapp.contracts import TF, Bar
    from tradeapp.data import BarStore

    store = BarStore(tmp_path / "history.db")
    store.upsert(
        "EURUSD",
        TF.H4,
        [
            Bar(time_utc=datetime(2026, 1, 1, h, tzinfo=UTC), open=1.1, high=1.2, low=1.0, close=1.15)
            for h in range(0, 20, 4)
        ],
    )
    client.runner.history_db = str(tmp_path / "history.db")

    body = client.get("/backtest/options").json()

    assert "ema_cross" in body["strategies"]
    assert body["data"] == [
        {
            "symbol": "EURUSD",
            "timeframe": "H4",
            "bars": 5,
            "from": "2026-01-01T00:00:00+00:00",
            "to": "2026-01-01T16:00:00+00:00",
        }
    ]


def test_an_empty_history_offers_no_data_rather_than_a_guess(client, tmp_path):
    client.runner.history_db = str(tmp_path / "empty.db")
    body = client.get("/backtest/options").json()
    assert body["data"] == []
    assert body["strategies"]  # the strategies are still there; the bars are not


# --- bars for the chart ------------------------------------------------------------------------


def test_bars_come_back_as_a_window_not_the_whole_store(client, tmp_path):
    from tradeapp.contracts import TF, Bar
    from tradeapp.data import BarStore

    store = BarStore(tmp_path / "history.db")
    store.upsert(
        "EURUSD",
        TF.H4,
        [
            Bar(
                time_utc=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=4 * i),
                open=1.1,
                high=1.2,
                low=1.0,
                close=1.15,
            )
            for i in range(50)
        ],
    )
    client.runner.history_db = str(tmp_path / "history.db")

    body = client.get("/bars?symbol=EURUSD&timeframe=H4&limit=5").json()
    assert len(body["bars"]) == 5
    assert set(body["bars"][0]) == {"t", "o", "h", "l", "c"}
    assert body["bars"][0]["t"].endswith("+00:00")

    windowed = client.get("/bars?symbol=EURUSD&timeframe=H4&start=2026-01-02T00:00:00Z&end=2026-01-02T12:00:00Z").json()
    assert [b["t"][:16] for b in windowed["bars"]] == [
        "2026-01-02T00:00",
        "2026-01-02T04:00",
        "2026-01-02T08:00",
        "2026-01-02T12:00",
    ]


def test_an_unreadable_time_is_a_400_not_a_stack_trace(client):
    assert client.get("/bars?symbol=EURUSD&timeframe=H4&start=yesterday").status_code == 400


def test_an_unknown_timeframe_is_refused(client):
    assert client.get("/bars?symbol=EURUSD&timeframe=H7").status_code == 400


def test_a_symbol_with_no_bars_is_an_empty_list_not_an_error(client, tmp_path):
    client.runner.history_db = str(tmp_path / "history.db")
    assert client.get("/bars?symbol=GBPUSD&timeframe=H4").json()["bars"] == []
