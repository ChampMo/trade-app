"""Reconcile: the broker is the truth, and a stop doing its job is not an emergency."""

from datetime import UTC, datetime

from tradeapp.broker.fake import FakeBehavior, FakeBroker
from tradeapp.contracts import OrderRequest, Side
from tradeapp.execution import Executor
from tradeapp.journal import Journal
from tradeapp.reconcile import Reconciler

NO_SLEEP = lambda s: None  # noqa: E731
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def an_order(magic: int = 100_001) -> OrderRequest:
    return OrderRequest(
        symbol="EURUSD", side=Side.LONG, volume=0.1, stop_price=1.15700, take_price=None, magic=magic, comment="t"
    )


def broker_and_journal(journal: Journal, **behavior):
    b = FakeBroker(behavior=FakeBehavior(**behavior))
    b.connect()
    return b, Executor(b, journal, sleep=NO_SLEEP)


def reconciler(b, journal, **kw) -> Reconciler:
    return Reconciler(b, journal, now=lambda: NOW, **kw)


# --- the journal's own view -----------------------------------------------------------


def test_journal_derives_open_positions_from_the_ledger(journal: Journal):
    b, ex = broker_and_journal(journal)
    first = ex.send(an_order())
    second = ex.send(an_order(magic=100_002))
    assert journal.open_position_tickets() == {first.position_ticket, second.position_ticket}

    ex.close(first.position_ticket)
    assert journal.open_position_tickets() == {second.position_ticket}


def test_a_rejected_open_never_counts_as_a_position(journal: Journal):
    b, ex = broker_and_journal(journal, reject_orders=True)
    ex.send(an_order())
    assert journal.open_position_tickets() == set()


# --- the clean case -------------------------------------------------------------------


def test_everything_matching_is_quiet(journal: Journal):
    b, ex = broker_and_journal(journal)
    opened = ex.send(an_order())
    result = reconciler(b, journal).run()

    assert result.ok and result.mismatch is None
    assert result.matched == [opened.position_ticket]
    assert not (result.orphans or result.ghosts or result.unprotected or result.foreign)
    assert any("reconcile ok" in e.message for e in journal.events_where(source="reconcile"))


def test_an_empty_account_reconciles_clean(journal: Journal):
    b = FakeBroker()
    b.connect()
    assert reconciler(b, journal).run().ok


# --- orphans: the dangerous direction --------------------------------------------------


def test_an_orphan_position_freezes_new_entries(journal: Journal):
    """Money at risk that the ledger never recorded. A timed-out order that actually filled looks like this."""
    b, ex = broker_and_journal(journal)
    b.seed_position(magic=100_001)  # appears at the broker without going through us

    rec = reconciler(b, journal)
    result = rec.run()

    assert result.ok is False
    assert len(result.orphans) == 1
    assert rec.frozen is True
    assert "never recorded" in rec.freeze_reason
    crit = [e for e in journal.events_where(severity="CRIT", source="reconcile")]
    assert any("orphan position" in e.message for e in crit)


def test_an_orphan_becomes_a_kill_switch_mismatch(journal: Journal):
    """This is the half of P1-05b that reconcile owns."""
    from tradeapp.risk.killswitch import KillSwitch, KillTrigger, SystemHealth

    b = FakeBroker()
    b.connect()
    b.seed_position()
    rec = reconciler(b, journal)
    rec.run()

    health = SystemHealth(
        now_utc=NOW,
        equity=10_000.0,
        day_start_equity=10_000.0,
        peak_equity=10_000.0,
        last_broker_contact_utc=NOW,
        **rec.health_inputs(),
    )
    trigger, detail = KillSwitch().evaluate(health)
    assert trigger is KillTrigger.RECONCILE_MISMATCH
    assert "never recorded" in detail


def test_the_freeze_lifts_once_the_orphan_is_gone(journal: Journal):
    b = FakeBroker()
    b.connect()
    pos = b.seed_position()
    rec = reconciler(b, journal)
    rec.run()
    assert rec.frozen is True

    b.close_position(pos.ticket)
    rec.run()
    assert rec.frozen is False and rec.freeze_reason is None
    assert any("unfrozen" in e.message for e in journal.events_where(source="reconcile"))


# --- ghosts: the normal direction ------------------------------------------------------


def test_a_stop_being_hit_is_not_an_alarm(journal: Journal):
    """The ledger thinks it is open, the broker says gone. That is a stop working, not a fault."""
    b, ex = broker_and_journal(journal)
    opened = ex.send(an_order())
    b._positions.pop(opened.position_ticket)  # the broker closed it on its own

    rec = reconciler(b, journal)
    result = rec.run()

    assert result.ghosts == [opened.position_ticket]
    assert result.ok is True and result.mismatch is None
    assert rec.frozen is False  # trading continues
    assert not [e for e in journal.events_where(severity="CRIT", source="reconcile")]


def test_a_ghost_is_recorded_so_the_next_run_is_quiet(journal: Journal):
    b, ex = broker_and_journal(journal)
    opened = ex.send(an_order())
    b._positions.pop(opened.position_ticket)

    rec = reconciler(b, journal)
    rec.run()
    assert journal.open_position_tickets() == set()

    second = rec.run()
    assert second.ghosts == []  # not reported twice
    closes = [o for o in journal.orders_for(f"reconcile-{opened.position_ticket}")]
    assert closes and closes[0].retcode_desc == "CLOSED_ELSEWHERE"


def test_ghost_recording_can_be_turned_off(journal: Journal):
    b, ex = broker_and_journal(journal)
    opened = ex.send(an_order())
    b._positions.pop(opened.position_ticket)

    rec = reconciler(b, journal, record_ghosts=False)
    rec.run()
    assert journal.open_position_tickets() == {opened.position_ticket}


# --- rule 03 audit ---------------------------------------------------------------------


def test_a_position_without_a_stop_is_reported_for_the_kill_switch(journal: Journal):
    b, ex = broker_and_journal(journal, drop_sl_on_fill=True)
    opened = ex.send(an_order(), verify_stop=False)  # deliberately left unprotected

    rec = reconciler(b, journal)
    result = rec.run()

    assert result.unprotected_tickets == (opened.position_ticket,)
    assert any("no stop at the broker" in e.message for e in journal.events_where(severity="CRIT", source="reconcile"))


def test_an_unprotected_position_reaches_the_kill_switch(journal: Journal):
    from tradeapp.risk.killswitch import KillSwitch, KillTrigger, SystemHealth

    b, ex = broker_and_journal(journal, drop_sl_on_fill=True)
    ex.send(an_order(), verify_stop=False)
    rec = reconciler(b, journal)
    rec.run()

    health = SystemHealth(
        now_utc=NOW,
        equity=10_000.0,
        day_start_equity=10_000.0,
        peak_equity=10_000.0,
        last_broker_contact_utc=NOW,
        **rec.health_inputs(),
    )
    trigger, _ = KillSwitch().evaluate(health)
    assert trigger is KillTrigger.POSITION_WITHOUT_STOP


# --- positions that are not ours -------------------------------------------------------


def test_a_manual_trade_is_reported_but_not_treated_as_an_orphan(journal: Journal):
    b = FakeBroker()
    b.connect()
    b.seed_position(magic=0)  # a human clicked buy in the terminal

    rec = reconciler(b, journal, magics={100_001, 100_002})
    result = rec.run()

    assert len(result.foreign) == 1
    assert result.orphans == [] and result.ok is True
    assert rec.frozen is False
    assert any("not ours" in e.message for e in journal.events_where(severity="WARN", source="reconcile"))


def test_our_own_magic_is_still_checked_when_magics_are_given(journal: Journal):
    b = FakeBroker()
    b.connect()
    b.seed_position(magic=100_001)

    result = reconciler(b, journal, magics={100_001}).run()
    assert len(result.orphans) == 1 and result.foreign == []


# --- a broker that cannot be read ------------------------------------------------------


def test_an_unreadable_broker_freezes_rather_than_assuming_nothing_is_open(journal: Journal):
    b = FakeBroker(behavior=FakeBehavior(raise_on_positions=True))
    b.connect()
    rec = reconciler(b, journal)
    result = rec.run()

    assert result.ok is False and result.error is not None
    assert rec.frozen is True
    assert "cannot read positions" in result.mismatch
    assert any("cannot read positions" in e.message for e in journal.events_where(severity="CRIT", source="reconcile"))


def test_summary_is_readable():
    b = FakeBroker()
    b.connect()
    assert "matched" in Reconciler(b, Journal(":memory:"), now=lambda: NOW).run().summary()
