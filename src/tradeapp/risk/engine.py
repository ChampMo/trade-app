"""The Risk Engine: the only path from an Intent to an order (CLAUDE.md rule 02).

Strategies say what they want. This decides whether it happens, how big it is, and writes down
why. Nothing else in the system may build an OrderRequest, which is what makes one place worth
testing exhaustively and auditing later.

Every call produces a journal row, approved or rejected. The rejections are the valuable ones:
they are the evidence that a limit did its job, and they are what a post-mortem reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from tradeapp.contracts import AccountInfo, Intent, OrderRequest, Position, Side, SymbolInfo, Tick
from tradeapp.journal import Journal
from tradeapp.risk.correlation import correlated_units
from tradeapp.risk.limits import AIContext, EngineState, RiskLimits, bias_size_multiplier
from tradeapp.risk.sizing import (
    currency_units,
    estimate_margin,
    lots_for_risk,
    money_at_risk_per_lot,
    net_currency_exposure,
    position_risk,
)


class RejectReason(StrEnum):
    ENGINE_NOT_RUNNING = "engine_not_running"
    FLAT_NOT_SUPPORTED = "flat_not_supported"
    MAX_DRAWDOWN = "max_drawdown"
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    OUTSIDE_TRADING_HOURS = "outside_trading_hours"
    NEWS_BLOCK = "news_block"
    AI_BLOCK = "ai_block"
    SYMBOL_NOT_TRADEABLE = "symbol_not_tradeable"
    UNKNOWN_SYMBOL = "unknown_symbol"
    STOP_WRONG_SIDE = "stop_wrong_side"
    STOP_TOO_CLOSE = "stop_too_close"
    DUPLICATE_POSITION = "duplicate_position"
    MAX_POSITIONS = "max_positions"
    CURRENCY_EXPOSURE = "currency_exposure"
    CORRELATED_EXPOSURE = "correlated_exposure"
    STRATEGY_DAILY_LOSS = "strategy_daily_loss"
    STRATEGY_OPEN_RISK = "strategy_open_risk"
    INSUFFICIENT_MARGIN = "insufficient_margin"
    SIZE_BELOW_MINIMUM = "size_below_minimum"
    MAX_OPEN_RISK = "max_open_risk"
    SIZING_UNAVAILABLE = "sizing_unavailable"


class Verdict(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class MarginQuote(Protocol):
    """The broker's own `order_calc_margin`. None means it would not answer."""

    def __call__(self, symbol: str, side: Side, lots: float, price: float) -> float | None: ...


class NewsBlocker(Protocol):
    """Phase 3 fills this in from the economic calendar. No LLM is involved (D6)."""

    def blocked(self, symbol: str, at: datetime) -> str | None: ...


@dataclass(frozen=True)
class RiskContext:
    """Everything the engine is allowed to look at. No hidden globals, so decisions are replayable."""

    account: AccountInfo
    symbols: dict[str, SymbolInfo]
    tick: Tick
    positions: list[Position]
    now_utc: datetime
    day_start_equity: float
    peak_equity: float
    state: EngineState = EngineState.RUNNING
    ai: AIContext = field(default_factory=AIContext.neutral)
    # Realised money per strategy since the trading day opened, negative for a loss. The core reads
    # it from the journal; an empty dict simply means the budget check has nothing to act on.
    strategy_day_pnl: dict[str, float] = field(default_factory=dict)

    def symbol_info(self, symbol: str) -> SymbolInfo | None:
        return self.symbols.get(symbol)


@dataclass(frozen=True)
class RiskDecision:
    verdict: Verdict
    reason: RejectReason | None
    detail: str
    order: OrderRequest | None = None
    size_lots: float | None = None
    risk_amount: float | None = None
    decision_id: int | None = None
    facts: dict = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.verdict is Verdict.APPROVED


class RiskEngine:
    def __init__(
        self,
        limits: RiskLimits | None = None,
        *,
        journal: Journal | None = None,
        news: NewsBlocker | None = None,
        magic_base: int = 100_000,
        margin_required: MarginQuote | None = None,
    ) -> None:
        self.limits = limits or RiskLimits()
        self.journal = journal
        self.news = news
        # The broker's own margin arithmetic when we have it; `estimate_margin` is the fallback.
        self.margin_required = margin_required
        self.magic_base = magic_base
        self._magics: dict[str, int] = {}

    # --- identity ----------------------------------------------------------------

    def magic_for(self, strategy_id: str) -> int:
        """Stable per-strategy MT5 magic number, so PnL splits cleanly by strategy and variant."""
        if strategy_id not in self._magics:
            self._magics[strategy_id] = self.magic_base + len(self._magics) + 1
        return self._magics[strategy_id]

    # --- the one entry point -----------------------------------------------------

    def evaluate(self, intent: Intent, strategy_id: str, ctx: RiskContext, variant: str | None = None) -> RiskDecision:
        decision = self._decide(intent, strategy_id, ctx)
        return self._journal(decision, intent, strategy_id, ctx, variant)

    def _decide(self, intent: Intent, strategy_id: str, ctx: RiskContext) -> RiskDecision:
        lim = self.limits
        ai = ctx.ai.effective(ctx.now_utc)

        if ctx.state is not EngineState.RUNNING:
            return _reject(RejectReason.ENGINE_NOT_RUNNING, f"engine is {ctx.state.value}, not accepting intents")

        if intent.side is Side.FLAT:
            return _reject(
                RejectReason.FLAT_NOT_SUPPORTED,
                "FLAT is an exit signal; exits go through the execution layer, not through sizing",
            )

        # --- account-wide gates: these stop everything, so they come first ---
        drawdown_pct = _pct_drop(ctx.account.equity, ctx.peak_equity)
        if drawdown_pct >= lim.max_drawdown_pct:
            return _reject(
                RejectReason.MAX_DRAWDOWN,
                f"drawdown {drawdown_pct:.2f}% at or beyond the {lim.max_drawdown_pct:.2f}% limit",
                facts={"drawdown_pct": round(drawdown_pct, 4), "peak_equity": ctx.peak_equity},
            )

        daily_loss_pct = _pct_drop(ctx.account.equity, ctx.day_start_equity)
        if daily_loss_pct >= lim.daily_loss_limit_pct:
            return _reject(
                RejectReason.DAILY_LOSS_LIMIT,
                f"down {daily_loss_pct:.2f}% today, limit is {lim.daily_loss_limit_pct:.2f}%",
                facts={"daily_loss_pct": round(daily_loss_pct, 4), "day_start_equity": ctx.day_start_equity},
            )

        strategy_pnl = ctx.strategy_day_pnl.get(strategy_id)
        strategy_budget = ctx.account.equity * (lim.strategy_daily_loss_pct / 100.0)
        if strategy_pnl is not None and strategy_pnl <= -strategy_budget:
            return _reject(
                RejectReason.STRATEGY_DAILY_LOSS,
                f"{strategy_id} is down {abs(strategy_pnl):.2f} today, its budget is "
                f"{strategy_budget:.2f} ({lim.strategy_daily_loss_pct:.2f}% of equity); "
                "the account is still fine, this strategy is not",
                facts={"strategy_day_pnl": round(strategy_pnl, 2), "strategy_budget": round(strategy_budget, 2)},
            )

        if not lim.within_trading_hours(ctx.now_utc):
            return _reject(
                RejectReason.OUTSIDE_TRADING_HOURS,
                f"{ctx.now_utc:%H:%M} UTC is outside {lim.trading_start_utc:%H:%M}-{lim.trading_end_utc:%H:%M}",
            )

        if self.news is not None:
            why = self.news.blocked(intent.symbol, ctx.now_utc)
            if why:
                return _reject(RejectReason.NEWS_BLOCK, f"news window: {why}")

        if ai.block:
            return _reject(RejectReason.AI_BLOCK, f"AI layer vetoed trading ({ai.source})")

        # --- symbol and stop sanity ---
        sym = ctx.symbol_info(intent.symbol)
        if sym is None:
            return _reject(RejectReason.UNKNOWN_SYMBOL, f"no symbol info for {intent.symbol}")
        if not sym.trade_allowed:
            return _reject(RejectReason.SYMBOL_NOT_TRADEABLE, f"broker has trading disabled for {intent.symbol}")

        entry = ctx.tick.ask if intent.side is Side.LONG else ctx.tick.bid
        wrong_side = (intent.side is Side.LONG and intent.stop_price >= entry) or (
            intent.side is Side.SHORT and intent.stop_price <= entry
        )
        if wrong_side:
            return _reject(
                RejectReason.STOP_WRONG_SIDE,
                f"{intent.side.value} at {entry} cannot have its stop at {intent.stop_price}",
            )

        stop_distance = abs(entry - intent.stop_price)
        min_distance = (sym.stops_level_points + sym.spread_points + lim.min_stop_buffer_points) * sym.point
        if stop_distance < min_distance:
            return _reject(
                RejectReason.STOP_TOO_CLOSE,
                f"stop is {stop_distance / sym.point:.0f} points away, broker needs at least "
                f"{min_distance / sym.point:.0f} including spread",
                facts={"stop_distance_points": round(stop_distance / sym.point, 1)},
            )

        # --- portfolio gates ---
        if any(p.symbol == intent.symbol and p.magic == self.magic_for(strategy_id) for p in ctx.positions):
            return _reject(
                RejectReason.DUPLICATE_POSITION,
                f"{strategy_id} already holds {intent.symbol}; stacking is not a v0 behaviour",
            )

        if len(ctx.positions) >= lim.max_positions:
            return _reject(
                RejectReason.MAX_POSITIONS,
                f"{len(ctx.positions)} positions open, limit is {lim.max_positions}",
            )

        wanted = currency_units(intent.symbol, intent.side)
        exposure = net_currency_exposure(ctx.positions, wanted)
        over = {c: u for c, u in exposure.items() if abs(u) > lim.max_currency_exposure}
        if over:
            worst = max(over.items(), key=lambda kv: abs(kv[1]))
            return _reject(
                RejectReason.CURRENCY_EXPOSURE,
                f"would leave {worst[1]:+d} units of {worst[0]}, limit is "
                f"{lim.max_currency_exposure} — three correlated trades are one risk, not three",
                facts={"exposure": exposure},
            )

        units, contributors = correlated_units(ctx.positions, intent.symbol, intent.side, floor=lim.correlation_floor)
        if units > lim.max_correlated_units:
            return _reject(
                RejectReason.CORRELATED_EXPOSURE,
                f"this would be {units:.2f} copies of the same bet (limit {lim.max_correlated_units:.2f}); "
                f"correlated with {', '.join(f'{sym} {c:+.2f}' for sym, c in contributors)}",
                facts={"correlated_units": units, "correlated_with": dict(contributors)},
            )

        # --- sizing ---
        bias_mult = bias_size_multiplier(ai.bias, intent.side, lim.opposing_bias_size_penalty)
        size_mult = min(max(ai.size_mult, 0.0), lim.max_size_mult)
        risk_amount = ctx.account.equity * (lim.risk_pct / 100.0) * intent.confidence * size_mult * bias_mult

        try:
            lots = lots_for_risk(risk_amount, stop_distance, sym)
            per_lot = money_at_risk_per_lot(stop_distance, sym)
        except ValueError as e:
            return _reject(RejectReason.SIZING_UNAVAILABLE, str(e))

        if sym.volume_max:
            lots = min(lots, sym.volume_max)
        if lots < sym.volume_min:
            return _reject(
                RejectReason.SIZE_BELOW_MINIMUM,
                f"risk budget {risk_amount:.2f} allows {lots:.4f} lots, below the {sym.volume_min} minimum; "
                "trading the minimum would exceed the limit",
                facts={"risk_amount": round(risk_amount, 2), "wanted_lots": lots},
            )

        actual_risk = lots * per_lot
        open_risk = _open_risk(ctx)
        max_open_risk = ctx.account.equity * (lim.max_open_risk_pct / 100.0)
        if open_risk + actual_risk > max_open_risk:
            return _reject(
                RejectReason.MAX_OPEN_RISK,
                f"open stops already risk {open_risk:.2f}; adding {actual_risk:.2f} passes the "
                f"{max_open_risk:.2f} ceiling ({lim.max_open_risk_pct:.2f}% of equity)",
                facts={"open_risk": round(open_risk, 2), "added_risk": round(actual_risk, 2)},
            )

        magic = self.magic_for(strategy_id)
        strategy_open_risk = _open_risk(ctx, magic=magic)
        strategy_ceiling = ctx.account.equity * (lim.strategy_max_open_risk_pct / 100.0)
        if strategy_open_risk + actual_risk > strategy_ceiling:
            return _reject(
                RejectReason.STRATEGY_OPEN_RISK,
                f"{strategy_id} already has {strategy_open_risk:.2f} at risk; adding {actual_risk:.2f} passes "
                f"its own {strategy_ceiling:.2f} ceiling ({lim.strategy_max_open_risk_pct:.2f}% of equity)",
                facts={"strategy_open_risk": round(strategy_open_risk, 2)},
            )

        margin = self._margin_for(intent.symbol, intent.side, lots, entry, ctx, sym)
        if margin is not None and ctx.account.margin_free is not None:
            allowed = ctx.account.margin_free * (lim.max_margin_use_pct / 100.0)
            if margin > allowed:
                return _reject(
                    RejectReason.INSUFFICIENT_MARGIN,
                    f"{lots} lots needs {margin:.2f} margin, more than the {allowed:.2f} allowed "
                    f"({lim.max_margin_use_pct:.0f}% of {ctx.account.margin_free:.2f} free)",
                    facts={"margin_required": round(margin, 2), "margin_free": ctx.account.margin_free},
                )

        order = OrderRequest(
            symbol=intent.symbol,
            side=intent.side,
            volume=lots,
            stop_price=intent.stop_price,
            take_price=intent.take_price,
            magic=magic,
            comment=strategy_id[:31],
        )
        return RiskDecision(
            verdict=Verdict.APPROVED,
            reason=None,
            detail=(
                f"{lots} lots risking {actual_risk:.2f} "
                f"({lim.risk_pct}% x conf {intent.confidence:.2f} x ai {size_mult:.2f} x bias {bias_mult:.2f})"
            ),
            order=order,
            size_lots=lots,
            risk_amount=round(actual_risk, 2),
            facts={
                "entry": entry,
                "stop_distance_points": round(stop_distance / sym.point, 1),
                "per_lot_risk": round(per_lot, 2),
                "open_risk_before": round(open_risk, 2),
                "size_mult": size_mult,
                "bias_mult": bias_mult,
                "correlated_units": units,
                "strategy_open_risk": round(strategy_open_risk, 2),
                "margin_required": round(margin, 2) if margin is not None else None,
            },
        )

    # --- margin ------------------------------------------------------------------

    def _margin_for(
        self,
        symbol: str,
        side: Side,
        lots: float,
        price: float,
        ctx: RiskContext,
        sym: SymbolInfo,
    ) -> float | None:
        """Ask the broker first; fall back to arithmetic; return None when neither can answer.

        None means the check is skipped, which is the right behaviour: at 0.25% risk on 1:500
        leverage margin is nowhere near binding, and refusing every trade because a number is
        missing would be a far worse failure than the one this check exists to catch.
        """
        if self.margin_required is not None:
            try:
                quoted = self.margin_required(symbol, side, lots, price)
            except Exception:  # noqa: BLE001 - a margin quote never breaks a decision
                quoted = None
            if quoted is not None:
                return quoted
        return estimate_margin(lots, sym, price, ctx.account.currency, ctx.account.leverage)

    # --- journal -----------------------------------------------------------------

    def _journal(
        self,
        decision: RiskDecision,
        intent: Intent,
        strategy_id: str,
        ctx: RiskContext,
        variant: str | None,
    ) -> RiskDecision:
        if self.journal is None:
            return decision
        ai = ctx.ai.effective(ctx.now_utc)
        decision_id = self.journal.decision(
            ts_utc=ctx.now_utc.replace(tzinfo=None),
            strategy_id=strategy_id,
            variant=variant,
            symbol=intent.symbol,
            side=intent.side.value,
            confidence=intent.confidence,
            stop_price=intent.stop_price,
            take_price=intent.take_price,
            reason=intent.reason,
            context={
                "equity": ctx.account.equity,
                "day_start_equity": ctx.day_start_equity,
                "peak_equity": ctx.peak_equity,
                "open_positions": len(ctx.positions),
                "bid": ctx.tick.bid,
                "ask": ctx.tick.ask,
                "server_utc_offset_min": ctx.tick.server_utc_offset_min,
                **decision.facts,
            },
            ai_regime=ai.regime,
            ai_bias=ai.bias,
            ai_size_mult=ai.size_mult,
            ai_block=ai.block,
            verdict=decision.verdict.value,
            verdict_reason=(f"{decision.reason.value}: {decision.detail}" if decision.reason else decision.detail),
            size_lots=decision.size_lots,
        )
        return RiskDecision(**{**decision.__dict__, "decision_id": decision_id})


def _reject(reason: RejectReason, detail: str, facts: dict | None = None) -> RiskDecision:
    return RiskDecision(verdict=Verdict.REJECTED, reason=reason, detail=detail, facts=facts or {})


def _pct_drop(current: float, reference: float) -> float:
    """How far `current` sits below `reference`, in percent. Never negative."""
    if reference <= 0:
        return 0.0
    return max(0.0, (reference - current) / reference * 100.0)


def _open_risk(ctx: RiskContext, magic: int | None = None) -> float:
    """Total account-currency loss if every open stop were hit.

    A position whose stop vanished at the broker is treated as unbounded risk by raising, but the
    engine cannot reject on that alone here: reconcile (P1-08) owns that failure. For v0 the safe
    reading is to skip it and let the missing stop be caught where it is actionable.
    """
    total = 0.0
    for pos in ctx.positions:
        if magic is not None and pos.magic != magic:
            continue
        sym = ctx.symbol_info(pos.symbol)
        if sym is None or pos.sl <= 0:
            continue
        total += position_risk(pos, sym)
    return total


def account_snapshot(account: AccountInfo, positions: list[Position]) -> dict:
    """Small helper for events and reports."""
    return {"equity": account.equity, "balance": account.balance, "positions": len(positions)}
