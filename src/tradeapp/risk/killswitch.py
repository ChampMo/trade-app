"""Kill switch: deterministic code, never AI (CLAUDE.md rule 06, D12).

Every trigger is a number compared against a limit. No model, no judgement, no network call
stands between the condition and the close. When it fires: close everything, stop accepting
intents, shout, and wait for a human. Unlocking needs a reason and lands in PAUSED, never
straight back in RUNNING, because whatever caused this deserves a look before trading resumes.

Two things this module refuses to do:

- report success it cannot prove. After closing it re-reads positions from the broker rather than
  trusting its own bookkeeping, and a kill with anything left open is `complete=False`. A kill
  switch that believes it closed everything while a position bleeds is worse than none.
- let a broken side channel stop the emergency. A notifier that raises is swallowed and journaled;
  Telegram being down is not a reason to leave positions open.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from tradeapp.contracts import Position
from tradeapp.journal import Journal
from tradeapp.risk.limits import EngineState, RiskLimits

SOURCE = "kill"


class KillTrigger(StrEnum):
    DAILY_LOSS = "daily_loss"
    MAX_DRAWDOWN = "max_drawdown"
    BROKER_SILENCE = "broker_silence"
    CONSECUTIVE_REJECTS = "consecutive_rejects"
    RECONCILE_MISMATCH = "reconcile_mismatch"
    POSITION_WITHOUT_STOP = "position_without_stop"
    MANUAL = "manual"


class Notifier(Protocol):
    """Telegram implements this in P4-02. Failures here never block a kill."""

    def critical(self, message: str, data: dict | None = None) -> None: ...


@dataclass(frozen=True)
class KillLimits:
    """Numbers only. Same thresholds as the Risk Engine, different action: it stops opening, this closes."""

    daily_loss_pct: float = 3.0
    max_drawdown_pct: float = 30.0
    broker_silence_s: float = 60.0
    consecutive_rejects: int = 3

    @classmethod
    def from_risk(cls, risk: RiskLimits, **over: Any) -> KillLimits:
        """Derive from RiskLimits so the two can never drift apart in a config edit."""
        base = {"daily_loss_pct": risk.daily_loss_limit_pct, "max_drawdown_pct": risk.max_drawdown_pct}
        return cls(**{**base, **over})


@dataclass(frozen=True)
class SystemHealth:
    """Everything the kill switch is allowed to look at, gathered by the caller each tick."""

    now_utc: datetime
    equity: float
    day_start_equity: float
    peak_equity: float
    last_broker_contact_utc: datetime | None = None
    consecutive_rejects: int = 0
    reconcile_mismatch: str | None = None  # filled by P1-08
    positions_without_stop: tuple[int, ...] = ()  # rule 03 violated at the broker


@dataclass(frozen=True)
class KillReport:
    trigger: KillTrigger
    detail: str
    at_utc: datetime
    closed: list[int] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)
    positions_remaining: int = 0  # -1 means the broker could not be read, so this is unknown
    complete: bool = False
    notified: bool = False

    @property
    def positions_unknown(self) -> bool:
        return self.positions_remaining < 0

    def summary(self) -> str:
        head = f"KILLED [{self.trigger.value}] {self.detail}"
        if self.complete:
            return f"{head} — closed {len(self.closed)} position(s), none left open"
        if self.positions_unknown:
            return f"{head} — CANNOT REACH THE BROKER, open positions unknown; check the terminal by hand right now"
        return f"{head} — {self.positions_remaining} position(s) STILL OPEN, needs manual intervention"


class KillSwitch:
    def __init__(
        self,
        limits: KillLimits | None = None,
        *,
        journal: Journal | None = None,
        notifier: Notifier | None = None,
        state: EngineState = EngineState.RUNNING,
        close_attempts: int = 3,
    ) -> None:
        self.limits = limits or KillLimits()
        self.journal = journal
        self.notifier = notifier
        self.state = state
        self.close_attempts = max(1, close_attempts)
        self.last_report: KillReport | None = None

    # --- detection: numbers only -------------------------------------------------

    def evaluate(self, health: SystemHealth) -> tuple[KillTrigger, str] | None:
        """Return the first trigger that fires, or None. Pure: it decides nothing and closes nothing."""
        lim = self.limits

        drawdown = _pct_drop(health.equity, health.peak_equity)
        if drawdown >= lim.max_drawdown_pct:
            return KillTrigger.MAX_DRAWDOWN, f"drawdown {drawdown:.2f}% reached the {lim.max_drawdown_pct:.2f}% limit"

        daily = _pct_drop(health.equity, health.day_start_equity)
        if daily >= lim.daily_loss_pct:
            return KillTrigger.DAILY_LOSS, f"down {daily:.2f}% today, limit is {lim.daily_loss_pct:.2f}%"

        if health.last_broker_contact_utc is not None:
            silence = (health.now_utc - health.last_broker_contact_utc).total_seconds()
            if silence > lim.broker_silence_s:
                return (
                    KillTrigger.BROKER_SILENCE,
                    f"no contact with MT5 for {silence:.0f}s, limit is {lim.broker_silence_s:.0f}s",
                )

        if health.consecutive_rejects >= lim.consecutive_rejects:
            return (
                KillTrigger.CONSECUTIVE_REJECTS,
                f"{health.consecutive_rejects} orders rejected in a row, limit is {lim.consecutive_rejects}",
            )

        if health.reconcile_mismatch:
            return KillTrigger.RECONCILE_MISMATCH, f"broker and journal disagree: {health.reconcile_mismatch}"

        if health.positions_without_stop:
            tickets = ", ".join(str(t) for t in health.positions_without_stop)
            return KillTrigger.POSITION_WITHOUT_STOP, f"position(s) {tickets} have no stop at the broker (rule 03)"

        return None

    # --- the emergency path ------------------------------------------------------

    def check_and_trip(self, health: SystemHealth, broker: Any) -> KillReport | None:
        """Evaluate, and fire if anything is over the line. The loop calls this every tick.

        Only while RUNNING. That is not an optimisation, it is what makes unlocking possible: the
        condition that caused a kill is usually still true afterwards (equity does not recover
        because someone typed a reason), so a switch that re-evaluated while PAUSED would slam
        shut on the next tick and the operator could never get back in. Positions are already flat
        by then, and anything opened later is opened from RUNNING, where the switch is live again.
        """
        if self.state is not EngineState.RUNNING:
            return None
        fired = self.evaluate(health)
        if fired is None:
            return None
        trigger, detail = fired
        return self.trip(trigger, detail, broker, at_utc=health.now_utc)

    def trip(
        self,
        trigger: KillTrigger,
        detail: str,
        broker: Any,
        at_utc: datetime | None = None,
    ) -> KillReport:
        at = at_utc or datetime.now()
        # State first: no intent may be accepted while positions are being closed.
        self.state = EngineState.KILLED
        self._event("CRIT", "kill switch tripped", {"trigger": trigger.value, "detail": detail})

        closed, failed, remaining = self._close_everything(broker)
        report = KillReport(
            trigger=trigger,
            detail=detail,
            at_utc=at,
            closed=closed,
            failed=failed,
            positions_remaining=remaining,
            complete=remaining == 0,
        )
        report = KillReport(**{**report.__dict__, "notified": self._notify(report)})

        self._event(
            "CRIT" if not report.complete else "WARN",
            report.summary(),
            {
                "trigger": trigger.value,
                "closed": closed,
                "failed": [{"ticket": t, "why": w} for t, w in failed],
                "positions_remaining": remaining,
                "complete": report.complete,
            },
        )
        self.last_report = report
        return report

    def _close_everything(self, broker: Any) -> tuple[list[int], list[tuple[int, str]], int]:
        """Close every open position, retrying, then verify against the broker rather than our own list."""
        closed: list[int] = []
        failed: dict[int, str] = {}

        for attempt in range(1, self.close_attempts + 1):
            try:
                positions: list[Position] = broker.positions()
            except Exception as e:  # noqa: BLE001 - a broker that cannot be read is the emergency
                self._event("CRIT", "cannot read positions during kill", {"attempt": attempt, "error": str(e)})
                return closed, list(failed.items()) + [(-1, f"positions() failed: {e}")], -1
            if not positions:
                break
            for pos in positions:
                try:
                    res = broker.close_position(pos.ticket)
                except Exception as e:  # noqa: BLE001
                    failed[pos.ticket] = f"{type(e).__name__}: {e}"
                    continue
                if res.ok:
                    closed.append(pos.ticket)
                    failed.pop(pos.ticket, None)
                else:
                    failed[pos.ticket] = res.retcode_desc

        # The only answer that counts comes from the broker, not from the loop above.
        try:
            remaining = len(broker.positions())
        except Exception as e:  # noqa: BLE001
            self._event("CRIT", "cannot verify positions after kill", {"error": str(e)})
            remaining = -1
        return closed, list(failed.items()), remaining

    # --- human-operated state changes --------------------------------------------

    def kill(self, broker: Any, detail: str = "manual kill") -> KillReport:
        """The UI button and Telegram /kill both land here (D12)."""
        return self.trip(KillTrigger.MANUAL, detail, broker)

    def unlock(self, reason: str) -> EngineState:
        """Leave KILLED only with a written reason, and only as far as PAUSED (D12)."""
        if self.state is not EngineState.KILLED:
            raise RuntimeError(f"unlock is only for a KILLED engine; state is {self.state.value}")
        if not reason or not reason.strip():
            raise ValueError("unlock requires a reason; it goes in the journal and into the post-mortem")
        self.state = EngineState.PAUSED
        self._event("WARN", "kill switch unlocked", {"reason": reason.strip(), "state": self.state.value})
        return self.state

    def resume(self) -> EngineState:
        if self.state is not EngineState.PAUSED:
            raise RuntimeError(f"resume is only for a PAUSED engine; state is {self.state.value}")
        self.state = EngineState.RUNNING
        self._event("INFO", "trading resumed", {"state": self.state.value})
        return self.state

    def pause(self, reason: str = "") -> EngineState:
        if self.state is EngineState.KILLED:
            raise RuntimeError("a KILLED engine must be unlocked, not paused")
        self.state = EngineState.PAUSED
        self._event("INFO", "trading paused", {"reason": reason})
        return self.state

    @property
    def accepting_intents(self) -> bool:
        return self.state is EngineState.RUNNING

    # --- plumbing ----------------------------------------------------------------

    def _notify(self, report: KillReport) -> bool:
        if self.notifier is None:
            return False
        try:
            self.notifier.critical(report.summary(), {"trigger": report.trigger.value})
            return True
        except Exception as e:  # noqa: BLE001 - a dead notifier must never keep positions open
            self._event("WARN", "kill notification failed", {"error": str(e)})
            return False

    def _event(self, severity: str, message: str, data: dict | None = None) -> None:
        if self.journal is not None:
            self.journal.event(severity, SOURCE, message, data)


def _pct_drop(current: float, reference: float) -> float:
    if reference <= 0:
        return 0.0
    return max(0.0, (reference - current) / reference * 100.0)
