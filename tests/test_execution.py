"""Execution layer: retries, slippage, rule 03 after the fill, and the counters the kill switch reads."""

from datetime import UTC, datetime

import pytest

from tradeapp.broker.fake import FakeBehavior, FakeBroker
from tradeapp.contracts import OrderRequest, OrderResult, Side
from tradeapp.execution import RETRYABLE, Executor, RetryPolicy, slippage_points
from tradeapp.journal import Journal

POINT = 0.00001
NO_SLEEP = lambda s: None  # noqa: E731


def order(side: Side = Side.LONG, volume: float = 0.1) -> OrderRequest:
    stop = 1.15700 if side is Side.LONG else 1.16100
    return OrderRequest(
        symbol="EURUSD", side=side, volume=volume, stop_price=stop, take_price=None, magic=100_001, comment="t"
    )


def broker(**behavior) -> FakeBroker:
    b = FakeBroker(behavior=FakeBehavior(**behavior))
    b.connect()
    return b


def executor(b: FakeBroker, journal: Journal, **kw) -> Executor:
    return Executor(b, journal, sleep=NO_SLEEP, **kw)


# --- retry ----------------------------------------------------------------------------


def test_a_requote_is_retried_and_then_fills(journal: Journal):
    b = broker(open_fail_times=1, open_fail_retcode=10004)  # REQUOTE
    res = executor(b, journal).send(order(), point=POINT)
    assert res.ok and res.attempts == 2
    assert any("retrying after REQUOTE" in e.message for e in journal.events_where(source="exec"))


def test_retries_are_exhausted_and_reported(journal: Journal):
    b = broker(open_fail_times=99, open_fail_retcode=10020)  # PRICE_CHANGED forever
    res = executor(b, journal).send(order(), point=POINT)
    assert res.ok is False and res.attempts == 3
    assert "PRICE_CHANGED after 3 attempt(s)" in res.detail
    assert b.open_tickets == []


def test_retry_count_is_configurable(journal: Journal):
    b = broker(open_fail_times=99)
    res = executor(b, journal, policy=RetryPolicy(max_attempts=5)).send(order(), point=POINT)
    assert res.attempts == 5


def test_a_timeout_is_never_retried():
    """10012 is ambiguous: the order may have filled. Retrying could open the position twice."""
    assert 10012 not in RETRYABLE


def test_a_timeout_stops_after_one_attempt(journal: Journal):
    b = broker(open_fail_times=99, open_fail_retcode=10012)  # TIMEOUT
    res = executor(b, journal).send(order(), point=POINT)
    assert res.attempts == 1 and res.ok is False
    assert "TIMEOUT" in res.detail


def test_a_plain_rejection_is_not_retried(journal: Journal):
    b = broker(reject_orders=True)
    res = executor(b, journal).send(order(), point=POINT)
    assert res.attempts == 1 and res.ok is False and "REJECT" in res.detail


def test_a_broker_that_raises_is_a_rejection_not_a_crash(journal: Journal):
    class Exploding(FakeBroker):
        def market_order(self, req):
            raise ConnectionError("terminal went away")

    b = Exploding()
    b.connect()
    ex = executor(b, journal)
    res = ex.send(order(), point=POINT)
    assert res.ok is False and "ConnectionError" in res.detail
    assert ex.consecutive_rejects == 1
    assert any("broker raised while sending" in e.message for e in journal.events_where(source="exec"))


# --- slippage -------------------------------------------------------------------------


def test_slippage_sign_is_from_the_traders_point_of_view():
    worse_long = OrderResult(ok=True, retcode=10009, retcode_desc="DONE", price_requested=1.16000, price_filled=1.16003)
    assert slippage_points(worse_long, Side.LONG, POINT) == 3.0  # paid more than asked
    better_long = OrderResult(
        ok=True, retcode=10009, retcode_desc="DONE", price_requested=1.16000, price_filled=1.15997
    )
    assert slippage_points(better_long, Side.LONG, POINT) == -3.0

    worse_short = OrderResult(
        ok=True, retcode=10009, retcode_desc="DONE", price_requested=1.16000, price_filled=1.15997
    )
    assert slippage_points(worse_short, Side.SHORT, POINT) == 3.0  # sold lower than asked


def test_slippage_on_a_close_flips_with_the_side():
    res = OrderResult(ok=True, retcode=10009, retcode_desc="DONE", price_requested=1.16000, price_filled=1.15997)
    assert slippage_points(res, Side.LONG, POINT, closing=True) == 3.0  # sold the long lower than asked


def test_slippage_is_none_without_prices():
    res = OrderResult(ok=False, retcode=10006, retcode_desc="REJECT")
    assert slippage_points(res, Side.LONG, POINT) is None


def test_slippage_reaches_the_journal(journal: Journal):
    b = broker(slippage_points=3)
    ex = executor(b, journal)
    opened = ex.send(order(), point=POINT)
    assert opened.slippage_points == 3.0
    row = journal.orders_for(opened.client_ref)[0]
    assert row.slippage_points == 3.0


# --- rule 03 after the fill -----------------------------------------------------------


def test_a_fill_without_a_stop_gets_one_set(journal: Journal):
    b = broker(drop_sl_on_fill=True)
    res = executor(b, journal).send(order(), point=POINT)
    assert res.ok and res.sl_verified is True
    assert [o.kind for o in journal.orders_for(res.client_ref)] == ["open", "modify"]
    assert b.position(res.position_ticket).sl > 0


def test_a_stop_that_cannot_be_set_closes_the_position(journal: Journal):
    b = broker(drop_sl_on_fill=True, fail_modify=True)
    res = executor(b, journal).send(order(), point=POINT)
    assert res.ok is False and res.sl_verified is False
    assert b.open_tickets == []  # closed immediately, not left running unprotected
    assert any("closing position now" in e.message for e in journal.events_where(severity="CRIT", source="exec"))


def test_the_open_row_records_whether_the_stop_was_verified(journal: Journal):
    b = broker()
    res = executor(b, journal).send(order(), point=POINT)
    assert journal.orders_for(res.client_ref)[0].sl_verified is True


def test_stop_verification_can_be_skipped_only_explicitly(journal: Journal):
    b = broker(drop_sl_on_fill=True)
    res = executor(b, journal).send(order(), point=POINT, verify_stop=False)
    assert res.ok and res.sl_verified is None
    assert b.position(res.position_ticket).sl == 0.0  # caller took responsibility


# --- the counters the kill switch reads -----------------------------------------------


def test_consecutive_rejects_counts_up_and_resets(journal: Journal):
    b = broker(reject_orders=True)
    ex = executor(b, journal)
    ex.send(order(), point=POINT)
    ex.send(order(), point=POINT)
    assert ex.consecutive_rejects == 2

    b.behavior = FakeBehavior()  # broker recovers
    ex.send(order(), point=POINT)
    assert ex.consecutive_rejects == 0


def test_three_rejects_reach_the_kill_switch_threshold(journal: Journal):
    """This is the wiring P1-05b needs: the executor produces what SystemHealth carries."""
    from tradeapp.risk.killswitch import KillLimits, KillSwitch, KillTrigger, SystemHealth

    b = broker(reject_orders=True)
    ex = executor(b, journal)
    for _ in range(3):
        ex.send(order(), point=POINT)

    health = SystemHealth(
        now_utc=datetime.now(UTC),
        equity=10_000.0,
        day_start_equity=10_000.0,
        peak_equity=10_000.0,
        last_broker_contact_utc=ex.last_broker_contact_utc,
        consecutive_rejects=ex.consecutive_rejects,
    )
    trigger, _ = KillSwitch(KillLimits()).evaluate(health)
    assert trigger is KillTrigger.CONSECUTIVE_REJECTS


def test_broker_contact_time_is_recorded(journal: Journal):
    stamps = [datetime(2026, 9, 3, 12, 0, tzinfo=UTC), datetime(2026, 9, 3, 12, 0, 1, tzinfo=UTC)]
    ex = Executor(broker(), journal, sleep=NO_SLEEP, now=lambda: stamps[min(len(stamps) - 1, 1)])
    assert ex.last_broker_contact_utc is None
    ex.send(order(), point=POINT)
    assert ex.last_broker_contact_utc is not None


# --- closing --------------------------------------------------------------------------


def test_close_records_slippage_and_a_journal_row(journal: Journal):
    b = broker(slippage_points=2)
    ex = executor(b, journal)
    opened = ex.send(order(), point=POINT)
    closed = ex.close(opened.position_ticket, client_ref=opened.client_ref, point=POINT, reason="test")
    assert closed.ok and closed.slippage_points == 2.0
    assert [o.kind for o in journal.orders_for(opened.client_ref)] == ["open", "close"]
    assert b.open_tickets == []


def test_a_failed_close_counts_as_a_rejection(journal: Journal):
    b = broker(fail_close_always=True)
    ex = executor(b, journal)
    opened = ex.send(order(), point=POINT)
    closed = ex.close(opened.position_ticket)
    assert closed.ok is False and ex.consecutive_rejects == 1
    assert b.open_tickets != []


# --- the journal links a decision to its order ----------------------------------------


def test_the_decision_row_points_at_the_order_it_produced(journal: Journal):
    from sqlalchemy import select

    from tradeapp.journal.models import Decision

    decision_id = journal.decision(strategy_id="trend_h4", symbol="EURUSD", verdict="APPROVED", side="LONG")
    res = executor(broker(), journal).send(order(), point=POINT, decision_id=decision_id)

    with journal.session() as s:
        row = s.execute(select(Decision).where(Decision.id == decision_id)).scalars().one()
    assert row.order_id == res.order_id


def test_updating_a_missing_decision_is_an_error(journal: Journal):
    with pytest.raises(KeyError):
        journal.update_decision(999, order_id=1)
