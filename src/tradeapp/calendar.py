"""Economic calendar and news blocking (P3-01). No LLM anywhere in this file.

This is the highest-value part of the whole AI plan and the only part that needs no model at all.
The release time of an interest rate decision is known days ahead; nothing has to be predicted.
"Do not hold a position through NFP" is a rule, and rules are cheap, deterministic, and testable.

The MetaTrader5 Python package exposes no calendar, so events come from outside. Two ways in:

- **A file you drop in.** Always works, no network, no third party deciding your risk. Both the
  project's own shape and the common ForexFactory weekly JSON are understood.
- **A URL you choose.** Deliberately not hardcoded. Which feed to trust is the owner's decision,
  not a default buried in a library, and a wrong or stale feed here silently stops the bot trading.

`NewsWindows` implements the `NewsBlocker` protocol the Risk Engine already accepts, so wiring it
in changes no decision code.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from tradeapp.config import resolve_data_path
from tradeapp.risk.sizing import split_pair

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    ts        INTEGER NOT NULL,
    currency  TEXT    NOT NULL,
    title     TEXT    NOT NULL,
    impact    TEXT    NOT NULL,
    source    TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (ts, currency, title)
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
"""


class Impact(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @property
    def rank(self) -> int:
        return {"LOW": 0, "MEDIUM": 1, "HIGH": 2}[self.value]

    @classmethod
    def parse(cls, raw: object) -> Impact:
        text = str(raw or "").strip().lower()
        if text in {"high", "3", "red"}:
            return cls.HIGH
        if text in {"medium", "moderate", "2", "orange", "ora"}:
            return cls.MEDIUM
        return cls.LOW


@dataclass(frozen=True)
class CalendarEvent:
    time_utc: datetime
    currency: str
    title: str
    impact: Impact
    source: str = ""

    def __str__(self) -> str:
        return f"{self.time_utc:%Y-%m-%d %H:%M} UTC  {self.currency}  {self.impact.value:<6} {self.title}"


def _epoch(moment: datetime) -> int:
    return int((moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp())


class CalendarStore:
    def __init__(self, path: str | Path = "data/calendar.db") -> None:
        path = resolve_data_path(path)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as con:
            con.executescript(SCHEMA)
            con.commit()

    def upsert(self, events: Sequence[CalendarEvent]) -> int:
        if not events:
            return 0
        before = self.count()
        with closing(sqlite3.connect(self.path)) as con:
            con.executemany(
                "INSERT OR REPLACE INTO events (ts, currency, title, impact, source) VALUES (?, ?, ?, ?, ?)",
                [(_epoch(e.time_utc), e.currency.upper(), e.title, e.impact.value, e.source) for e in events],
            )
            con.commit()
        return self.count() - before

    def count(self) -> int:
        with closing(sqlite3.connect(self.path)) as con:
            return int(con.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def between(
        self,
        start: datetime,
        end: datetime,
        currencies: Iterable[str] | None = None,
        min_impact: Impact | None = None,
    ) -> list[CalendarEvent]:
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(
                "SELECT ts, currency, title, impact, source FROM events WHERE ts >= ? AND ts <= ? ORDER BY ts",
                (_epoch(start), _epoch(end)),
            ).fetchall()
        wanted = {c.upper() for c in currencies} if currencies else None
        out = []
        for ts, currency, title, impact, source in rows:
            level = Impact(impact)
            if wanted is not None and currency not in wanted:
                continue
            if min_impact is not None and level.rank < min_impact.rank:
                continue
            out.append(
                CalendarEvent(
                    time_utc=datetime.fromtimestamp(ts, UTC),
                    currency=currency,
                    title=title,
                    impact=level,
                    source=source,
                )
            )
        return out

    def upcoming(self, now: datetime, hours: int = 24, min_impact: Impact | None = None) -> list[CalendarEvent]:
        return self.between(now, now + timedelta(hours=hours), min_impact=min_impact)

    def range(self) -> tuple[datetime | None, datetime | None]:
        with closing(sqlite3.connect(self.path)) as con:
            row = con.execute("SELECT MIN(ts), MAX(ts) FROM events").fetchone()
        if row is None or row[0] is None:
            return None, None
        return datetime.fromtimestamp(row[0], UTC), datetime.fromtimestamp(row[1], UTC)


class NewsWindows:
    """The `NewsBlocker` the Risk Engine already knows how to ask.

    Blocks a symbol while either of its currencies is inside a window around a release. The
    default is HIGH impact only and ±30 minutes (D3): blocking on every low-impact number would
    stop trading most of the day and teach you to switch it off.
    """

    def __init__(
        self,
        store: CalendarStore,
        *,
        before_min: int = 30,
        after_min: int = 30,
        min_impact: Impact = Impact.HIGH,
        cache_hours: int = 6,
    ) -> None:
        self.store = store
        self.before = timedelta(minutes=before_min)
        self.after = timedelta(minutes=after_min)
        self.min_impact = min_impact
        self.cache_hours = cache_hours
        self._cache: list[CalendarEvent] = []
        self._cached_until: datetime | None = None

    def _events_near(self, at: datetime) -> list[CalendarEvent]:
        if (
            self._cached_until is None
            or at > self._cached_until
            or at < self._cached_until - timedelta(hours=self.cache_hours * 2)
        ):
            window = timedelta(hours=self.cache_hours)
            self._cache = self.store.between(at - window, at + window, min_impact=self.min_impact)
            self._cached_until = at + window
        return self._cache

    def blocked(self, symbol: str, at: datetime) -> str | None:
        pair = split_pair(symbol)
        currencies = set(pair) if pair else set()
        for event in self._events_near(at):
            if currencies and event.currency not in currencies:
                continue
            if event.time_utc - self.before <= at <= event.time_utc + self.after:
                minutes = (event.time_utc - at).total_seconds() / 60
                when = f"in {minutes:.0f} min" if minutes >= 0 else f"{-minutes:.0f} min ago"
                return f"{event.currency} {event.title} ({event.impact.value}) {when}"
        return None

    def windows(self, symbol: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime, str]]:
        """For the UI: the blocked stretches ahead, so a human can see them on a chart."""
        pair = split_pair(symbol)
        currencies = set(pair) if pair else set()
        out = []
        for event in self.store.between(start - self.before, end + self.after, min_impact=self.min_impact):
            if currencies and event.currency not in currencies:
                continue
            out.append((event.time_utc - self.before, event.time_utc + self.after, f"{event.currency} {event.title}"))
        return out


# --- getting events in ---------------------------------------------------------------


def parse_events(payload: object, source: str = "file") -> list[CalendarEvent]:
    """Understands this project's shape and the common ForexFactory weekly JSON.

    Rows that cannot be parsed are skipped rather than failing the import: a calendar feed with one
    malformed entry should not leave the bot with no calendar at all.
    """
    rows = payload if isinstance(payload, list) else payload.get("events", []) if isinstance(payload, dict) else []
    out: list[CalendarEvent] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_time = row.get("time_utc") or row.get("date") or row.get("time")
        moment = _parse_time(raw_time)
        currency = str(row.get("currency") or row.get("country") or "").strip().upper()
        title = str(row.get("title") or row.get("event") or row.get("name") or "").strip()
        if moment is None or not currency or not title:
            continue
        out.append(
            CalendarEvent(
                time_utc=moment,
                currency=currency,
                title=title,
                impact=Impact.parse(row.get("impact") or row.get("importance")),
                source=source,
            )
        )
    return out


def _parse_time(raw: object) -> datetime | None:
    if isinstance(raw, int | float):
        return datetime.fromtimestamp(float(raw), UTC)
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)


def load_file(path: str | Path, store: CalendarStore) -> int:
    """Import a JSON file the owner dropped in. Always available, no network, no third party."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return store.upsert(parse_events(payload, source=str(path)))


def fetch_url(url: str, store: CalendarStore, timeout: float = 20.0) -> int:
    """Import from a feed the owner chose.

    No default URL on purpose. Which calendar to trust is a decision with consequences — a stale or
    wrong feed silently stops the bot trading, or worse, silently lets it trade through NFP.
    """
    import httpx

    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return store.upsert(parse_events(response.json(), source=url))
