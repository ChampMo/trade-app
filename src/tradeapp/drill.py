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
from types import SimpleNamespace

from tradeapp.broker.fake import FakeBehavior, FakeBroker
from tradeapp.broker.mt5_bridge import MT5Broker
from tradeapp.contracts import TF, Bar, BrokerError, Intent, Side
from tradeapp.core import Core, CoreConfig
from tradeapp.journal import Journal
from tradeapp.risk.killswitch import KillSwitch, KillTrigger, SystemHealth
from tradeapp.risk.limits import EngineState, RiskLimits
from tradeapp.runtime import StrategyRuntime

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


# --- watchdog drills (P4-01) -----------------------------------------------------------------
#
# The kill-switch drills above never lose the broker: they hand a healthy FakeBroker to the switch
# and check the brake. These ones take the terminal away mid-run, which is the failure this system
# is most likely to actually meet - MT5 drops its connection on every broker restart, every laptop
# sleep, every flaky VPS night. Two outcomes are acceptable and both are checked here:
#
#   - the terminal comes back and the loop carries on, having opened nothing while blind;
#   - it does not, and the kill switch trips on silence and says honestly what it could not close.
#
# What none of this proves is the wiring to a real terminal. Killing terminal64.exe while `serve`
# is running is still an owner task (RUNNING.md), and gate 5 in DECISIONS D3 depends on it.


class _DeadTerminal(FakeBroker):
    """A FakeBroker whose terminal can be taken away and given back."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.alive = True

    def ensure_connected(self) -> bool:
        return self.alive

    def account(self):
        if not self.alive:
            raise BrokerError("terminal not responding")
        return super().account()

    def positions(self, symbol=None, magic=None):
        if not self.alive:
            raise BrokerError("terminal not responding")
        return super().positions(symbol, magic)


class _AlwaysLong:
    id, symbols, timeframe = "drill_long", ["EURUSD"], TF.H4

    def on_bar(self, ctx):
        return Intent(
            symbol=ctx.symbol,
            side=Side.LONG,
            confidence=1.0,
            stop_price=round(ctx.close() - 0.0020, 5),
            take_price=round(ctx.close() + 0.0040, 5),
            reason="watchdog drill",
        )


def _drill_bars(end: datetime, n: int = 60) -> list[Bar]:
    return [
        Bar(
            time_utc=end - timedelta(hours=4 * (n - 1 - i)),
            open=1.1000 + i * 0.0001,
            high=1.1005 + i * 0.0001,
            low=1.0995 + i * 0.0001,
            close=1.1000 + i * 0.0001,
        )
        for i in range(n)
    ]


def _drill_core(journal: Journal | None, broker: FakeBroker, clock) -> Core:
    runtime = StrategyRuntime(journal)
    runtime.register(_AlwaysLong())
    return Core(
        broker,
        journal or Journal(":memory:"),
        runtime=runtime,
        config=CoreConfig(),
        limits=RiskLimits(),
        now=clock,
        sleep=lambda s: None,
    )


def run_watchdog_drills(journal: Journal | None = None) -> list[DrillResult]:
    results: list[DrillResult] = []
    start = datetime.now(UTC)

    def record(name: str, expected: str, got: str, passed: bool) -> None:
        results.append(DrillResult(name, expected, got, passed))
        if journal is not None:
            journal.event(
                "INFO" if passed else "CRIT",
                SOURCE,
                f"drill {'passed' if passed else 'FAILED'}: {name}",
                {"expected": expected, "got": got},
            )

    bars = _drill_bars(start - timedelta(hours=4))

    def fresh_core():
        broker = _DeadTerminal(behavior=FakeBehavior())
        broker.seed_bars(bars)
        broker.bid = bars[-1].close
        clock = [start]
        core = _drill_core(journal, broker, lambda: clock[0])
        core.start()
        core.tick()  # one healthy tick, so the loop has a contact time that can go stale
        return broker, core, clock

    # --- the terminal dies and comes back ---
    broker, core, clock = fresh_core()
    broker.alive = False
    clock[0] = start + timedelta(seconds=5)
    blind = core.tick()
    opened_nothing = blind.sent == 0 and any("not reachable" in n for n in blind.notes)

    broker.alive = True
    clock[0] = start + timedelta(seconds=10)
    broker.seed_bars([*bars, _drill_bars(start + timedelta(hours=4), 1)[0]])
    back = core.tick()
    resumed = back.new_bar and not any("fresh view" in n for n in back.notes)
    record(
        "terminal dies, then returns",
        "nothing opened while blind, loop resumes",
        f"blind_sent={blind.sent} resumed={resumed} state={core.kill.state.value}",
        opened_nothing and resumed and core.kill.state is EngineState.RUNNING,
    )

    # --- the terminal dies and stays dead ---
    broker, core, clock = fresh_core()
    broker.alive = False
    clock[0] = start + timedelta(seconds=61)  # past the silence window
    dead = core.tick()
    record(
        "terminal stays dead",
        "kill on broker silence",
        f"killed={dead.killed} state={core.kill.state.value}",
        dead.killed and core.kill.state is EngineState.KILLED,
    )

    # A kill that could not reach the terminal must not report success. This is the same honesty
    # the kill-switch drills check, arrived at through the loop rather than through the switch.
    summary = dead.notes[-1] if dead.notes else "no note"
    record(
        "a kill through a dead terminal is honest about it",
        "says the positions are unknown, never 'none left open'",
        summary,
        "none left open" not in summary and ("CANNOT REACH" in summary or "STILL OPEN" in summary),
    )

    # --- the bridge itself repairs a dropped connection ---
    stub = _StubTerminal()
    bridge = MT5Broker(mt5_module=stub, journal=journal, sleep=lambda s: None, connect_backoff_s=0.0)
    bridge.connect()
    stub.connected = False  # the terminal is alive but has lost the broker
    healed = bridge.ensure_connected()
    record(
        "a dropped MT5 connection is re-established",
        "reconnected without a human",
        f"healed={healed} reconnects={bridge.reconnects} initialize_calls={stub.initialize_calls}",
        healed and bridge.reconnects == 1 and stub.initialize_calls == 2,
    )

    if journal is not None:
        passed = sum(1 for r in results if r.passed)
        journal.event(
            "INFO" if passed == len(results) else "CRIT",
            SOURCE,
            f"watchdog drill: {passed}/{len(results)} passed",
            {"failed": [r.name for r in results if not r.passed]},
        )
    return results


class _StubTerminal:
    """The few MetaTrader5 calls connect() and ensure_connected() make, and nothing else.

    It exists so the drill exercises the real bridge rather than a FakeBroker that has no terminal
    to lose. `connected` is the flag a real terminal drops when it loses the broker.
    """

    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2

    def __init__(self) -> None:
        self.connected = True
        self.initialize_calls = 0
        self.shutdown_calls = 0

    def initialize(self, **kwargs) -> bool:
        self.initialize_calls += 1
        self.connected = True  # reattaching is what fixes it
        return True

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def last_error(self):
        return (-1, "stub terminal")

    def account_info(self):
        return SimpleNamespace(
            login=0,
            server="drill",
            trade_mode=0,
            balance=10_000.0,
            equity=10_000.0,
            currency="USD",
            leverage=500,
        )

    def terminal_info(self):
        return SimpleNamespace(trade_allowed=True, connected=self.connected)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=1.1, ask=1.1, time=int((datetime.now(UTC) + timedelta(minutes=180)).timestamp()))
