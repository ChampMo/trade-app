from pathlib import Path

import pytest

from tradeapp.journal import Journal
from tradeapp.journal.models import SCHEMA_VERSION


def test_schema_version_written(journal: Journal):
    assert journal.schema_version == SCHEMA_VERSION


def test_event_roundtrip(journal: Journal):
    journal.event("INFO", "core", "hello", {"a": 1})
    journal.event("WARN", "mt5", "reconnect", None)
    rows = journal.tail_events(10)
    assert [r.message for r in rows] == ["hello", "reconnect"]
    assert rows[0].data == {"a": 1}
    assert rows[0].ts_utc.tzinfo is None  # naive UTC by decision D13


def test_event_rejects_unknown_severity(journal: Journal):
    with pytest.raises(ValueError):
        journal.event("DEBUG", "core", "nope")


def test_order_update_and_lookup(journal: Journal):
    oid = journal.order(
        client_ref="r1",
        kind="open",
        symbol="EURUSD",
        side="LONG",
        volume=0.01,
        ok=True,
        retcode=10009,
        retcode_desc="DONE",
    )
    journal.update_order(oid, sl_verified=True, slippage_points=0.4)
    rows = journal.orders_for("r1")
    assert len(rows) == 1 and rows[0].sl_verified is True and rows[0].slippage_points == 0.4


def test_file_journal_persists(tmp_path: Path):
    db = tmp_path / "nested" / "journal.db"
    j = Journal(db)
    j.event("INFO", "core", "persisted")
    j.close()
    assert db.exists()
    j2 = Journal(db)
    assert [e.message for e in j2.tail_events(5)] == ["persisted"]
    j2.close()
