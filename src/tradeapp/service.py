"""Runs the core loop in a background thread and hands the API a safe view of it.

The MT5 package is blocking and the loop is synchronous, so the loop gets a thread of its own and
the API keeps the main one. That means two threads touch the same Core, and this class is the only
place that is allowed to: everything goes through one lock, so the API can never read half a tick.

The loop thread also never dies quietly. An exception inside `tick` is caught, journaled as CRIT
and the service stops — a trading loop that has silently stopped looping is the failure mode most
worth making loud.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from tradeapp.core import Core, TickReport
from tradeapp.risk import EngineState

SOURCE = "service"


@dataclass
class ServiceState:
    running: bool = False
    started_utc: datetime | None = None
    ticks: int = 0
    last_tick_utc: datetime | None = None
    last_error: str | None = None
    stopped_reason: str | None = None
    recent: list[dict] = field(default_factory=list)


class CoreService:
    def __init__(self, core: Core, *, tick_interval_s: float | None = None, keep_reports: int = 50) -> None:
        self.core = core
        self.interval = tick_interval_s if tick_interval_s is not None else core.config.tick_interval_s
        self.keep_reports = keep_reports
        self.state = ServiceState()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle ----------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            self.core.start()
            self.state = ServiceState(running=True, started_utc=datetime.now(UTC))
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="core-loop", daemon=True)
        self._thread.start()

    def stop(self, reason: str = "asked to stop", timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        with self._lock:
            if self.state.running:
                self.core.shutdown()
            self.state.running = False
            self.state.stopped_reason = self.state.stopped_reason or reason

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                with self._lock:
                    report = self.core.tick()
                    self._record(report)
            except Exception as e:  # noqa: BLE001 - a loop that dies quietly is the worst outcome
                # Record why first, then mark it stopped. The other order lets an observer see a
                # dead loop with no explanation for it yet, which is exactly the wrong moment to
                # have nothing in the journal.
                self.core.journal.event("CRIT", SOURCE, "core loop stopped on an exception", {"error": str(e)})
                with self._lock:
                    self.state.last_error = f"{type(e).__name__}: {e}"
                    self.state.stopped_reason = "the loop raised"
                    self.state.running = False
                return
            self._stop.wait(self.interval)

    def _record(self, report: TickReport) -> None:
        self.state.ticks += 1
        self.state.last_tick_utc = report.at_utc
        self.state.recent.append(
            {
                "at_utc": report.at_utc.isoformat(),
                "state": report.state.value,
                "equity": report.equity,
                "killed": report.killed,
                "frozen": report.frozen,
                "new_bar": report.new_bar,
                "signals": report.signals,
                "sent": report.sent,
                "notes": report.notes,
            }
        )
        del self.state.recent[: -self.keep_reports]

    # --- the API's view -------------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            core = self.core.status()
        return {
            **core,
            "service": {
                "running": self.state.running,
                "started_utc": self.state.started_utc.isoformat() if self.state.started_utc else None,
                "ticks": self.state.ticks,
                "last_tick_utc": self.state.last_tick_utc.isoformat() if self.state.last_tick_utc else None,
                "last_error": self.state.last_error,
                "stopped_reason": self.state.stopped_reason,
                "tick_interval_s": self.interval,
            },
        }

    def positions(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "side": p.side.value,
                    "volume": p.volume,
                    "price_open": p.price_open,
                    "sl": p.sl,
                    "tp": p.tp,
                    "profit": p.profit,
                    "magic": p.magic,
                    "comment": p.comment,
                }
                for p in self.core.positions
            ]

    def recent_ticks(self) -> list[dict]:
        with self._lock:
            return list(self.state.recent)

    # --- control ---------------------------------------------------------------------

    def kill(self, reason: str) -> dict:
        with self._lock:
            report = self.core.kill.kill(self.core.broker, reason or "manual kill from the UI")
            self.core.positions = _safe_positions(self.core)
        return {
            "trigger": report.trigger.value,
            "detail": report.detail,
            "closed": report.closed,
            "failed": [{"ticket": t, "why": w} for t, w in report.failed],
            "positions_remaining": report.positions_remaining,
            "complete": report.complete,
            "summary": report.summary(),
        }

    def unlock(self, reason: str) -> dict:
        with self._lock:
            state = self.core.kill.unlock(reason)
        return {"state": state.value}

    def pause(self, reason: str = "") -> dict:
        with self._lock:
            state = self.core.kill.pause(reason)
        return {"state": state.value}

    def resume(self) -> dict:
        with self._lock:
            state = self.core.kill.resume()
        return {"state": state.value}

    @property
    def engine_state(self) -> EngineState:
        with self._lock:
            return self.core.kill.state


def _safe_positions(core: Core):
    try:
        return core.broker.positions()
    except Exception:  # noqa: BLE001
        return []


def wait_until(predicate, timeout: float = 5.0, poll: float = 0.02) -> bool:
    """Small helper for tests and startup checks; avoids sprinkling sleeps around."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()
