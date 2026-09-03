"""Daily post-mortem and the A/B comparison (P4-03, P3-04).

D11 is the rule this file exists to enforce: **the normal output of a post-mortem is a report, not
a parameter change.** A strategy that wins 45% of the time will lose five in a row regularly, and a
system that adjusts itself after every losing streak is fitting itself to last week.

So losses are classified, and only two of the four classes can possibly lead to a code change:

- **execution** — slippage, retries, a rejected order, a stop that had to be set after the fill.
  Something in the plumbing cost money. Worth fixing.
- **bug** — a strategy was disabled, a schema failed, the loop raised. Worth fixing.
- **variance** — a normal loss. Worth nothing but the record.
- **regime** — the market changed. This one is **not** classified automatically, on purpose. It
  needs judgement across weeks, and a rule that guessed at it would quietly relabel ordinary
  losing streaks as regime changes, which is the exact mistake the classification exists to stop.

Nothing here proposes anything. It counts, groups and explains, and a human reads it in five
minutes over coffee.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from tradeapp.journal import Journal
from tradeapp.journal.models import AICall, Decision, Order

SOURCE = "report"
SLIPPAGE_FLAG_POINTS = 2.0


@dataclass
class TradeRow:
    """One round trip, stitched from the open and close rows that share a client_ref."""

    client_ref: str
    symbol: str
    side: str | None
    volume: float | None
    magic: int | None
    opened_utc: datetime
    closed_utc: datetime | None
    entry: float | None
    exit: float | None
    open_slippage: float | None
    close_slippage: float | None
    sl_verified: bool | None
    retries: int
    strategy: str | None = None
    variant: str | None = None

    @property
    def closed(self) -> bool:
        return self.exit is not None

    @property
    def points(self) -> float | None:
        if self.entry is None or self.exit is None or self.side is None:
            return None
        sign = 1 if self.side == "LONG" else -1
        return sign * (self.exit - self.entry)

    @property
    def worst_slippage(self) -> float:
        return max(self.open_slippage or 0.0, self.close_slippage or 0.0)


@dataclass
class DayReport:
    day: str
    generated_utc: datetime
    trades: list[TradeRow] = field(default_factory=list)
    classification: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    rejections: Counter = field(default_factory=Counter)
    by_strategy: dict[str, dict] = field(default_factory=dict)
    events: dict[str, int] = field(default_factory=dict)
    ai_calls: int = 0
    ai_cost: float = 0.0
    ai_schema_failures: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def actionable(self) -> list[str]:
        """Only execution and bug lead anywhere. Variance is the record, regime is a human's call."""
        return self.classification.get("execution", []) + self.classification.get("bug", [])


def _day_bounds(day: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    return start, start + timedelta(days=1)


def collect_trades(journal: Journal, start: datetime, end: datetime) -> list[TradeRow]:
    """Stitch order rows into round trips. Rows are grouped by client_ref, as the executor writes them."""
    with journal.session() as s:
        rows = list(
            s.execute(
                select(Order)
                .where(Order.ts_utc >= start.replace(tzinfo=None), Order.ts_utc < end.replace(tzinfo=None))
                .order_by(Order.id)
            ).scalars()
        )
        decisions = list(s.execute(select(Decision)).scalars())

    by_order = {d.order_id: d for d in decisions if d.order_id}
    grouped: dict[str, list[Order]] = defaultdict(list)
    for row in rows:
        grouped[row.client_ref].append(row)

    out: list[TradeRow] = []
    for ref, group in grouped.items():
        opens = [o for o in group if o.kind == "open" and o.ok]
        if not opens:
            continue
        first = opens[0]
        closes = [o for o in group if o.kind == "close" and o.ok]
        last = closes[-1] if closes else None
        decision = by_order.get(first.id)
        out.append(
            TradeRow(
                client_ref=ref,
                symbol=first.symbol,
                side=first.side,
                volume=first.volume,
                magic=first.magic,
                opened_utc=first.ts_utc,
                closed_utc=last.ts_utc if last else None,
                entry=first.price_filled,
                exit=last.price_filled if last else None,
                open_slippage=first.slippage_points,
                close_slippage=last.slippage_points if last else None,
                sl_verified=first.sl_verified,
                retries=len([o for o in group if o.kind == "open"]) - 1,
                strategy=decision.strategy_id if decision else first.comment,
                variant=decision.variant if decision else None,
            )
        )
    return sorted(out, key=lambda t: t.opened_utc)


def classify(trade: TradeRow) -> tuple[str, str]:
    """Deterministic. `regime` is never returned: see the module docstring."""
    if trade.sl_verified is False:
        return "bug", "the stop could not be verified at the broker"
    if trade.retries > 0:
        return "execution", f"the order took {trade.retries + 1} attempts"
    if trade.worst_slippage >= SLIPPAGE_FLAG_POINTS:
        return "execution", f"slippage {trade.worst_slippage:.1f} points"
    return "variance", "an ordinary result"


def build(journal: Journal, day: str | None = None, now: datetime | None = None) -> DayReport:
    moment = now or datetime.now(UTC)
    day = day or moment.strftime("%Y-%m-%d")
    start, end = _day_bounds(day)
    report = DayReport(day=day, generated_utc=moment)

    report.trades = collect_trades(journal, start, end)
    for trade in report.trades:
        kind, why = classify(trade)
        if kind == "variance" and (trade.points or 0) >= 0:
            continue  # winners need no classification
        report.classification[kind].append(f"{trade.client_ref} ({trade.strategy}): {why}")

    with journal.session() as s:
        decisions = list(
            s.execute(
                select(Decision).where(
                    Decision.ts_utc >= start.replace(tzinfo=None), Decision.ts_utc < end.replace(tzinfo=None)
                )
            ).scalars()
        )
        ai = list(
            s.execute(
                select(AICall).where(
                    AICall.ts_utc >= start.replace(tzinfo=None), AICall.ts_utc < end.replace(tzinfo=None)
                )
            ).scalars()
        )

    for d in decisions:
        if d.verdict != "APPROVED" and d.verdict_reason:
            report.rejections[d.verdict_reason.split(":")[0]] += 1

    groups: dict[str, list[TradeRow]] = defaultdict(list)
    for trade in report.trades:
        groups[f"{trade.strategy}{f' · {trade.variant}' if trade.variant else ''}"].append(trade)
    for key, group in groups.items():
        closed = [t for t in group if t.closed]
        wins = [t for t in closed if (t.points or 0) > 0]
        report.by_strategy[key] = {
            "trades": len(group),
            "closed": len(closed),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
            "avg_slippage": round(sum(t.worst_slippage for t in group) / len(group), 2) if group else 0.0,
        }

    report.ai_calls = len(ai)
    report.ai_cost = round(sum(c.cost_usd or 0 for c in ai), 5)
    report.ai_schema_failures = len([c for c in ai if c.schema_ok is False])

    with journal.session() as s:
        from tradeapp.journal.models import Event

        events = list(
            s.execute(
                select(Event).where(Event.ts_utc >= start.replace(tzinfo=None), Event.ts_utc < end.replace(tzinfo=None))
            ).scalars()
        )
    report.events = dict(Counter(e.severity for e in events))

    if not report.trades:
        report.notes.append("no trades today")
    if report.classification.get("bug"):
        report.notes.append("something in the plumbing broke; read the bug list before anything else")
    if not report.actionable and report.trades:
        report.notes.append("nothing actionable — losses today were ordinary variance")
    return report


def render(report: DayReport) -> str:
    lines = [
        f"# Post-mortem · {report.day}",
        "",
        f"Generated {report.generated_utc:%Y-%m-%d %H:%M} UTC. Read this in five minutes; it proposes nothing.",
        "",
        "## Day",
        "",
        f"- trades: {len(report.trades)} ({len([t for t in report.trades if t.closed])} closed)",
        f"- events: {report.events or 'none'}",
        f"- AI: {report.ai_calls} call(s), ${report.ai_cost:.5f}"
        + (f", {report.ai_schema_failures} schema failure(s)" if report.ai_schema_failures else ""),
        "",
    ]

    if report.by_strategy:
        lines += [
            "## By strategy",
            "",
            "| strategy | trades | closed | wins | win rate | avg slippage |",
            "|---|---|---|---|---|---|",
        ]
        for key, s in sorted(report.by_strategy.items()):
            lines.append(
                f"| {key} | {s['trades']} | {s['closed']} | {s['wins']} | {s['win_rate']}% | {s['avg_slippage']} pt |"
            )
        lines.append("")

    lines += ["## Losses, classified", ""]
    for kind in ("bug", "execution", "variance"):
        items = report.classification.get(kind, [])
        lines.append(f"**{kind}** — {len(items)}")
        for item in items[:20]:
            lines.append(f"  - {item}")
    lines += [
        "",
        "`regime` is deliberately not assigned automatically. It needs judgement across weeks, and a "
        "rule that guessed would quietly relabel ordinary losing streaks as regime changes (D11).",
        "",
    ]

    if report.rejections:
        lines += ["## What the limits refused", ""]
        for reason, count in report.rejections.most_common():
            lines.append(f"- {reason}: {count}")
        lines.append("")

    if report.notes:
        lines += ["## Notes", "", *[f"- {n}" for n in report.notes], ""]

    lines += [
        "## Proposals",
        "",
        "None. A parameter change needs a full-history backtest plus walk-forward and happens on a "
        "fixed monthly cadence, not after a losing day (D11). Only the **execution** and **bug** lists "
        "above can lead to a code change.",
        "",
    ]
    return "\n".join(lines)


def write(report: DayReport, directory: str | Path = "reports") -> Path:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"postmortem-{report.day}.md"
    target.write_text(render(report), encoding="utf-8")
    return target


# --- A/B comparison (P3-04) --------------------------------------------------------------


def ab_table(journal: Journal, days: int = 30, now: datetime | None = None) -> dict[str, dict]:
    """Group closed trades by strategy and variant, which is what the magic numbers were for (D9).

    The question this answers is the one the whole AI layer has to justify itself against: does the
    variant with the calendar, or with the model, actually beat the one with neither?
    """
    moment = now or datetime.now(UTC)
    trades = collect_trades(journal, moment - timedelta(days=days), moment + timedelta(days=1))
    groups: dict[str, list[TradeRow]] = defaultdict(list)
    for t in trades:
        groups[f"{t.strategy}{f' · {t.variant}' if t.variant else ''}"].append(t)

    out = {}
    for key, group in groups.items():
        closed = [t for t in group if t.closed]
        wins = [t for t in closed if (t.points or 0) > 0]
        out[key] = {
            "trades": len(group),
            "closed": len(closed),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
            "avg_slippage": round(sum(t.worst_slippage for t in group) / len(group), 2) if group else 0.0,
            "magics": sorted({t.magic for t in group if t.magic}),
        }
    return out
