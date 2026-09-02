"""Historical bar store (P2-01).

A plain SQLite file, one row per bar, keyed by (symbol, timeframe, time). Not parquet: the whole
project has stayed dependency-light on purpose, and a backtest on H4 reads a few thousand rows.
The file opens in DB Browser like the journal does, which matters when a backtest result looks
wrong and the first question is "what did the data actually say".

Two things this does that a naive cache does not:

- **Incremental sync.** Bars are upserted by time, so re-syncing overlaps is free and safe. Broker
  history is also revised occasionally; a later fetch overwrites an earlier one rather than
  duplicating it.
- **Honest gaps.** A gap over a weekend is the market being shut, not missing data. Reporting
  those as holes would bury the real ones, so weekends are recognised and excluded.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tradeapp.contracts import TF, Bar

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol     TEXT    NOT NULL,
    timeframe  TEXT    NOT NULL,
    ts         INTEGER NOT NULL,   -- epoch seconds, real UTC (D13)
    open       REAL    NOT NULL,
    high       REAL    NOT NULL,
    low        REAL    NOT NULL,
    close      REAL    NOT NULL,
    volume     REAL    NOT NULL DEFAULT 0,
    spread     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, timeframe, ts)
);
"""


@dataclass(frozen=True)
class Gap:
    start_utc: datetime
    end_utc: datetime
    missing_bars: int

    def __str__(self) -> str:
        return f"{self.start_utc:%Y-%m-%d %H:%M} -> {self.end_utc:%Y-%m-%d %H:%M} ({self.missing_bars} bars)"


@dataclass(frozen=True)
class SyncReport:
    symbol: str
    timeframe: TF
    fetched: int
    stored: int
    first_utc: datetime | None
    last_utc: datetime | None
    total: int

    def __str__(self) -> str:
        span = f"{self.first_utc:%Y-%m-%d} to {self.last_utc:%Y-%m-%d}" if self.first_utc else "empty"
        return f"{self.symbol} {self.timeframe.value}: +{self.stored} of {self.fetched} fetched, {self.total} total, {span}"


def _to_epoch(moment: datetime) -> int:
    return int((moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp())


def _from_epoch(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


class BarStore:
    def __init__(self, path: str | Path = "data/history.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con:
            con.executescript(SCHEMA)
            con.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    # --- writing ------------------------------------------------------------------

    def upsert(self, symbol: str, timeframe: TF, bars: Sequence[Bar]) -> int:
        """Insert or replace. Returns how many rows the file gained, not how many were written."""
        if not bars:
            return 0
        before = self.count(symbol, timeframe)
        rows = [
            (
                symbol,
                timeframe.value,
                _to_epoch(b.time_utc),
                b.open,
                b.high,
                b.low,
                b.close,
                b.volume,
                b.spread_points,
            )
            for b in bars
        ]
        with closing(self._connect()) as con:
            con.executemany(
                "INSERT OR REPLACE INTO bars (symbol, timeframe, ts, open, high, low, close, volume, spread) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            con.commit()
        return self.count(symbol, timeframe) - before

    def sync_from_broker(self, broker, symbol: str, timeframe: TF, count: int = 5000) -> SyncReport:
        """Pull the most recent `count` closed bars and merge them in."""
        bars = broker.bars(symbol, timeframe, count)
        stored = self.upsert(symbol, timeframe, bars)
        first, last = self.range(symbol, timeframe)
        return SyncReport(
            symbol=symbol,
            timeframe=timeframe,
            fetched=len(bars),
            stored=stored,
            first_utc=first,
            last_utc=last,
            total=self.count(symbol, timeframe),
        )

    # --- reading ------------------------------------------------------------------

    def load(
        self,
        symbol: str,
        timeframe: TF,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[Bar]:
        sql = "SELECT ts, open, high, low, close, volume, spread FROM bars WHERE symbol = ? AND timeframe = ?"
        params: list = [symbol, timeframe.value]
        if start is not None:
            sql += " AND ts >= ?"
            params.append(_to_epoch(start))
        if end is not None:
            sql += " AND ts <= ?"
            params.append(_to_epoch(end))
        sql += " ORDER BY ts"
        if limit is not None:
            # take the LAST `limit` bars of the range, which is what "recent history" means
            sql = f"SELECT * FROM ({sql} DESC LIMIT {int(limit)}) ORDER BY ts"
        with closing(self._connect()) as con:
            rows = con.execute(sql, params).fetchall()
        return [
            Bar(
                time_utc=_from_epoch(r[0]),
                open=r[1],
                high=r[2],
                low=r[3],
                close=r[4],
                volume=r[5],
                spread_points=r[6],
            )
            for r in rows
        ]

    def count(self, symbol: str, timeframe: TF) -> int:
        with closing(self._connect()) as con:
            row = con.execute(
                "SELECT COUNT(*) FROM bars WHERE symbol = ? AND timeframe = ?", (symbol, timeframe.value)
            ).fetchone()
        return int(row[0])

    def range(self, symbol: str, timeframe: TF) -> tuple[datetime | None, datetime | None]:
        with closing(self._connect()) as con:
            row = con.execute(
                "SELECT MIN(ts), MAX(ts) FROM bars WHERE symbol = ? AND timeframe = ?", (symbol, timeframe.value)
            ).fetchone()
        if row is None or row[0] is None:
            return None, None
        return _from_epoch(row[0]), _from_epoch(row[1])

    def symbols(self) -> list[tuple[str, str, int]]:
        with closing(self._connect()) as con:
            return [
                (r[0], r[1], r[2])
                for r in con.execute(
                    "SELECT symbol, timeframe, COUNT(*) FROM bars GROUP BY symbol, timeframe ORDER BY symbol"
                ).fetchall()
            ]

    # --- quality ------------------------------------------------------------------

    def gaps(self, symbol: str, timeframe: TF, tolerance: float = 1.5) -> list[Gap]:
        """Holes in the series, ignoring the weekend when the FX market is simply shut.

        `tolerance` allows for the odd late bar before something counts as a gap.
        """
        bars = self.load(symbol, timeframe)
        step = timedelta(minutes=timeframe.minutes)
        out: list[Gap] = []
        for previous, current in zip(bars, bars[1:], strict=False):
            delta = current.time_utc - previous.time_utc
            if delta <= step * tolerance:
                continue
            if _spans_weekend(previous.time_utc, current.time_utc):
                continue
            out.append(
                Gap(
                    start_utc=previous.time_utc,
                    end_utc=current.time_utc,
                    missing_bars=max(0, int(delta / step) - 1),
                )
            )
        return out


def _spans_weekend(start: datetime, end: datetime) -> bool:
    """FX closes Friday evening and reopens Sunday evening; that hole is the market, not the data."""
    if (end - start) > timedelta(days=3):
        return False  # too long to be just a weekend; that is a real hole
    day = start
    while day < end:
        if day.weekday() == 5:  # Saturday falls inside the interval
            return True
        day += timedelta(hours=1)
    return end.weekday() == 5
