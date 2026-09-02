"""The local API: what the UI can read, what it can change, and what it must not be able to do."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tradeapp.api import create_app, serve
from tradeapp.broker.fake import FakeBroker
from tradeapp.contracts import TF, Bar, Intent, Side
from tradeapp.core import Core, CoreConfig
from tradeapp.journal import Journal
from tradeapp.runtime import StrategyRuntime
from tradeapp.service import CoreService, wait_until

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


class AlwaysLong:
    id, symbols, timeframe = "always_long", ["EURUSD"], TF.H4

    def on_bar(self, ctx):
        return Intent(
            symbol=ctx.symbol,
            side=Side.LONG,
            confidence=1.0,
            stop_price=round(ctx.close() - 0.0020, 5),
            take_price=round(ctx.close() + 0.0040, 5),
            reason="test signal",
        )


def bars_ending(at: datetime, n: int = 60) -> list[Bar]:
    return [
        Bar(
            time_utc=at - timedelta(hours=4 * (n - 1 - i)),
            open=1.1000 + i * 0.0001,
            high=1.1005 + i * 0.0001,
            low=1.0995 + i * 0.0001,
            close=1.1000 + i * 0.0001,
        )
        for i in range(n)
    ]


def build_service(journal: Journal) -> CoreService:
    broker = FakeBroker()
    bars = bars_ending(NOW - timedelta(hours=4))
    broker.seed_bars(bars)
    broker.bid = bars[-1].close
    runtime = StrategyRuntime(journal)
    runtime.register(AlwaysLong())
    core = Core(
        broker,
        journal,
        runtime=runtime,
        config=CoreConfig(tick_interval_s=0.01),
        now=lambda: NOW,
        sleep=lambda _s: None,
    )
    return CoreService(core, tick_interval_s=0.01)


@pytest.fixture
def client(journal: Journal):
    service = build_service(journal)
    service.start()
    wait_until(lambda: service.state.ticks > 0)
    with TestClient(create_app(service, journal)) as c:
        c.service = service
        yield c
    service.stop()


# --- reading ---------------------------------------------------------------------------


def test_status_describes_the_core_and_the_service(client):
    body = client.get("/status").json()
    assert body["state"] == "RUNNING"
    assert body["service"]["running"] is True and body["service"]["ticks"] >= 1
    assert body["peak_equity"] > 0
    assert body["open_positions"] >= 0


def test_positions_lists_what_the_broker_holds(client):
    wait_until(lambda: client.get("/positions").json())
    rows = client.get("/positions").json()
    assert rows and rows[0]["symbol"] == "EURUSD"
    assert rows[0]["sl"] > 0  # rule 03 all the way to the UI


def test_events_can_be_paged_forward(client):
    first = client.get("/events", params={"after_id": 0, "limit": 5}).json()
    assert first
    later = client.get("/events", params={"after_id": first[-1]["id"]}).json()
    assert all(e["id"] > first[-1]["id"] for e in later)


def test_decisions_carry_the_reason_and_the_ai_view(client):
    wait_until(lambda: client.get("/decisions").json())
    rows = client.get("/decisions").json()
    assert rows and rows[0]["verdict"] in {"APPROVED", "REJECTED"}
    assert rows[0]["reason"] == "test signal"
    assert "bias" in rows[0]["ai"]


def test_orders_show_the_fill_and_whether_the_stop_was_verified(client):
    wait_until(lambda: client.get("/orders").json())
    rows = client.get("/orders").json()
    assert rows and rows[0]["kind"] == "open"
    assert rows[0]["sl_verified"] is True


def test_strategies_include_their_lifecycle_state(client):
    rows = client.get("/strategies").json()
    assert rows[0]["key"] == "always_long"
    assert rows[0]["lifecycle"] == "research"
    assert rows[0]["timeframe"] == "H4"


def test_ticks_expose_recent_loop_activity(client):
    rows = client.get("/ticks").json()
    assert rows and "equity" in rows[0] and "state" in rows[0]


# --- control ----------------------------------------------------------------------------


def test_kill_flattens_and_locks(client):
    body = client.post("/control/kill", json={"reason": "testing the button"}).json()
    assert body["complete"] is True
    assert client.get("/status").json()["state"] == "KILLED"
    assert client.get("/positions").json() == []


def test_unlock_requires_a_reason(client):
    client.post("/control/kill", json={"reason": "setup"})
    refused = client.post("/control/unlock", json={"reason": "   "})
    assert refused.status_code == 400 and "reason" in refused.json()["detail"]
    assert client.get("/status").json()["state"] == "KILLED"


def test_the_full_cycle_through_http(client):
    client.post("/control/kill", json={"reason": "setup"})
    assert client.post("/control/unlock", json={"reason": "checked the journal"}).json()["state"] == "PAUSED"
    assert client.post("/control/resume").json()["state"] == "RUNNING"


def test_resuming_a_killed_engine_is_a_conflict_not_a_crash(client):
    client.post("/control/kill", json={"reason": "setup"})
    response = client.post("/control/resume")
    assert response.status_code == 409
    assert "only for a PAUSED engine" in response.json()["detail"]


def test_pause_and_resume(client):
    assert client.post("/control/pause", json={"reason": "lunch"}).json()["state"] == "PAUSED"
    assert client.post("/control/resume").json()["state"] == "RUNNING"


# --- live stream --------------------------------------------------------------------------


def test_the_websocket_streams_new_events_only(client):
    with client.websocket_connect("/ws/events") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        client.service.core.journal.event("WARN", "test", "something happened", {"x": 1})
        message = ws.receive_json()
        assert message["type"] == "event"
        assert message["message"] == "something happened"
        assert message["id"] > hello["after_id"]


# --- the assumption the whole design rests on ----------------------------------------------


def test_serving_on_a_public_interface_is_refused(journal: Journal):
    """No auth is fine on loopback and reckless anywhere else; a typo must not expose the kill switch."""
    service = build_service(journal)
    for host in ("0.0.0.0", "192.168.1.20", "::"):
        with pytest.raises(ValueError, match="refusing to bind"):
            serve(service, host=host)


def test_loopback_hosts_are_accepted():
    from tradeapp.api import _is_loopback

    assert _is_loopback("127.0.0.1") and _is_loopback("localhost") and _is_loopback("::1")
    assert not _is_loopback("0.0.0.0") and not _is_loopback("10.0.0.5")


# --- the loop must not die quietly ------------------------------------------------------------


def test_a_loop_that_raises_stops_loudly(journal: Journal):
    service = build_service(journal)

    def explode():
        raise ZeroDivisionError("something in the loop")

    service.core.tick = explode
    service.start()
    wait_until(lambda: service.state.running is False)

    assert service.state.running is False
    assert "ZeroDivisionError" in service.state.last_error
    crit = [e for e in journal.events_where(severity="CRIT", source="service")]
    assert crit and "stopped on an exception" in crit[0].message
    service.stop()


def test_status_reports_a_stopped_loop(journal: Journal):
    service = build_service(journal)
    service.core.tick = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    service.start()
    wait_until(lambda: service.state.running is False)
    with TestClient(create_app(service, journal)) as c:
        body = c.get("/status").json()
    assert body["service"]["running"] is False
    assert "boom" in body["service"]["last_error"]
    service.stop()
