"""Kill-switch drills: fire every trigger on purpose and check what actually happened.

These run against the fake broker, so they prove the logic and the journalling, not the wiring to
a real terminal. Pulling the network cable and killing terminal64.exe mid-trade is a different and
harder test; it belongs to the watchdog work (P4-01) and to gate 5 in DECISIONS D3, and this
command does not substitute for it. What it does give you is a repeatable, journaled answer to
"does the brake still work" after any change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tradeapp.broker.fake import FakeBehavior, FakeBroker
from tradeapp.journal import Journal
from tradeapp.risk.killswitch import KillSwitch, KillTrigger, SystemHealth
from tradeapp.risk.limits import EngineState

SOURCE = "drill"


@dataclass
class DrillResult:
    name: str
    expected: str
    got: str
    passed: bool


def _broker(positions: int = 2, behavior: FakeBehavior | None = None) -> FakeBroker:
    b = FakeBroker(behavior=behavior or FakeBehavior())
    b.connect()
    for _ in range(positions):
        b.seed_position()
    return b


def _health(now: datetime, **over) -> SystemHealth:
    base = {
        "now_utc": now,
        "equity": 10_000.0,
        "day_start_equity": 10_000.0,
        "peak_equity": 10_000.0,
        "last_broker_contact_utc": now,
    }
    base.update(over)
    return SystemHealth(**base)


def run_drills(journal: Journal | None = None) -> list[DrillResult]:
    now = datetime.now(UTC)
    results: list[DrillResult] = []

    def record(name: str, expected: str, got: str, passed: bool) -> None:
        results.append(DrillResult(name, expected, got, passed))
        if journal is not None:
            journal.event(
                "INFO" if passed else "CRIT",
                SOURCE,
                f"drill {'passed' if passed else 'FAILED'}: {name}",
                {"expected": expected, "got": got},
            )

    # --- every trigger fires on its own condition ---
    cases = [
        ("daily loss 3%", {"equity": 9_700.0}, KillTrigger.DAILY_LOSS),
        ("drawdown 30%", {"equity": 7_000.0, "day_start_equity": 7_000.0}, KillTrigger.MAX_DRAWDOWN),
        ("MT5 silent 61s", {"last_broker_contact_utc": now - timedelta(seconds=61)}, KillTrigger.BROKER_SILENCE),
        ("3 rejects in a row", {"consecutive_rejects": 3}, KillTrigger.CONSECUTIVE_REJECTS),
        ("reconcile mismatch", {"reconcile_mismatch": "MT5 2, journal 1"}, KillTrigger.RECONCILE_MISMATCH),
        ("position with no stop", {"positions_without_stop": (500_001,)}, KillTrigger.POSITION_WITHOUT_STOP),
    ]
    for name, over, expect in cases:
        broker = _broker(2)
        ks = KillSwitch(journal=journal)
        report = ks.check_and_trip(_health(now, **over), broker)
        ok = (
            report is not None
            and report.trigger is expect
            and report.complete
            and broker.open_tickets == []
            and ks.state is EngineState.KILLED
        )
        got = "no trigger" if report is None else f"{report.trigger.value}, {len(report.closed)} closed"
        record(name, f"{expect.value}, all closed", got, ok)

    # --- healthy system is left alone ---
    broker = _broker(2)
    ks = KillSwitch(journal=journal)
    untouched = ks.check_and_trip(_health(now), broker) is None and len(broker.open_tickets) == 2
    record("healthy system untouched", "no trigger, 2 open", "ok" if untouched else "tripped", untouched)

    # --- manual kill, the UI button and Telegram /kill ---
    broker = _broker(2)
    ks = KillSwitch(journal=journal)
    report = ks.kill(broker, "drill: manual kill")
    record(
        "manual kill",
        "all closed",
        f"{len(report.closed)} closed, {report.positions_remaining} left",
        report.complete and broker.open_tickets == [],
    )

    # --- the failures that matter: it must not claim success ---
    broker = _broker(2, FakeBehavior(fail_close_always=True))
    report = KillSwitch(journal=journal).kill(broker, "drill: broker rejects every close")
    honest = report.complete is False and report.positions_remaining == 2
    record("broker rejects every close", "reported incomplete", report.summary(), honest)

    broker = _broker(1, FakeBehavior(raise_on_positions=True))
    report = KillSwitch(journal=journal).kill(broker, "drill: terminal not responding")
    record("terminal not responding", "reported incomplete", report.summary(), report.complete is False)

    broker = _broker(1, FakeBehavior(fail_close_times=1))
    report = KillSwitch(journal=journal).kill(broker, "drill: one requote then success")
    record("retry after a requote", "all closed", report.summary(), report.complete)

    # --- unlocking is deliberate (D12) ---
    ks = KillSwitch(journal=journal)
    ks.kill(_broker(0), "drill: state machine")
    try:
        ks.unlock("  ")
        reason_enforced = False
    except ValueError:
        reason_enforced = True
    lands_paused = ks.unlock("drill: reviewed and cleared") is EngineState.PAUSED
    still_locked = ks.accepting_intents is False
    resumed = ks.resume() is EngineState.RUNNING
    record(
        "unlock needs a reason and lands in PAUSED",
        "reason required, PAUSED then RUNNING",
        f"reason_enforced={reason_enforced} paused={lands_paused} resumed={resumed}",
        reason_enforced and lands_paused and still_locked and resumed,
    )

    if journal is not None:
        passed = sum(1 for r in results if r.passed)
        journal.event(
            "INFO" if passed == len(results) else "CRIT",
            SOURCE,
            f"kill switch drill: {passed}/{len(results)} passed",
            {"failed": [r.name for r in results if not r.passed]},
        )
    return results
