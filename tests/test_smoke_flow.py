from tradeapp.broker.fake import FakeBehavior, FakeBroker
from tradeapp.contracts import AccountMode, Side
from tradeapp.journal import Journal
from tradeapp.smoke import run_smoke

NO_SLEEP = lambda s: None  # noqa: E731


def test_happy_path_journals_every_step(journal: Journal, fake_broker: FakeBroker):
    report = run_smoke(fake_broker, journal, hold_seconds=0, sleep=NO_SLEEP)
    assert report.ok, report.steps
    assert report.sl_verified is True
    assert fake_broker.connected is False  # disconnected in finally
    assert fake_broker.open_tickets == []

    orders = journal.orders_for(report.client_ref)
    assert [o.kind for o in orders] == ["open", "close"]
    assert orders[0].sl_verified is True and orders[0].sl and orders[0].sl > 0
    messages = [e.message for e in journal.events_where(source="smoke")]
    assert messages[0] == "smoke start" and messages[-1] == "smoke done"
    # the stop check itself now lives in the execution layer, so it is the same code live trading runs
    assert "SL verified at broker" in [e.message for e in journal.events_where(source="exec")]


def test_dropped_sl_is_set_after_fill(journal: Journal):
    b = FakeBroker(behavior=FakeBehavior(drop_sl_on_fill=True))
    report = run_smoke(b, journal, hold_seconds=0, sleep=NO_SLEEP)
    assert report.ok and report.sl_verified is True
    kinds = [o.kind for o in journal.orders_for(report.client_ref)]
    assert kinds == ["open", "modify", "close"]
    assert any(e.severity == "WARN" for e in journal.events_where(source="exec"))


def test_unsettable_sl_closes_position_immediately(journal: Journal):
    b = FakeBroker(behavior=FakeBehavior(drop_sl_on_fill=True, fail_modify=True))
    report = run_smoke(b, journal, hold_seconds=0, sleep=NO_SLEEP)
    assert report.ok is False and report.sl_verified is False
    assert b.open_tickets == [] and len(b.closed) == 1  # closed by rule 03, not by the hold/close step
    crit = [e for e in journal.events_where(severity="CRIT", source="exec")]
    assert any("closing position now" in e.message for e in crit)


def test_rejected_order_is_journaled(journal: Journal):
    b = FakeBroker(behavior=FakeBehavior(reject_orders=True))
    report = run_smoke(b, journal, hold_seconds=0, sleep=NO_SLEEP)
    assert report.ok is False and "REJECT" in (report.error or "")
    orders = journal.orders_for(report.client_ref)
    assert len(orders) == 1 and orders[0].ok is False and orders[0].retcode_desc == "REJECT"


def test_refuses_non_demo_account(journal: Journal):
    b = FakeBroker(behavior=FakeBehavior(mode=AccountMode.REAL), allow_live=True)  # even when the guard is off
    report = run_smoke(b, journal, hold_seconds=0, sleep=NO_SLEEP)
    assert report.ok is False and "DEMO only" in (report.error or "")
    assert b.sent == []


def test_refuses_when_algo_trading_off(journal: Journal):
    b = FakeBroker(behavior=FakeBehavior(algo_trading=False))
    report = run_smoke(b, journal, hold_seconds=0, sleep=NO_SLEEP)
    assert report.ok is False and "Algo Trading" in (report.error or "")


def test_slippage_is_recorded_with_sign(journal: Journal):
    b = FakeBroker(behavior=FakeBehavior(slippage_points=3))
    report = run_smoke(b, journal, hold_seconds=0, sleep=NO_SLEEP, side=Side.SHORT)
    assert report.ok
    assert report.open_slippage_points == 3.0 and report.close_slippage_points == 3.0
