"""Journal store: one SQLite file per profile, written through SQLAlchemy sessions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from tradeapp.journal.models import SCHEMA_VERSION, AICall, Base, Decision, Event, Order, SchemaVersion

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
            path = Path(db_path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            self.engine = create_engine(f"sqlite:///{path.as_posix()}", echo=echo)
            self.path = path
        Base.metadata.create_all(self.engine)
        self._session = sessionmaker(self.engine, expire_on_commit=False)
        self._ensure_version()

    # --- schema -----------------------------------------------------------------

    def _ensure_version(self) -> None:
        with self._session() as s:
            row = s.get(SchemaVersion, 1)
            if row is None:
                s.add(SchemaVersion(id=1, version=SCHEMA_VERSION, applied_utc=utcnow()))
                s.commit()
            elif row.version != SCHEMA_VERSION:
                raise RuntimeError(
                    f"journal schema version {row.version} != code {SCHEMA_VERSION}; migrate before running"
                )

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

    # --- reads -------------------------------------------------------------------

    def tail_events(self, n: int = 20) -> list[Event]:
        with self._session() as s:
            rows = s.execute(select(Event).order_by(Event.id.desc()).limit(n)).scalars().all()
            return list(reversed(rows))

    def orders_for(self, client_ref: str) -> list[Order]:
        with self._session() as s:
            return list(s.execute(select(Order).where(Order.client_ref == client_ref).order_by(Order.id)).scalars())

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
