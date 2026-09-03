"""Journal store: one SQLite file per profile, written through SQLAlchemy sessions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from tradeapp.config import resolve_data_path
from tradeapp.journal.models import (
    SCHEMA_VERSION,
    AICall,
    BacktestRun,
    Base,
    Decision,
    Event,
    Order,
    SchemaVersion,
    State,
)

SEVERITIES = ("INFO", "WARN", "CRIT")


def utcnow() -> datetime:
    """Naive UTC now (D13). Every journal row uses this."""
    return datetime.now(UTC).replace(tzinfo=None)


class Journal:
    def __init__(self, db_path: str | Path | None = None, echo: bool = False) -> None:
        if db_path is None or str(db_path) == ":memory:":
            self.engine = create_engine(
                "sqlite:///:memory:",
                echo=echo,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            self.path: Path | None = None
        else:
            # The record must live in one place whatever directory the core was started from:
            # peak equity lives here so a restart cannot erase the drawdown history (D21).
            path = resolve_data_path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.engine = create_engine(f"sqlite:///{path.as_posix()}", echo=echo)
            self.path = path
        Base.metadata.create_all(self.engine)
        self._session = sessionmaker(self.engine, expire_on_commit=False)
        self._ensure_version()

    # --- schema -----------------------------------------------------------------

    def _ensure_version(self) -> None:
        """Additive upgrades apply themselves; a journal from newer code is refused.

        `create_all` above has already added any missing tables, so stepping the recorded version
        forward is the whole migration for additive changes. Running older code against a newer
        journal is the dangerous direction and stops here.
        """
        with self._session() as s:
            row = s.get(SchemaVersion, 1)
            if row is None:
                s.add(SchemaVersion(id=1, version=SCHEMA_VERSION, applied_utc=utcnow()))
                s.commit()
                return
            if row.version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"journal was written by schema version {row.version} but this code is {SCHEMA_VERSION}; "
                    "upgrade the code rather than downgrading the journal"
                )
            if row.version < SCHEMA_VERSION:
                previous = row.version
                row.version = SCHEMA_VERSION
                row.applied_utc = utcnow()
                s.add(
                    Event(
                        ts_utc=utcnow(),
                        severity="INFO",
                        source="journal",
                        message=f"journal schema upgraded {previous} -> {SCHEMA_VERSION}",
                        data={"from": previous, "to": SCHEMA_VERSION},
                    )
                )
                s.commit()

    @property
    def schema_version(self) -> int:
        with self._session() as s:
            return s.get(SchemaVersion, 1).version

    # --- writes ------------------------------------------------------------------

    def event(self, severity: str, source: str, message: str, data: dict[str, Any] | None = None) -> int:
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")
        with self._session() as s:
            row = Event(ts_utc=utcnow(), severity=severity, source=source, message=message, data=data)
            s.add(row)
            s.commit()
            return row.id

    def order(self, **fields: Any) -> int:
        fields.setdefault("ts_utc", utcnow())
        with self._session() as s:
            row = Order(**fields)
            s.add(row)
            s.commit()
            return row.id

    def update_order(self, order_id: int, **fields: Any) -> None:
        with self._session() as s:
            row = s.get(Order, order_id)
            if row is None:
                raise KeyError(order_id)
            for k, v in fields.items():
                setattr(row, k, v)
            s.commit()

    def decision(self, **fields: Any) -> int:
        fields.setdefault("ts_utc", utcnow())
        with self._session() as s:
            row = Decision(**fields)
            s.add(row)
            s.commit()
            return row.id

    def update_decision(self, decision_id: int, **fields: Any) -> None:
        """Used to link a decision to the order it produced, once execution knows the order row."""
        with self._session() as s:
            row = s.get(Decision, decision_id)
            if row is None:
                raise KeyError(decision_id)
            for k, v in fields.items():
                setattr(row, k, v)
            s.commit()

    def ai_call(self, **fields: Any) -> int:
        fields.setdefault("ts_utc", utcnow())
        with self._session() as s:
            row = AICall(**fields)
            s.add(row)
            s.commit()
            return row.id

    def update_ai_call(self, call_id: int | None, **fields: Any) -> None:
        """Fill in whether the reply parsed, once the caller knows."""
        if call_id is None:
            return
        with self._session() as s:
            row = s.get(AICall, call_id)
            if row is None:
                raise KeyError(call_id)
            for k, v in fields.items():
                setattr(row, k, v)
            s.commit()

    def backtest(self, **fields: Any) -> int:
        """Store one backtest run. Research that is not written down is research that repeats."""
        fields.setdefault("ts_utc", utcnow())
        with self._session() as s:
            row = BacktestRun(**fields)
            s.add(row)
            s.commit()
            return row.id

    # --- reads -------------------------------------------------------------------

    def backtest_runs(self, limit: int = 20, strategy: str | None = None) -> list[BacktestRun]:
        with self._session() as s:
            q = select(BacktestRun).order_by(BacktestRun.id.desc()).limit(limit)
            if strategy:
                q = select(BacktestRun).where(BacktestRun.strategy == strategy).order_by(BacktestRun.id.desc())
                q = q.limit(limit)
            return list(s.execute(q).scalars())

    def backtest_run(self, run_id: int) -> BacktestRun | None:
        with self._session() as s:
            return s.get(BacktestRun, run_id)

    def latest_backtest(self, strategy: str) -> BacktestRun | None:
        runs = self.backtest_runs(limit=1, strategy=strategy)
        return runs[0] if runs else None

    def tail_events(self, n: int = 20) -> list[Event]:
        with self._session() as s:
            rows = s.execute(select(Event).order_by(Event.id.desc()).limit(n)).scalars().all()
            return list(reversed(rows))

    def original_stop(self, position_ticket: int) -> float | None:
        """The stop this position was opened with, not the one it has now.

        A trail moves the stop, and after that the distance from entry no longer says what was
        risked — so break-even and R multiples read the original from here. It comes off the
        opening order row, which means it survives a restart in the middle of a trade.
        """
        with self._session() as s:
            row = (
                s.execute(
                    select(Order)
                    .where(Order.position_ticket == position_ticket, Order.kind == "open", Order.ok.is_(True))
                    .order_by(Order.id)
                    .limit(1)
                )
                .scalars()
                .first()
            )
        return row.sl if row and row.sl else None

    def orders_for(self, client_ref: str) -> list[Order]:
        with self._session() as s:
            return list(s.execute(select(Order).where(Order.client_ref == client_ref).order_by(Order.id)).scalars())

    # --- durable state (D21) -----------------------------------------------------

    def get_state(self, key: str, default: Any = None) -> Any:
        with self._session() as s:
            row = s.get(State, key)
            return row.value.get("v", default) if row else default

    def set_state(self, key: str, value: Any) -> None:
        with self._session() as s:
            row = s.get(State, key)
            if row is None:
                s.add(State(key=key, value={"v": value}, updated_utc=utcnow()))
            else:
                row.value = {"v": value}
                row.updated_utc = utcnow()
            s.commit()

    def open_position_tickets(self) -> set[int]:
        """Positions the journal believes are still open: opened successfully, never closed successfully.

        Derived from the ledger rather than kept as state, so it cannot drift out of sync with the
        rows it is supposed to summarise.
        """
        with self._session() as s:
            opened = s.execute(
                select(Order.position_ticket).where(
                    Order.kind == "open", Order.ok.is_(True), Order.position_ticket.is_not(None)
                )
            ).scalars()
            closed = s.execute(
                select(Order.position_ticket).where(
                    Order.kind == "close", Order.ok.is_(True), Order.position_ticket.is_not(None)
                )
            ).scalars()
            return set(opened) - set(closed)

    def events_since(self, after_id: int = 0, limit: int = 200) -> list[Event]:
        """Events newer than an id, oldest first. The websocket walks forward with this."""
        with self._session() as s:
            return list(s.execute(select(Event).where(Event.id > after_id).order_by(Event.id).limit(limit)).scalars())

    def recent_decisions(self, limit: int = 100) -> list[Decision]:
        with self._session() as s:
            rows = s.execute(select(Decision).order_by(Decision.id.desc()).limit(limit)).scalars().all()
            return list(reversed(rows))

    def orders_recent(self, limit: int = 100) -> list[Order]:
        with self._session() as s:
            rows = s.execute(select(Order).order_by(Order.id.desc()).limit(limit)).scalars().all()
            return list(reversed(rows))

    def events_where(self, severity: str | None = None, source: str | None = None) -> list[Event]:
        with self._session() as s:
            q = select(Event).order_by(Event.id)
            if severity:
                q = q.where(Event.severity == severity)
            if source:
                q = q.where(Event.source == source)
            return list(s.execute(q).scalars())

    def session(self) -> Session:
        """Escape hatch for reports and tests."""
        return self._session()

    def close(self) -> None:
        self.engine.dispose()
