"""Strategy lifecycle: the gates from DECISIONS D3, written as code that refuses.

The plan's own diagnosis of why retail systems fail was not bad code. It was overfitting to a
backtest and then talking yourself past your own rules while excited. Rules you have to remember
are rules you break at 2am; this module is those rules in a place that does not get excited.

research → backtested → forward → live_small → live → retired

Every arrow has numeric conditions and `promote` returns a refusal listing exactly which ones are
not met. There is deliberately no force parameter. Changing a threshold means editing
docs/DECISIONS.md and then this file, in that order (CLAUDE.md rule 07).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from tradeapp.journal import Journal

SOURCE = "lifecycle"
KEY_PREFIX = "lifecycle:"


class LifecycleState(StrEnum):
    RESEARCH = "research"  # an idea; may be backtested freely
    BACKTESTED = "backtested"  # survived costs on full history
    FORWARD = "forward"  # running on demo, parameters frozen
    LIVE_SMALL = "live_small"  # real money at 0.25% risk, one symbol
    LIVE = "live"  # real money at full size
    RETIRED = "retired"  # edge gone or replaced


ORDER = [
    LifecycleState.RESEARCH,
    LifecycleState.BACKTESTED,
    LifecycleState.FORWARD,
    LifecycleState.LIVE_SMALL,
    LifecycleState.LIVE,
]


@dataclass(frozen=True)
class GateLimits:
    """D3, in one place. Editing these is a decision, not a tweak."""

    min_backtest_trades: int = 30
    min_backtest_profit_factor: float = 1.0  # it must at least have made money on its own data
    min_walk_forward_efficiency: float = 0.5
    max_monte_carlo_p95_drawdown_pct: float = 15.0  # half of the 30% account limit
    min_forward_days: int = 90
    min_forward_trades: int = 200
    max_forward_drawdown_pct: float = 15.0
    required_kill_drills: int = 3
    required_news_events: int = 3
    min_live_small_days: int = 30
    max_live_vs_demo_divergence_pct: float = 30.0


@dataclass(frozen=True)
class Evidence:
    """Everything the gates are allowed to read. Anything absent counts as not yet proven."""

    backtest_trades: int | None = None
    backtest_costs_modelled: bool = False
    backtest_stopped_early: bool = False
    backtest_profit_factor: float | None = None
    walk_forward_efficiency: float | None = None
    monte_carlo_p95_drawdown_pct: float | None = None
    forward_days: int | None = None
    forward_trades: int | None = None
    forward_max_drawdown_pct: float | None = None
    forward_params_unchanged: bool = False
    kill_drills_passed: int = 0
    news_events_survived: int = 0
    live_small_days: int | None = None
    live_vs_demo_divergence_pct: float | None = None


@dataclass(frozen=True)
class Gate:
    name: str
    requirement: str
    actual: object
    passed: bool

    def __str__(self) -> str:
        mark = "ok  " if self.passed else "FAIL"
        return f"{mark} {self.name}: need {self.requirement}, have {self.actual}"


@dataclass(frozen=True)
class GateResult:
    frm: LifecycleState
    to: LifecycleState
    gates: list[Gate] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.gates) and all(g.passed for g in self.gates)

    @property
    def failures(self) -> list[Gate]:
        return [g for g in self.gates if not g.passed]

    def report(self) -> str:
        head = f"{self.frm.value} → {self.to.value}: {'ready' if self.passed else 'not ready'}"
        return "\n".join([head, *(f"  {g}" for g in self.gates)])


def next_state(current: LifecycleState) -> LifecycleState | None:
    if current is LifecycleState.RETIRED or current not in ORDER:
        return None
    i = ORDER.index(current)
    return ORDER[i + 1] if i + 1 < len(ORDER) else None


def _gate(name: str, requirement: str, actual, passed: bool) -> Gate:
    return Gate(name=name, requirement=requirement, actual=actual if actual is not None else "nothing", passed=passed)


def _at_least(value: float | int | None, minimum: float | int) -> bool:
    return value is not None and value >= minimum


def _at_most(value: float | int | None, maximum: float | int) -> bool:
    return value is not None and value <= maximum


def evaluate(current: LifecycleState, evidence: Evidence, limits: GateLimits | None = None) -> GateResult:
    """Which conditions stand between this strategy and the next state."""
    lim = limits or GateLimits()
    target = next_state(current)
    if target is None:
        return GateResult(frm=current, to=current, gates=[])

    if target is LifecycleState.BACKTESTED:
        gates = [
            _gate(
                "backtest trades",
                f">= {lim.min_backtest_trades}",
                evidence.backtest_trades,
                _at_least(evidence.backtest_trades, lim.min_backtest_trades),
            ),
            _gate(
                "costs modelled",
                "spread and slippage",
                evidence.backtest_costs_modelled,
                evidence.backtest_costs_modelled,
            ),
            _gate(
                "backtest completed",
                "not stopped by the kill switch",
                "stopped early" if evidence.backtest_stopped_early else "ran to the end",
                not evidence.backtest_stopped_early,
            ),
            _gate(
                "profit factor",
                f"> {lim.min_backtest_profit_factor}",
                evidence.backtest_profit_factor,
                _at_least(evidence.backtest_profit_factor, lim.min_backtest_profit_factor)
                and evidence.backtest_profit_factor != lim.min_backtest_profit_factor,
            ),
        ]
    elif target is LifecycleState.FORWARD:
        gates = [
            _gate(
                "walk-forward efficiency",
                f">= {lim.min_walk_forward_efficiency}",
                evidence.walk_forward_efficiency,
                _at_least(evidence.walk_forward_efficiency, lim.min_walk_forward_efficiency),
            ),
            _gate(
                "Monte Carlo p95 drawdown",
                f"<= {lim.max_monte_carlo_p95_drawdown_pct}%",
                evidence.monte_carlo_p95_drawdown_pct,
                _at_most(evidence.monte_carlo_p95_drawdown_pct, lim.max_monte_carlo_p95_drawdown_pct),
            ),
        ]
    elif target is LifecycleState.LIVE_SMALL:
        gates = [
            _gate(
                "forward test length",
                f">= {lim.min_forward_days} days",
                evidence.forward_days,
                _at_least(evidence.forward_days, lim.min_forward_days),
            ),
            _gate(
                "forward trades",
                f">= {lim.min_forward_trades}",
                evidence.forward_trades,
                _at_least(evidence.forward_trades, lim.min_forward_trades),
            ),
            _gate(
                "parameters untouched",
                "no changes during the forward test",
                evidence.forward_params_unchanged,
                evidence.forward_params_unchanged,
            ),
            _gate(
                "forward drawdown",
                f"<= {lim.max_forward_drawdown_pct}%",
                evidence.forward_max_drawdown_pct,
                _at_most(evidence.forward_max_drawdown_pct, lim.max_forward_drawdown_pct),
            ),
            _gate(
                "kill switch drills",
                f"{lim.required_kill_drills} passed",
                evidence.kill_drills_passed,
                _at_least(evidence.kill_drills_passed, lim.required_kill_drills),
            ),
            _gate(
                "high-impact news survived",
                f">= {lim.required_news_events}",
                evidence.news_events_survived,
                _at_least(evidence.news_events_survived, lim.required_news_events),
            ),
        ]
    else:  # LIVE
        gates = [
            _gate(
                "live_small length",
                f">= {lim.min_live_small_days} days",
                evidence.live_small_days,
                _at_least(evidence.live_small_days, lim.min_live_small_days),
            ),
            _gate(
                "live matches demo",
                f"divergence <= {lim.max_live_vs_demo_divergence_pct}%",
                evidence.live_vs_demo_divergence_pct,
                _at_most(evidence.live_vs_demo_divergence_pct, lim.max_live_vs_demo_divergence_pct),
            ),
        ]

    return GateResult(frm=current, to=target, gates=gates)


class PromotionRefused(RuntimeError):
    """Raised instead of letting a strategy move up on an unmet gate."""

    def __init__(self, result: GateResult) -> None:
        super().__init__(f"cannot promote {result.frm.value} → {result.to.value}:\n{result.report()}")
        self.result = result


class Lifecycle:
    """Per-strategy state, stored in the journal so it survives restarts like everything else (D21)."""

    def __init__(self, journal: Journal, limits: GateLimits | None = None) -> None:
        self.journal = journal
        self.limits = limits or GateLimits()

    def _key(self, strategy_key: str) -> str:
        return f"{KEY_PREFIX}{strategy_key}"

    def state(self, strategy_key: str) -> LifecycleState:
        raw = self.journal.get_state(self._key(strategy_key))
        return LifecycleState(raw["state"]) if raw else LifecycleState.RESEARCH

    def record(self, strategy_key: str) -> dict:
        return self.journal.get_state(self._key(strategy_key)) or {
            "state": LifecycleState.RESEARCH.value,
            "history": [],
        }

    def check(self, strategy_key: str, evidence: Evidence) -> GateResult:
        return evaluate(self.state(strategy_key), evidence, self.limits)

    def promote(self, strategy_key: str, evidence: Evidence) -> LifecycleState:
        """Move up one step, or refuse and say exactly which numbers are missing."""
        result = self.check(strategy_key, evidence)
        if result.to is result.frm:
            raise PromotionRefused(GateResult(frm=result.frm, to=result.frm, gates=[]))
        if not result.passed:
            self.journal.event(
                "WARN",
                SOURCE,
                f"promotion refused for {strategy_key}: {result.frm.value} → {result.to.value}",
                {"failed": [g.name for g in result.failures]},
            )
            raise PromotionRefused(result)
        self._write(strategy_key, result.to, reason=f"gates passed: {result.frm.value} → {result.to.value}")
        return result.to

    def retire(self, strategy_key: str, reason: str) -> LifecycleState:
        """Always allowed. Stopping is never gated; only starting is."""
        if not reason.strip():
            raise ValueError("retiring a strategy needs a reason; it goes in the journal")
        self._write(strategy_key, LifecycleState.RETIRED, reason=reason.strip())
        return LifecycleState.RETIRED

    def demote_to_research(self, strategy_key: str, reason: str) -> LifecycleState:
        """Changing a parameter sends a strategy back to the start (D3): the clock restarts."""
        if not reason.strip():
            raise ValueError("demoting a strategy needs a reason")
        self._write(strategy_key, LifecycleState.RESEARCH, reason=reason.strip())
        return LifecycleState.RESEARCH

    def _write(self, strategy_key: str, state: LifecycleState, reason: str) -> None:
        record = self.record(strategy_key)
        previous = record["state"]
        record["state"] = state.value
        record.setdefault("history", []).append(
            {"from": previous, "to": state.value, "at_utc": datetime.now(UTC).isoformat(), "reason": reason}
        )
        self.journal.set_state(self._key(strategy_key), record)
        self.journal.event("INFO", SOURCE, f"{strategy_key}: {previous} → {state.value}", {"reason": reason})

    def all_states(self) -> dict[str, str]:
        """Everything the store knows about, for the Strategies page and reports."""
        from sqlalchemy import select

        from tradeapp.journal.models import State

        with self.journal.session() as s:
            rows = s.execute(select(State).where(State.key.like(f"{KEY_PREFIX}%"))).scalars().all()
        return {r.key[len(KEY_PREFIX) :]: r.value["v"]["state"] for r in rows}


def evidence_from_backtest(result, monte_carlo=None, walk_forward=None, costs_modelled: bool = True) -> Evidence:
    """Bridge from a backtest run to the gate inputs, so nobody types these numbers by hand."""
    return Evidence(
        backtest_trades=result.stats.trades,
        backtest_costs_modelled=costs_modelled,
        backtest_stopped_early=result.stopped_early,
        backtest_profit_factor=result.stats.profit_factor,
        monte_carlo_p95_drawdown_pct=monte_carlo.drawdown_p95 if monte_carlo else None,
        walk_forward_efficiency=walk_forward.efficiency if walk_forward else None,
    )


# --- who is allowed to trade, and where (D26) --------------------------------------------------

REAL_MONEY_STAGES = (LifecycleState.LIVE_SMALL, LifecycleState.LIVE)


def may_trade(state: LifecycleState, *, real_money: bool) -> bool:
    """The ladder as a runtime rule, not just a report.

    Everything above was about *promoting* a strategy. None of it meant anything while the loop
    registered every strategy it could find regardless of stage: the gates described a ladder that
    nothing was actually made to climb.

    Two rules, and deliberately only two:

    - **retired never trades**, anywhere. That is what retiring is.
    - **real money needs `live_small` or `live`.** Not demo, not paper, not a backtest — money.

    Demo stays open on purpose. Demo is where a strategy earns the evidence the gates ask for, and
    a rule that refused to run anything below `forward` on demo would leave no way to reach
    `forward` at all. What demo is not allowed to do is stay quiet about it: the loop journals a
    warning naming every strategy running below its gate, and the UI shows the stage next to it.
    """
    if state is LifecycleState.RETIRED:
        return False
    return state in REAL_MONEY_STAGES if real_money else True


def below_forward(state: LifecycleState) -> bool:
    """True while a strategy has not yet earned a place on a demo account with frozen parameters."""
    return state in (LifecycleState.RESEARCH, LifecycleState.BACKTESTED)
