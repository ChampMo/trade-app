"""Reconcile: ask the broker what is actually open, and compare it to what we think.

The rule this enforces is simple and absolute — **the broker is the truth**. Our own files are a
record of what we believe, and a system that trusts its own bookkeeping over the account is one
crash away from trading against a position it does not know it has.

The two directions are not symmetrical, and treating them the same is the mistake to avoid:

- **Orphan** — the broker has one of our positions that the ledger never recorded. This is money at
  risk that nothing is managing. It freezes new entries and is reported as a mismatch.
- **Ghost** — the ledger thinks a position is open and the broker says it is gone. Almost always
  this is a stop or a target doing exactly its job, which is normal and must not raise an alarm or
  stop trading. It is recorded as closed and noted, nothing more.

A naive reconcile that shouts about both would cry wolf every time a stop is hit, and would be
switched off within a week. This one stays quiet about the normal case so its alarm still means
something.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tradeapp.contracts import Position
from tradeapp.journal import Journal

SOURCE = "reconcile"


@dataclass(frozen=True)
class ReconcileResult:
    at_utc: datetime
    matched: list[int] = field(default_factory=list)
    orphans: list[Position] = field(default_factory=list)
    ghosts: list[int] = field(default_factory=list)
    unprotected: list[Position] = field(default_factory=list)
    foreign: list[Position] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when nothing needs a human. Ghosts are normal; orphans and errors are not."""
        return self.error is None and not self.orphans

    @property
    def mismatch(self) -> str | None:
        """What to hand `SystemHealth.reconcile_mismatch`. None when there is nothing to report."""
        if self.error:
            return f"cannot read positions from the broker: {self.error}"
        if self.orphans:
            tickets = ", ".join(str(p.ticket) for p in self.orphans)
            return f"broker has {len(self.orphans)} position(s) the journal never recorded: {tickets}"
        return None

    @property
    def unprotected_tickets(self) -> tuple[int, ...]:
        """Feeds `SystemHealth.positions_without_stop`, which is a kill trigger (rule 03, D12a)."""
        return tuple(p.ticket for p in self.unprotected)

    def summary(self) -> str:
        if self.error:
            return f"reconcile failed: {self.error}"
        bits = [f"{len(self.matched)} matched"]
        for name, items in (
            ("orphan", self.orphans),
            ("ghost", self.ghosts),
            ("unprotected", self.unprotected),
            ("foreign", self.foreign),
        ):
            if items:
                bits.append(f"{len(items)} {name}")
        return ", ".join(bits)


class Reconciler:
    def __init__(
        self,
        broker: Any,
        journal: Journal,
        *,
        magics: set[int] | Callable[[], set[int]] | None = None,
        record_ghosts: bool = True,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """`magics` says which positions are ours.

        Left as None, every position on the account is treated as ours, which is right for a
        dedicated bot account (D4, D8). Pass the set of magic numbers to share an account with
        manual trading; anything outside it is then reported but never acted on.
        """
        self.broker = broker
        self.journal = journal
        self._magics = magics
        self.record_ghosts = record_ghosts
        self._now = now
        self.frozen = False
        self.freeze_reason: str | None = None
        self.last: ReconcileResult | None = None

    def _ours(self, pos: Position) -> bool:
        if self._magics is None:
            return True
        magics = self._magics() if callable(self._magics) else self._magics
        return pos.magic in magics

    def run(self) -> ReconcileResult:
        at = self._now()
        try:
            live = self.broker.positions()
        except Exception as e:  # noqa: BLE001 - an unreadable broker is itself the finding
            result = ReconcileResult(at_utc=at, error=f"{type(e).__name__}: {e}")
            self._freeze(result.mismatch)
            self.journal.event("CRIT", SOURCE, "cannot read positions from the broker", {"error": str(e)})
            self.last = result
            return result

        ours = [p for p in live if self._ours(p)]
        foreign = [p for p in live if not self._ours(p)]
        believed = self.journal.open_position_tickets()
        live_tickets = {p.ticket for p in ours}

        orphans = [p for p in ours if p.ticket not in believed]
        ghosts = sorted(believed - live_tickets)
        unprotected = [p for p in ours if p.sl <= 0]
        matched = sorted(live_tickets & believed)

        result = ReconcileResult(
            at_utc=at,
            matched=matched,
            orphans=orphans,
            ghosts=ghosts,
            unprotected=unprotected,
            foreign=foreign,
            error=None,
        )

        self._report(result)
        if self.record_ghosts and ghosts:
            self._close_ghosts(ghosts)

        if result.orphans:
            self._freeze(result.mismatch)
        else:
            self._unfreeze()

        self.last = result
        return result

    # --- reporting ----------------------------------------------------------------

    def _report(self, result: ReconcileResult) -> None:
        for pos in result.orphans:
            self.journal.event(
                "CRIT",
                SOURCE,
                f"orphan position at the broker: {pos.ticket}",
                {
                    "ticket": pos.ticket,
                    "symbol": pos.symbol,
                    "side": pos.side.value,
                    "volume": pos.volume,
                    "sl": pos.sl,
                    "magic": pos.magic,
                    "why": "money at risk that the journal never recorded; a timed-out order may have filled",
                },
            )
        for pos in result.unprotected:
            self.journal.event(
                "CRIT",
                SOURCE,
                f"position {pos.ticket} has no stop at the broker",
                {"ticket": pos.ticket, "symbol": pos.symbol, "volume": pos.volume, "rule": "03"},
            )
        for pos in result.foreign:
            self.journal.event(
                "WARN",
                SOURCE,
                f"position {pos.ticket} is not ours (magic {pos.magic}); leaving it alone",
                {"ticket": pos.ticket, "symbol": pos.symbol, "magic": pos.magic},
            )
        if result.ok and not result.ghosts and not result.foreign:
            self.journal.event("INFO", SOURCE, f"reconcile ok: {result.summary()}", {"matched": result.matched})

    def _close_ghosts(self, ghosts: list[int]) -> None:
        """A position that vanished at the broker has almost certainly hit its stop or target.

        Record the close so the ledger agrees with the account and the next run stays quiet. This
        is bookkeeping, not an alarm: `orders` gets a close row marked CLOSED_ELSEWHERE, which also
        makes it obvious in the journal browser that the system did not place this exit itself.
        """
        for ticket in ghosts:
            self.journal.order(
                client_ref=f"reconcile-{ticket}",
                kind="close",
                symbol="",
                ok=True,
                retcode=0,
                retcode_desc="CLOSED_ELSEWHERE",
                position_ticket=ticket,
            )
            self.journal.event(
                "INFO",
                SOURCE,
                f"position {ticket} closed at the broker without us; recorded",
                {"ticket": ticket, "likely": "stop loss, take profit, or a manual close"},
            )

    # --- freeze -------------------------------------------------------------------

    def _freeze(self, reason: str | None) -> None:
        if not self.frozen:
            self.journal.event("CRIT", SOURCE, "new entries frozen until this is resolved", {"reason": reason})
        self.frozen = True
        self.freeze_reason = reason

    def _unfreeze(self) -> None:
        if self.frozen:
            self.journal.event("WARN", SOURCE, "reconcile clean again; new entries unfrozen", None)
        self.frozen = False
        self.freeze_reason = None

    # --- what the kill switch reads -----------------------------------------------

    def health_inputs(self) -> dict:
        """Merge into `SystemHealth(**...)` so the kill switch sees what reconcile found."""
        if self.last is None:
            return {}
        return {
            "reconcile_mismatch": self.last.mismatch,
            "positions_without_stop": self.last.unprotected_tickets,
        }
