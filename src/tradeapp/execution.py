"""Execution layer: the only code that opens a position, and the only code that retries.

It takes an `OrderRequest` the Risk Engine already approved and gets it to the broker, or reports
honestly that it could not. Three things live here because they must behave identically everywhere:

- **Retry, but only when the broker said "definitely not filled".** Requote, price changed, price
  off and rate limiting are safe to repeat. A TIMEOUT is not: nobody knows whether that order
  landed, and retrying it can open the position twice. Those go to reconcile (P1-08) instead.
- **Rule 03 after the fill.** A position without a stop at the broker is closed immediately.
  Same code path for the smoke test and for live trading, so the rule cannot drift between them.
- **The counters the kill switch reads.** Consecutive rejects and the time of the last successful
  broker contact are produced here, because this is where the system actually talks to MT5.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tradeapp.broker.mt5_bridge import RETCODE_RETRYABLE
from tradeapp.contracts import OrderRequest, OrderResult, Position, Side
from tradeapp.journal import Journal

SOURCE = "exec"

# Retcodes that mean "the order did not reach the book", so sending again cannot duplicate anything.
# TIMEOUT (10012) is deliberately absent: it is ambiguous, and a retry could open a second position.
RETRYABLE = frozenset(RETCODE_RETRYABLE)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_s: float = 0.2
    retryable: frozenset[int] = RETRYABLE


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    detail: str
    order: OrderRequest | None = None
    result: OrderResult | None = None
    attempts: int = 0
    slippage_points: float | None = None
    position_ticket: int | None = None
    sl_verified: bool | None = None
    order_id: int | None = None
    client_ref: str = ""
    position: Position | None = None
    facts: dict = field(default_factory=dict)


def slippage_points(res: OrderResult, side: Side, point: float, closing: bool = False) -> float | None:
    """Positive means the fill was worse than the price asked for."""
    if res.price_requested is None or res.price_filled is None or point <= 0:
        return None
    diff = (res.price_filled - res.price_requested) / point
    worse_when_higher = (side is Side.LONG) != closing
    return round(diff if worse_when_higher else -diff, 1)


def order_row(journal: Journal, ref: str, kind: str, req: OrderRequest | None, res: OrderResult, **extra: Any) -> int:
    """One journal row per broker interaction, grouped by client_ref."""
    return journal.order(
        client_ref=ref,
        kind=kind,
        symbol=req.symbol if req else extra.pop("symbol", ""),
        side=req.side.value if req else extra.pop("side", None),
        volume=req.volume if req else res.volume,
        magic=req.magic if req else extra.pop("magic", None),
        comment=req.comment if req else kind,
        price_requested=res.price_requested,
        price_filled=res.price_filled,
        sl=req.stop_price if req else extra.pop("sl", None),
        tp=req.take_price if req else extra.pop("tp", None),
        ok=res.ok,
        retcode=res.retcode,
        retcode_desc=res.retcode_desc,
        order_ticket=res.order_ticket,
        deal_ticket=res.deal_ticket,
        position_ticket=res.position_ticket,
        raw_request=res.raw.get("request") if res.raw else None,
        raw_result=res.raw.get("result") if res.raw else None,
        **extra,
    )


def _failed(retcode: int, desc: str) -> OrderResult:
    return OrderResult(ok=False, retcode=retcode, retcode_desc=desc)


class Executor:
    def __init__(
        self,
        broker: Any,
        journal: Journal,
        *,
        policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.broker = broker
        self.journal = journal
        self.policy = policy or RetryPolicy()
        self._sleep = sleep
        self._now = now
        # Read by the kill switch through SystemHealth (P1-05b).
        self.consecutive_rejects = 0
        self.last_broker_contact_utc: datetime | None = None

    # --- health the kill switch reads --------------------------------------------

    def _contacted(self) -> None:
        self.last_broker_contact_utc = self._now()

    def _record_outcome(self, ok: bool) -> None:
        self.consecutive_rejects = 0 if ok else self.consecutive_rejects + 1

    # --- opening ------------------------------------------------------------------

    def send(
        self,
        order: OrderRequest,
        *,
        client_ref: str | None = None,
        decision_id: int | None = None,
        point: float | None = None,
        verify_stop: bool = True,
    ) -> ExecutionResult:
        """Place an approved order, retry the safe failures, then prove the stop is at the broker."""
        ref = client_ref or f"x-{uuid.uuid4().hex[:8]}"
        res, attempts = self._send_with_retry(order)
        slip = slippage_points(res, order.side, point) if point else None
        order_id = order_row(self.journal, ref, "open", order, res, slippage_points=slip)
        if decision_id is not None:
            # Close the loop both ways: the decision row now points at the order it produced,
            # which is what the journal browser walks when you click a trade.
            self.journal.update_decision(decision_id, order_id=order_id)
        self._record_outcome(res.ok)

        if not res.ok:
            self.journal.event(
                "WARN",
                SOURCE,
                f"order rejected after {attempts} attempt(s): {res.retcode_desc}",
                {"symbol": order.symbol, "consecutive_rejects": self.consecutive_rejects, "ref": ref},
            )
            return ExecutionResult(
                ok=False,
                detail=f"{res.retcode_desc} after {attempts} attempt(s)",
                order=order,
                result=res,
                attempts=attempts,
                client_ref=ref,
                order_id=order_id,
            )

        ticket = res.position_ticket
        verified: bool | None = None
        position: Position | None = None
        if verify_stop:
            verified, position = self.verify_stop(ticket, order.stop_price, order.take_price, ref)
            self.journal.update_order(order_id, sl_verified=verified)
            if not verified:
                return ExecutionResult(
                    ok=False,
                    detail="stop could not be placed at the broker; position closed (rule 03)",
                    order=order,
                    result=res,
                    attempts=attempts,
                    slippage_points=slip,
                    position_ticket=ticket,
                    sl_verified=False,
                    order_id=order_id,
                    client_ref=ref,
                )

        self.journal.event(
            "INFO",
            SOURCE,
            f"filled {order.volume} {order.symbol} {order.side.value} at {res.price_filled}",
            {
                "ticket": ticket,
                "slippage_points": slip,
                "attempts": attempts,
                "ref": ref,
                "decision_id": decision_id,
            },
        )
        return ExecutionResult(
            ok=True,
            detail=f"filled at {res.price_filled}",
            order=order,
            result=res,
            attempts=attempts,
            slippage_points=slip,
            position_ticket=ticket,
            sl_verified=verified,
            order_id=order_id,
            client_ref=ref,
            position=position,
        )

    def _send_with_retry(self, order: OrderRequest) -> tuple[OrderResult, int]:
        last: OrderResult = _failed(-1, "NOT_SENT")
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                last = self.broker.market_order(order)
                self._contacted()
            except Exception as e:  # noqa: BLE001 - a raising bridge is a rejected order, not a crash
                last = _failed(-1, f"{type(e).__name__}: {e}")
                self.journal.event("WARN", SOURCE, "broker raised while sending", {"attempt": attempt, "error": str(e)})
                return last, attempt
            if last.ok or last.retcode not in self.policy.retryable:
                return last, attempt
            if attempt < self.policy.max_attempts:
                self.journal.event(
                    "INFO",
                    SOURCE,
                    f"retrying after {last.retcode_desc}",
                    {"attempt": attempt, "symbol": order.symbol},
                )
                self._sleep(self.policy.backoff_s * attempt)
        return last, self.policy.max_attempts

    # --- rule 03 ------------------------------------------------------------------

    def verify_stop(self, ticket: int | None, sl: float, tp: float | None, ref: str) -> tuple[bool, Position | None]:
        """The stop must exist at the broker. Try once to set it; otherwise close the position now."""
        if ticket is None:
            self.journal.event("CRIT", SOURCE, "fill reported no position ticket", {"ref": ref})
            return False, None

        pos = (
            self.broker.wait_position(ticket) if hasattr(self.broker, "wait_position") else self.broker.position(ticket)
        )
        self._contacted()
        if pos is None:
            self.journal.event("CRIT", SOURCE, "position not found after fill", {"ticket": ticket, "ref": ref})
            return False, None
        if pos.sl > 0:
            self.journal.event("INFO", SOURCE, "SL verified at broker", {"ticket": ticket, "sl": pos.sl})
            return True, pos

        self.journal.event("WARN", SOURCE, "position filled without SL; setting it now", {"ticket": ticket, "sl": sl})
        res = self.broker.modify_sltp(ticket, sl, tp)
        self._contacted()
        order_row(
            self.journal,
            ref,
            "modify",
            None,
            res,
            symbol=pos.symbol,
            side=pos.side.value,
            magic=pos.magic,
            sl=sl,
            tp=tp,
        )
        pos = self.broker.position(ticket)
        if res.ok and pos is not None and pos.sl > 0:
            self.journal.event("INFO", SOURCE, "SL set and verified at broker", {"ticket": ticket, "sl": pos.sl})
            return True, pos

        self.journal.event(
            "CRIT",
            SOURCE,
            "cannot set SL at broker; closing position now (rule 03)",
            {"ticket": ticket, "retcode": res.retcode_desc, "ref": ref},
        )
        self.close(ticket, client_ref=ref, reason="no stop at broker")
        return False, None

    # --- closing ------------------------------------------------------------------

    def close(
        self,
        ticket: int,
        *,
        client_ref: str | None = None,
        point: float | None = None,
        reason: str = "",
    ) -> ExecutionResult:
        ref = client_ref or f"x-{uuid.uuid4().hex[:8]}"
        pos = self.broker.position(ticket)
        self._contacted()
        side = pos.side if pos else None

        try:
            res = self.broker.close_position(ticket)
            self._contacted()
        except Exception as e:  # noqa: BLE001
            res = _failed(-1, f"{type(e).__name__}: {e}")

        slip = slippage_points(res, side, point, closing=True) if (point and side) else None
        order_row(
            self.journal,
            ref,
            "close",
            None,
            res,
            symbol=pos.symbol if pos else "",
            side=side.value if side else None,
            magic=pos.magic if pos else None,
            slippage_points=slip,
        )
        self._record_outcome(res.ok)
        self.journal.event(
            "INFO" if res.ok else "WARN",
            SOURCE,
            f"close {'ok' if res.ok else 'failed'} for {ticket}" + (f": {reason}" if reason else ""),
            {"ticket": ticket, "retcode": res.retcode_desc, "slippage_points": slip, "ref": ref},
        )
        return ExecutionResult(
            ok=res.ok,
            detail=res.retcode_desc,
            result=res,
            attempts=1,
            slippage_points=slip,
            position_ticket=ticket,
            client_ref=ref,
        )
