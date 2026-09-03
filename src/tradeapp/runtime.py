"""Strategy runtime: run every strategy over a bar, and make sure one broken one cannot stop the rest.

Isolation is the whole job. A strategy is the part of this system most likely to be wrong, most
likely to be written quickly, and most likely to be written by an unattended session. So a
strategy that raises is disabled on the spot with a CRIT event, and the others carry on. Same for
one that returns nonsense: an Intent for the wrong symbol is a bug, not a trade.

The runtime is synchronous and owns no event loop on purpose. The live core will call `on_bar`
from its loop and the backtest will call it from a for-loop, which is rule 04 in practice: one
code path, two callers.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Any

from tradeapp.contracts import Intent
from tradeapp.journal import Journal

SOURCE = "strategy"


@dataclass
class Slot:
    """One registered strategy and how it has been behaving."""

    strategy: Any
    variant: str | None = None
    enabled: bool = True
    error: str | None = None
    calls: int = 0
    signals: int = 0

    @property
    def id(self) -> str:
        return self.strategy.id

    @property
    def key(self) -> str:
        return f"{self.id}·{self.variant}" if self.variant else self.id


@dataclass(frozen=True)
class Signal:
    strategy_id: str
    variant: str | None
    intent: Intent

    @property
    def key(self) -> str:
        return f"{self.strategy_id}·{self.variant}" if self.variant else self.strategy_id


class StrategyRuntime:
    def __init__(self, journal: Journal | None = None) -> None:
        self.journal = journal
        self.slots: list[Slot] = []

    # --- registration -------------------------------------------------------------

    def register(self, strategy: Any, variant: str | None = None) -> Slot:
        key = f"{strategy.id}·{variant}" if variant else strategy.id
        if any(s.key == key for s in self.slots):
            raise ValueError(f"{key} is already registered")
        slot = Slot(strategy=strategy, variant=variant)
        self.slots.append(slot)
        self._event("INFO", f"strategy registered: {key}", {"symbols": list(strategy.symbols)})
        return slot

    def slot(self, key: str) -> Slot | None:
        return next((s for s in self.slots if s.key == key or s.id == key), None)

    @property
    def enabled(self) -> list[Slot]:
        return [s for s in self.slots if s.enabled]

    # --- the loop calls this ------------------------------------------------------

    def on_bar(self, ctx) -> list[Signal]:
        """Ask every enabled strategy that trades this symbol. Never raises."""
        signals: list[Signal] = []
        for slot in list(self.slots):
            if not slot.enabled or ctx.symbol not in slot.strategy.symbols:
                continue
            if getattr(slot.strategy, "timeframe", ctx.timeframe) != ctx.timeframe:
                continue
            slot.calls += 1
            try:
                intent = slot.strategy.on_bar(ctx)
            except Exception as e:  # noqa: BLE001 - isolation is the point of this class
                self.disable(
                    slot,
                    f"{type(e).__name__}: {e}",
                    trace=traceback.format_exc(limit=6),
                )
                continue

            if intent is None:
                continue
            problem = self._validate(intent, ctx)
            if problem:
                self.disable(slot, f"returned an invalid intent: {problem}")
                continue

            slot.signals += 1
            signals.append(Signal(strategy_id=slot.id, variant=slot.variant, intent=intent))
        return signals

    def manage(self, key: str, ctx, position, initial_stop: float | None) -> float | None:
        """Ask the strategy that opened this position where its stop should be now.

        Optional: a strategy with no `manage` simply never moves a stop. Failures are isolated the
        same way `on_bar` failures are — a strategy that raises here is disabled rather than
        allowed to take the loop down with it — and the position keeps the stop it has, which is
        the safe direction to fail in.
        """
        slot = next((s for s in self.slots if s.key == key), None)
        if slot is None or not slot.enabled:
            return None
        hook = getattr(slot.strategy, "manage", None)
        if hook is None:
            return None
        try:
            proposed = hook(ctx, position, initial_stop)
        except Exception as e:  # noqa: BLE001 - isolation is the point of this class
            self.disable(slot, f"manage() raised: {type(e).__name__}: {e}", trace=traceback.format_exc(limit=6))
            return None
        if proposed is None:
            return None
        if not isinstance(proposed, int | float) or isinstance(proposed, bool) or proposed <= 0:
            self.disable(slot, f"manage() returned {proposed!r}, which is not a price")
            return None
        return float(proposed)

    @staticmethod
    def _validate(intent: Any, ctx) -> str | None:
        if not isinstance(intent, Intent):
            return f"expected Intent or None, got {type(intent).__name__}"
        if intent.symbol != ctx.symbol:
            return f"intent is for {intent.symbol} but the bar is {ctx.symbol}"
        return None

    # --- enable / disable ---------------------------------------------------------

    def disable(self, slot: Slot | str, reason: str, trace: str | None = None) -> Slot | None:
        target = slot if isinstance(slot, Slot) else self.slot(slot)
        if target is None:
            return None
        target.enabled = False
        target.error = reason
        self._event(
            "CRIT",
            f"strategy disabled: {target.key}",
            {"reason": reason, "trace": trace, "calls": target.calls},
        )
        return target

    def enable(self, key: str) -> Slot | None:
        target = self.slot(key)
        if target is None:
            return None
        target.enabled = True
        target.error = None
        self._event("WARN", f"strategy re-enabled: {target.key}", None)
        return target

    # --- reporting ----------------------------------------------------------------

    def status(self) -> list[dict]:
        return [
            {
                "key": s.key,
                "enabled": s.enabled,
                "calls": s.calls,
                "signals": s.signals,
                "error": s.error,
                "symbols": list(s.strategy.symbols),
                "timeframe": getattr(s.strategy, "timeframe", None),
            }
            for s in self.slots
        ]

    def _event(self, severity: str, message: str, data: dict | None) -> None:
        if self.journal is not None:
            self.journal.event(severity, SOURCE, message, data)
