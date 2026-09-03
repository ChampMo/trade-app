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

from tradeapp.contracts import SymbolInfo
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


def realized_pnl_by_strategy(
    journal: Journal,
    start: datetime,
    end: datetime,
    symbols: dict[str, SymbolInfo],
) -> dict[str, float]:
    """Money made or lost per strategy on closed round trips in the window, in the account currency.

    Derived from the journal's own fill prices rather than the broker's profit field, because the
    journal is what survives a restart and what a post-mortem can replay. That means it captures
    the spread (entries fill at the ask, exits at the bid) but **not** commission or swap, so a
    strategy's real day is very slightly worse than this says. The number exists to answer "has
    this strategy spent its budget today", where being a few cents optimistic changes nothing.
    """
    out: dict[str, float] = defaultdict(float)
    for trade in collect_trades(journal, start, end):
        sym = symbols.get(trade.symbol)
        if sym is None or trade.points is None or not trade.volume:
            continue
        tick_size = sym.tick_size or sym.point
        if tick_size <= 0 or sym.tick_value <= 0:
            continue
        out[trade.strategy or "unknown"] += (trade.points / tick_size) * sym.tick_value * trade.volume
    return {k: round(v, 2) for k, v in out.items()}


# --- drift: live against the backtest it was promoted on (P4-04) --------------------------


DRIFT_MIN_TRADES = 20  # below this the comparison is noise, and the report says so first


@dataclass
class Metric:
    name: str
    backtest: float | None
    live: float | None
    worse: bool
    note: str = ""

    @property
    def gap(self) -> float | None:
        if self.backtest is None or self.live is None:
            return None
        return round(self.live - self.backtest, 2)


@dataclass
class DriftReport:
    run_id: int
    strategy: str
    symbol: str
    generated_utc: datetime
    days: int
    backtest_trades: int
    live_trades: int
    metrics: list[Metric] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    live_slippage: float = 0.0

    @property
    def meaningful(self) -> bool:
        return self.live_trades >= DRIFT_MIN_TRADES

    @property
    def diverging(self) -> list[Metric]:
        """Empty until the sample is large enough. A difference over five trades is not drift."""
        return [m for m in self.metrics if m.worse] if self.meaningful else []


def _points(entry: float | None, exit_: float | None, side: str | None, point: float) -> float | None:
    if entry is None or exit_ is None or side is None or point <= 0:
        return None
    sign = 1 if side.upper() == "LONG" else -1
    return sign * (exit_ - entry) / point


def _profile(points: list[float], hold_hours: list[float], span_days: float) -> dict[str, float | None]:
    """The five numbers both sides are compared on. Points, not money, so lot size cannot distort."""
    if not points:
        return {
            "trades_per_week": 0.0,
            "win_rate": None,
            "avg_win": None,
            "avg_loss": None,
            "expectancy": None,
            "avg_hold_hours": None,
        }
    wins = [p for p in points if p > 0]
    losses = [p for p in points if p <= 0]
    weeks = max(span_days / 7.0, 1e-9)
    return {
        "trades_per_week": round(len(points) / weeks, 2),
        "win_rate": round(len(wins) / len(points) * 100, 1),
        "avg_win": round(sum(wins) / len(wins), 1) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 1) if losses else 0.0,
        "expectancy": round(sum(points) / len(points), 1),
        "avg_hold_hours": round(sum(hold_hours) / len(hold_hours), 1) if hold_hours else None,
    }


def build_drift(
    journal: Journal,
    run_id: int,
    *,
    days: int = 30,
    point: float = 0.00001,
    now: datetime | None = None,
) -> DriftReport:
    """Compare live trading against the stored backtest the strategy was promoted on.

    The comparison is in **points per trade**, not money: lot sizes differ between a backtest on a
    flat 10,000 and a live account that has moved since, and comparing money would call that drift.

    Nothing here is a verdict on the strategy. A live win rate below the backtest is the expected
    state for a small sample, which is why the report refuses to draw conclusions under
    `DRIFT_MIN_TRADES` trades and says so before anything else.
    """
    moment = now or datetime.now(UTC)
    run = journal.backtest_run(run_id)
    if run is None:
        raise KeyError(f"no backtest run #{run_id} in the journal")

    stored = run.trades or []
    bt_points = [
        p for p in (_points(t.get("entry"), t.get("exit"), t.get("side"), point) for t in stored) if p is not None
    ]
    bt_hold = []
    for t in stored:
        try:
            opened = datetime.fromisoformat(t["opened_utc"])
            closed = datetime.fromisoformat(t["closed_utc"])
            bt_hold.append((closed - opened).total_seconds() / 3600)
        except (KeyError, TypeError, ValueError):
            continue
    bt_span = ((run.data_to - run.data_from).days if run.data_from and run.data_to else 1) or 1
    backtest = _profile(bt_points, bt_hold, bt_span)

    start = moment - timedelta(days=days)
    live_trades = [t for t in collect_trades(journal, start, moment + timedelta(days=1)) if t.closed]
    # Only this strategy's trades, and a variant counts (`ema_cross` covers `ema_cross:B`). A trade
    # with no strategy on it is *not* counted: attributing an unknown trade here would quietly put
    # the smoke tests and anything opened by hand into the strategy's record.
    live_trades = [t for t in live_trades if (t.strategy or "").startswith(run.strategy)]
    live_points = [p for p in (_points(t.entry, t.exit, t.side, point) for t in live_trades) if p is not None]
    live_hold = [
        (t.closed_utc - t.opened_utc).total_seconds() / 3600 for t in live_trades if t.closed_utc and t.opened_utc
    ]
    live = _profile(live_points, live_hold, float(days))

    report = DriftReport(
        run_id=run_id,
        strategy=run.strategy,
        symbol=run.symbol,
        generated_utc=moment,
        days=days,
        backtest_trades=len(bt_points),
        live_trades=len(live_points),
        live_slippage=round(sum(t.worst_slippage for t in live_trades) / len(live_trades), 2) if live_trades else 0.0,
    )

    def add(name: str, key: str, worse: bool, note: str = "") -> None:
        report.metrics.append(Metric(name, backtest[key], live[key], worse and report.live_trades > 0, note))

    wr_b, wr_l = backtest["win_rate"], live["win_rate"]
    ex_b, ex_l = backtest["expectancy"], live["expectancy"]
    tw_b, tw_l = backtest["trades_per_week"], live["trades_per_week"]
    hold_b, hold_l = backtest["avg_hold_hours"], live["avg_hold_hours"]

    add(
        "win rate %",
        "win_rate",
        wr_b is not None and wr_l is not None and wr_l < wr_b - 10,
        "10 points below the backtest is the line; anything less is ordinary variance",
    )
    add(
        "expectancy, points per trade",
        "expectancy",
        ex_b is not None and ex_l is not None and (ex_l < 0 <= ex_b or (ex_b > 0 and ex_l < ex_b * 0.5)),
        "the number that decides whether the edge survived contact with the broker",
    )
    add(
        "trades per week",
        "trades_per_week",
        bool(tw_b) and bool(tw_l) and abs(tw_l - tw_b) > tw_b * 0.5,
        "a different trade count means the live system is not seeing the same setups",
    )
    add("average win, points", "avg_win", False)
    add("average loss, points", "avg_loss", False)
    add(
        "average hold, hours",
        "avg_hold_hours",
        bool(hold_b) and bool(hold_l) and abs(hold_l - hold_b) > hold_b * 0.5,
        "exits firing at different times point at the stop or the data, not the idea",
    )

    if not report.live_trades:
        report.notes.append(f"no closed live trades for {run.strategy} in the last {days} days; nothing to compare yet")
    elif not report.meaningful:
        report.notes.append(
            f"only {report.live_trades} live trades. Below {DRIFT_MIN_TRADES} the differences below are noise, "
            "and reading them as drift is how a working strategy gets changed for no reason (D11)"
        )
    if report.live_slippage >= SLIPPAGE_FLAG_POINTS:
        report.notes.append(
            f"average slippage {report.live_slippage} points; the backtest assumed "
            f"{(run.costs or {}).get('slippage_points', '?')}. Slippage is an execution problem and it is actionable"
        )
    if report.meaningful and not report.diverging:
        report.notes.append("live is tracking the backtest within the thresholds; nothing to look at")
    return report


def render_drift(report: DriftReport) -> str:
    def cell(v: float | None) -> str:
        return "—" if v is None else f"{v:g}"

    lines = [
        f"# Drift · {report.strategy} · {report.generated_utc:%Y-%m-%d}",
        "",
        f"Live over the last {report.days} days against stored backtest run #{report.run_id} "
        f"({report.backtest_trades} trades). Everything is in points per trade, because lot sizes "
        "differ between the two and comparing money would call that drift.",
        "",
    ]

    if not report.meaningful:
        lines += [
            "## Read this first",
            "",
            f"**{report.live_trades} live trades is not enough to conclude anything** "
            f"(the bar is {DRIFT_MIN_TRADES}). The table is below because the numbers are worth "
            "watching accumulate, not because they mean something yet.",
            "",
        ]

    lines += [
        "## Live against backtest",
        "",
        "| metric | backtest | live | gap |",
        "|---|---|---|---|",
    ]
    for m in report.metrics:
        flag = " ⚠" if m.worse and report.meaningful else ""
        lines.append(f"| {m.name}{flag} | {cell(m.backtest)} | {cell(m.live)} | {cell(m.gap)} |")
    lines += ["", f"Average live slippage: {report.live_slippage} points.", ""]

    diverging = report.diverging
    lines += ["## What is diverging", ""]
    if diverging:
        for m in diverging:
            lines.append(f"- **{m.name}**: backtest {cell(m.backtest)}, live {cell(m.live)}. {m.note}")
    else:
        lines.append("- nothing beyond the thresholds")
    lines.append("")

    if report.notes:
        lines += ["## Notes", "", *[f"- {n}" for n in report.notes], ""]

    lines += [
        "## Proposals",
        "",
        "None, and deliberately so (D11). A gap here is a question, not an answer: slippage and "
        "trade counts are execution problems and are actionable; a lower win rate over a small "
        "sample is what a small sample looks like. Deciding that the market has changed needs "
        "judgement across weeks, and a rule that guessed would relabel every losing month as a "
        "regime change.",
        "",
    ]
    return "\n".join(lines)


def write_drift(report: DriftReport, directory: str | Path = "reports") -> Path:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"drift-{report.strategy}-{report.generated_utc:%Y-%m-%d}.md"
    target.write_text(render_drift(report), encoding="utf-8")
    return target
