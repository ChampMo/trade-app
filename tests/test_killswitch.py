"""Kill switch: fault injection for every trigger, and for the ways closing itself can fail.

This is the code that has to work when nothing else does, so the interesting tests are the ugly
ones: the broker rejects every close, the terminal stops answering, the notifier throws.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tradeapp.broker.fake import FakeBehavior, FakeBroker
from tradeapp.contracts import OrderRequest, Side
from tradeapp.journal import Journal
from tradeapp.risk.killswitch import (
    KillLimits,
    KillSwitch,
    KillTrigger,
    SystemHealth,
)
from tradeapp.risk.limits import EngineState, RiskLimits

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def health(**over) -> SystemHealth:
    base = {
        "now_utc": NOW,
        "equity": 10_000.0,
        "day_start_equity": 10_000.0,
        "peak_equity": 10_000.0,
        "last_broker_contact_utc": NOW,
        "consecutive_rejects": 0,
        "reconcile_mismatch": None,
        "positions_without_stop": (),
    }
    base.update(over)
    return SystemHealth(**base)


def broker_with_positions(n: int = 2, behavior: FakeBehavior | None = None) -> FakeBroker:
    b = FakeBroker(behavior=behavior or FakeBehavior())
    b.connect()
    for _ in range(n):
        b.market_order(
            OrderRequest(
                symbol="EURUSD", side=Side.LONG, volume=0.1, stop_price=1.15700, take_price=None, magic=100_001
            )
        )
    return b


# --- detection: one test per trigger --------------------------------------------------


def test_no_trigger_when_everything_is_fine():
    assert KillSwitch().evaluate(health()) is None


def test_trigger_daily_loss():
    trigger, detail = KillSwitch().evaluate(health(equity=9_700.0))
    assert trigger is KillTrigger.DAILY_LOSS and "3.00%" in detail


def test_trigger_max_drawdown():
    trigger, _ = KillSwitch().evaluate(health(equity=7_000.0, day_start_equity=7_000.0, peak_equity=10_000.0))
    assert trigger is KillTrigger.MAX_DRAWDOWN


def test_drawdown_outranks_daily_loss_when_both_fire():
    """The bigger emergency should be the one named in the journal."""
    trigger, _ = KillSwitch().evaluate(health(equity=6_000.0, day_start_equity=10_000.0, peak_equity=10_000.0))
    assert trigger is KillTrigger.MAX_DRAWDOWN


def test_trigger_broker_silence():
    trigger, detail = KillSwitch().evaluate(health(last_broker_contact_utc=NOW - timedelta(seconds=61)))
    assert trigger is KillTrigger.BROKER_SILENCE and "61s" in detail


def test_silence_just_inside_the_limit_does_not_fire():
    assert KillSwitch().evaluate(health(last_broker_contact_utc=NOW - timedelta(seconds=59))) is None


def test_trigger_consecutive_rejects():
    trigger, _ = KillSwitch().evaluate(health(consecutive_rejects=3))
    assert trigger is KillTrigger.CONSECUTIVE_REJECTS
    assert KillSwitch().evaluate(health(consecutive_rejects=2)) is None


def test_trigger_reconcile_mismatch():
    trigger, detail = KillSwitch().evaluate(health(reconcile_mismatch="MT5 has 2 positions, journal has 1"))
    assert trigger is KillTrigger.RECONCILE_MISMATCH and "journal has 1" in detail


def test_trigger_position_without_a_stop():
    """Rule 03 broken at the broker is unbounded risk, so it is an emergency, not a warning."""
    trigger, detail = KillSwitch().evaluate(health(positions_without_stop=(500_001,)))
    assert trigger is KillTrigger.POSITION_WITHOUT_STOP and "500001" in detail


def test_limits_are_derived_from_risk_limits_so_they_cannot_drift():
    lim = KillLimits.from_risk(RiskLimits(daily_loss_limit_pct=2.0, max_drawdown_pct=25.0))
    assert lim.daily_loss_pct == 2.0 and lim.max_drawdown_pct == 25.0
    assert lim.broker_silence_s == 60.0  # kill-only settings keep their defaults


# --- firing: the whole emergency path -------------------------------------------------


def test_trip_closes_everything_and_locks_the_engine(journal: Journal):
    broker = broker_with_positions(2)
    ks = KillSwitch(journal=journal)

    report = ks.check_and_trip(health(equity=9_700.0), broker)

    assert report is not None
    assert report.trigger is KillTrigger.DAILY_LOSS
    assert report.complete is True and report.positions_remaining == 0
    assert len(report.closed) == 2 and report.failed == []
    assert broker.open_tickets == []
    assert ks.state is EngineState.KILLED
    assert ks.accepting_intents is False


def test_trip_is_journaled_as_critical(journal: Journal):
    ks = KillSwitch(journal=journal)
    ks.check_and_trip(health(equity=9_700.0), broker_with_positions(1))

    messages = [e.message for e in journal.events_where(source="kill")]
    assert messages[0] == "kill switch tripped"
    crit = [e for e in journal.events_where(severity="CRIT", source="kill")]
    assert crit and crit[0].data["trigger"] == "daily_loss"


def test_a_close_that_needs_a_retry_still_completes():
    broker = broker_with_positions(1, FakeBehavior(fail_close_times=1))
    report = KillSwitch().kill(broker)
    assert report.complete is True and report.positions_remaining == 0


def test_a_kill_that_cannot_close_reports_failure_loudly(journal: Journal):
    """The dangerous lie would be complete=True with a position still bleeding."""
    broker = broker_with_positions(2, FakeBehavior(fail_close_always=True))
    report = KillSwitch(journal=journal).kill(broker)

    assert report.complete is False
    assert report.positions_remaining == 2
    assert len(report.failed) == 2 and all(why == "REJECT" for _, why in report.failed)
    assert "STILL OPEN" in report.summary()
    assert broker.open_tickets != []
    crit = [e for e in journal.events_where(severity="CRIT", source="kill")]
    assert any("STILL OPEN" in e.message for e in crit)


def test_a_broker_that_cannot_be_read_is_still_a_kill(journal: Journal):
    broker = broker_with_positions(1, FakeBehavior(raise_on_positions=True))
    report = KillSwitch(journal=journal).kill(broker)
    assert report.complete is False
    assert KillSwitch(journal=journal).state is EngineState.RUNNING  # a fresh one is unaffected
    assert any("cannot read positions" in e.message for e in journal.events_where(source="kill"))


def test_kill_with_no_open_positions_is_complete():
    broker = FakeBroker()
    broker.connect()
    report = KillSwitch().kill(broker)
    assert report.complete is True and report.closed == []


def test_a_dead_notifier_never_blocks_the_close(journal: Journal):
    class BrokenTelegram:
        def critical(self, message, data=None):
            raise ConnectionError("telegram unreachable")

    broker = broker_with_positions(1)
    report = KillSwitch(journal=journal, notifier=BrokenTelegram()).kill(broker)

    assert report.complete is True and broker.open_tickets == []
    assert report.notified is False
    assert any("notification failed" in e.message for e in journal.events_where(source="kill"))


def test_a_working_notifier_is_told_what_happened():
    seen = []

    class Telegram:
        def critical(self, message, data=None):
            seen.append((message, data))

    report = KillSwitch(notifier=Telegram()).kill(broker_with_positions(1))
    assert report.notified is True
    assert "KILLED" in seen[0][0] and seen[0][1]["trigger"] == "manual"


def test_check_and_trip_does_nothing_twice():
    broker = broker_with_positions(1)
    ks = KillSwitch()
    assert ks.check_and_trip(health(equity=9_700.0), broker) is not None
    assert ks.check_and_trip(health(equity=9_700.0), broker) is None  # already KILLED


# --- state machine (D12) --------------------------------------------------------------


def test_unlock_requires_a_reason_and_lands_in_paused(journal: Journal):
    ks = KillSwitch(journal=journal)
    ks.kill(broker_with_positions(0))

    with pytest.raises(ValueError, match="requires a reason"):
        ks.unlock("   ")
    assert ks.state is EngineState.KILLED

    assert ks.unlock("NFP spike, stop was too tight") is EngineState.PAUSED
    assert ks.accepting_intents is False  # PAUSED still does not trade


def test_the_reason_reaches_the_journal(journal: Journal):
    ks = KillSwitch(journal=journal)
    ks.kill(broker_with_positions(0))
    ks.unlock("widened the news window")
    unlocked = [e for e in journal.events_where(source="kill") if e.message == "kill switch unlocked"]
    assert unlocked[0].data["reason"] == "widened the news window"


def test_resume_needs_a_deliberate_second_step():
    """D12: unlocking never puts the bot straight back to work."""
    ks = KillSwitch()
    ks.kill(broker_with_positions(0))
    with pytest.raises(RuntimeError, match="only for a PAUSED engine"):
        ks.resume()
    ks.unlock("checked the journal")
    assert ks.resume() is EngineState.RUNNING
    assert ks.accepting_intents is True


def test_unlock_only_applies_to_a_killed_engine():
    ks = KillSwitch()
    with pytest.raises(RuntimeError, match="only for a KILLED engine"):
        ks.unlock("nothing to unlock")


def test_a_killed_engine_cannot_be_quietly_paused():
    ks = KillSwitch()
    ks.kill(broker_with_positions(0))
    with pytest.raises(RuntimeError, match="must be unlocked"):
        ks.pause("carry on")


def test_pause_and_resume_without_a_kill():
    ks = KillSwitch()
    assert ks.pause("lunch") is EngineState.PAUSED
    assert ks.resume() is EngineState.RUNNING


def test_unreachable_broker_says_unknown_not_minus_one():
    """ "-1 positions still open" is gibberish in the one message that must be instantly clear."""
    broker = broker_with_positions(1, FakeBehavior(raise_on_positions=True))
    report = KillSwitch().kill(broker)
    assert report.positions_unknown is True
    assert "CANNOT REACH THE BROKER" in report.summary()
    assert "-1" not in report.summary()
