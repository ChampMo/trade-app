"""Journal schema (rule 05). Timestamps are naive UTC (D13).

Tables:
  events      everything the system does (severity INFO/WARN/CRIT, source core/mt5/exec/risk/ai/reconcile/kill/smoke)
  orders      every order request and its result, open/close/modify, with slippage and SL verification
  decisions   one row per strategy decision incl. rejected/blocked ones (filled from Phase 1)
  ai_calls    raw prompt + response of every LLM call (filled from Phase 3)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA_VERSION = 2  # 2 adds the `state` table (D21)


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    applied_utc: Mapped[datetime] = mapped_column(DateTime)


class State(Base):
    """Small durable key/value store for facts that must survive a restart (D21).

    Peak equity is the important one: if it reset on restart, the 30% drawdown limit would be
    measured against today's balance instead of the real high-water mark, and closing the app
    would silently erase the drawdown history the limit depends on.
    """

    __tablename__ = "state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_utc: Mapped[datetime] = mapped_column(DateTime)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime, index=True)
    severity: Mapped[str] = mapped_column(String(8), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime, index=True)
    client_ref: Mapped[str] = mapped_column(String(64), index=True)  # groups open/modify/close of one trade
    kind: Mapped[str] = mapped_column(String(8))  # open | close | modify
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    magic: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    comment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price_requested: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_filled: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean)
    retcode: Mapped[int] = mapped_column(Integer)
    retcode_desc: Mapped[str] = mapped_column(String(64))
    order_ticket: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deal_ticket: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position_ticket: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    slippage_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    raw_request: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    raw_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime, index=True)
    strategy_id: Mapped[str] = mapped_column(String(32), index=True)
    variant: Mapped[str | None] = mapped_column(String(8), nullable=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # bars / indicators seen
    ai_regime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_bias: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_size_mult: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_block: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ai_call_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verdict: Mapped[str] = mapped_column(String(16), index=True)  # APPROVED | REJECTED | NO_INTENT | BLOCKED
    verdict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_lots: Mapped[float | None] = mapped_column(Float, nullable=True)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tag: Mapped[str | None] = mapped_column(String(16), nullable=True)  # variance | execution | regime | bug
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AICall(Base):
    __tablename__ = "ai_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime, index=True)
    agent: Mapped[str] = mapped_column(String(16), index=True)  # scout | analyst | reviewer
    model: Mapped[str] = mapped_column(String(48))
    prompt: Mapped[str] = mapped_column(Text)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    schema_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
