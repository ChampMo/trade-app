"""The Scout: what is on the radar this week, in the model's own words (P3-03).

The Scout is the agent with the least power in the system, and that is deliberate. It cannot open,
close or block anything. It produces a short briefing that the Analyst gets to read, and the
Analyst can only move four bounded numbers (D6).

The reason it stays that weak is the failure mode of asking a language model about the calendar:
it will happily give you a release time, and the time will sometimes be wrong. A wrong time in the
calendar is not a small error — the calendar is what stops the bot trading through NFP, and a
system that trusted an invented one would block the wrong hour and trade through the right one.
So **the Scout never writes to the calendar** (D24). The calendar comes from a file or a feed the
owner chose, and stays the only thing that can block a trade on a schedule.

What the Scout is good at is the shape of the week: which currencies are in play, what themes are
running, what the market is waiting for. That is context, and context is exactly what the Analyst
is missing when all it can see is EMAs and an ATR.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tradeapp.ai.client import BudgetExceeded, DeepSeekClient
from tradeapp.ai.schemas import ScoutReport
from tradeapp.journal import Journal
from tradeapp.risk.sizing import split_pair

SOURCE = "ai"
STATE_KEY = "scout_briefing"
MAX_EVENTS = 12
DEFAULT_MAX_AGE_HOURS = 36

SYSTEM = """You are the scout in an automated FX trading system.

You do not decide trades and you are not the calendar. Your job is to say what is on the radar for
the currencies given, in the period given. Reply with a single JSON object and nothing else:

{
  "events": [
    {"title": "short name of the event or theme",
     "currency": "USD",
     "impact": "HIGH" | "MEDIUM" | "LOW",
     "summary": "one sentence on why it matters"}
  ]
}

Rules you must follow:
- **Never state a release time or date.** You will be wrong often enough to matter and the system
  gets its times from a real calendar, not from you.
- At most 12 entries, ordered by how much they matter. Fewer is better than padded.
- Only the currencies asked about.
- If nothing of consequence is expected, return an empty list. That is a useful answer."""


@dataclass
class Briefing:
    """What the Scout last said, and when. Old briefings expire rather than misleading anyone."""

    written_utc: datetime
    events: list[dict]

    def age_hours(self, now: datetime) -> float:
        return (now - self.written_utc).total_seconds() / 3600

    def fresh(self, now: datetime, max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> bool:
        return self.age_hours(now) <= max_age_hours

    def lines(self, limit: int = 6) -> list[str]:
        return [
            f"  {e.get('currency', '???')}  {e.get('impact', 'LOW')}  {e.get('title', '')}"
            + (f" — {e['summary']}" if e.get("summary") else "")
            for e in self.events[:limit]
        ]


def currencies_for(symbol: str) -> list[str]:
    pair = split_pair(symbol)
    return list(pair) if pair else [symbol.upper()]


def build_prompt(symbol: str, now: datetime, days: int = 7) -> str:
    ccy = currencies_for(symbol)
    return "\n".join(
        [
            f"Currencies: {', '.join(ccy)} (the pair traded is {symbol})",
            f"Period: the next {days} days from {now:%Y-%m-%d} (UTC)",
            "",
            "What is on the radar for these currencies in that period? Themes and scheduled events "
            "both count. Remember: no times, no dates, at most 12 entries.",
        ]
    )


def load_briefing(journal: Journal) -> Briefing | None:
    raw = journal.get_state(STATE_KEY)
    if not raw:
        return None
    try:
        return Briefing(datetime.fromisoformat(raw["written_utc"]), list(raw["events"]))
    except (KeyError, TypeError, ValueError):
        return None


def briefing_lines(journal: Journal, now: datetime, max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> list[str]:
    """What the Analyst is allowed to see. An old briefing is dropped, never shown as current."""
    briefing = load_briefing(journal)
    if briefing is None or not briefing.fresh(now, max_age_hours) or not briefing.events:
        return []
    return briefing.lines()


class Scout:
    def __init__(
        self,
        client: DeepSeekClient,
        journal: Journal,
        *,
        symbol: str = "EURUSD",
        now=lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.journal = journal
        self.symbol = symbol
        self._now = now

    def refresh(self, days: int = 7) -> tuple[Briefing | None, str]:
        """Ask for a briefing. Every failure leaves the previous one alone; none of them stop anything."""
        now = self._now()
        if not self.client.available:
            return load_briefing(self.journal), ("no API key" if not self.client.api_key else "daily budget spent")

        try:
            report: ScoutReport = self.client.ask_json(
                "scout", SYSTEM, build_prompt(self.symbol, now, days), ScoutReport
            )
        except BudgetExceeded as e:
            return load_briefing(self.journal), str(e)
        except (RuntimeError, ValueError) as e:
            self.journal.event("WARN", SOURCE, "scout call failed; keeping the previous briefing", {"error": str(e)})
            return load_briefing(self.journal), str(e)

        wanted = set(currencies_for(self.symbol))
        events = [e.model_dump() for e in report.events if e.currency.upper() in wanted][:MAX_EVENTS]
        dropped = len(report.events) - len(events)
        briefing = Briefing(now, events)
        self.journal.set_state(STATE_KEY, {"written_utc": now.isoformat(), "events": events})
        self.journal.event(
            "INFO",
            SOURCE,
            f"scout briefing: {len(events)} item(s) on {'/'.join(sorted(wanted))}",
            {
                "events": events,
                "dropped_out_of_scope": dropped,
                "spent_today_usd": round(self.client.spent_today, 4),
            },
        )
        return briefing, f"{len(events)} item(s)" + (f", {dropped} dropped as out of scope" if dropped else "")


def stale_after(now: datetime, hours: float = DEFAULT_MAX_AGE_HOURS) -> datetime:
    return now + timedelta(hours=hours)
