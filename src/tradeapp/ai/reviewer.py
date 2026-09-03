"""The Reviewer: an opinion on the day, recorded next to the facts (P3-03, D11).

The post-mortem already classifies the day deterministically, and that classification is the one
the system counts. The Reviewer reads the same facts and says what it thinks, in prose, and its
opinion is stored beside the deterministic answer rather than instead of it.

That ordering is the whole design. `regime` is the classification a rule must never assign on its
own (D11), because a rule that guessed would relabel every losing streak as a changed market. A
model is no better at that guess, so the Reviewer is allowed to *say* regime and is not allowed to
*do* anything about it: it cannot change a parameter, promote a strategy, size a position or place
an order. It writes a paragraph a human reads over coffee.

What the prompt carries is trades in points, slippage, retries and counts. No equity, no balance,
no login, no ticket numbers — the same rule the Analyst lives under, for the same reason.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tradeapp.ai.client import BudgetExceeded, DeepSeekClient
from tradeapp.ai.schemas import ReviewReport
from tradeapp.journal import Journal
from tradeapp.reports import DayReport

SOURCE = "ai"

SYSTEM = """You are the reviewer in an automated FX trading system.

You are reading one day of a system that already knows what happened. You explain; you do not
propose. Reply with a single JSON object and nothing else:

{
  "summary": "a short paragraph on how the day went",
  "findings": [
    {"decision_id": 12 or null,
     "classification": "variance" | "execution" | "regime" | "bug",
     "note": "one sentence"}
  ],
  "bias_was_right": true | false | null
}

Rules you must follow:
- **Never propose a parameter change, a new strategy, or a trade.** Not in the summary, not in a
  note. A change follows a full backtest on a fixed cadence, never a losing day.
- Use `variance` unless something specific says otherwise. Most losses are ordinary.
- `execution` is for slippage, retries and rejected orders. `bug` is for a stop that could not be
  verified, a disabled strategy or a loop that raised.
- `regime` is a strong claim about the market changing. Say it only if the day genuinely reads
  that way, and say why in the note.
- `bias_was_right` refers to the AI bias if the day shows one; null when there is nothing to judge."""


def build_prompt(report: DayReport) -> str:
    """Facts only, in points. No equity, no balance, no tickets (D6)."""
    lines = [
        f"Day: {report.day} (UTC)",
        f"Trades: {len(report.trades)} ({len([t for t in report.trades if t.closed])} closed)",
        "",
    ]
    if report.trades:
        lines.append("Closed trades:")
        for t in report.trades:
            if not t.closed:
                continue
            points = f"{t.points / 0.0001:+.1f} pips" if t.points is not None else "open"
            lines.append(
                f"  {t.strategy or 'unknown'}  {t.side}  {points}  "
                f"slippage {t.worst_slippage:.1f}pt  retries {t.retries}"
                + ("  STOP NOT VERIFIED" if t.sl_verified is False else "")
            )
        lines.append("")

    if report.by_strategy:
        lines.append("By strategy:")
        for key, s in sorted(report.by_strategy.items()):
            lines.append(f"  {key}: {s['wins']}/{s['closed']} won, avg slippage {s['avg_slippage']}pt")
        lines.append("")

    lines.append("What the deterministic classifier already decided (this is not up for debate):")
    for kind in ("bug", "execution", "variance"):
        items = report.classification.get(kind, [])
        lines.append(f"  {kind}: {len(items)}")
        lines += [f"    {item}" for item in items[:10]]

    if report.rejections:
        lines += ["", "Rejections by the risk limits:"]
        lines += [f"  {reason}: {count}" for reason, count in report.rejections.most_common()]
    lines += ["", f"Events by severity: {report.events or 'none'}", f"AI calls: {report.ai_calls}"]
    return "\n".join(lines)


def render(review: ReviewReport) -> str:
    """The section appended to the post-mortem. Its heading says what it is worth."""
    lines = [
        "## What the reviewer said",
        "",
        "An opinion from a language model, recorded next to the facts and carrying no authority "
        "over any of them. It cannot change a parameter, promote a strategy or place an order (D11).",
        "",
        review.summary.strip(),
        "",
    ]
    if review.findings:
        lines.append("Findings:")
        for f in review.findings:
            where = f" (decision {f.decision_id})" if f.decision_id else ""
            lines.append(f"- **{f.classification}**{where}: {f.note}")
        lines.append("")
    if review.bias_was_right is not None:
        lines += [f"The reviewer thinks the AI bias was {'right' if review.bias_was_right else 'wrong'}.", ""]
    return "\n".join(lines)


class Reviewer:
    def __init__(self, client: DeepSeekClient, journal: Journal, *, now=lambda: datetime.now(UTC)) -> None:
        self.client = client
        self.journal = journal
        self._now = now

    def review(self, report: DayReport) -> tuple[ReviewReport | None, str]:
        """Returns the review, or None and a reason. Never raises: this runs after the trading day."""
        if not self.client.available:
            return None, ("no API key" if not self.client.api_key else "daily budget spent")
        try:
            result: ReviewReport = self.client.ask_json("reviewer", SYSTEM, build_prompt(report), ReviewReport)
        except BudgetExceeded as e:
            return None, str(e)
        except (RuntimeError, ValueError) as e:
            self.journal.event("WARN", SOURCE, "reviewer call failed", {"error": str(e)})
            return None, str(e)

        self.journal.event(
            "INFO",
            SOURCE,
            f"reviewer on {report.day}: {len(result.findings)} finding(s)",
            {
                "day": report.day,
                "summary": result.summary,
                "findings": [f.model_dump() for f in result.findings],
                "bias_was_right": result.bias_was_right,
                "spent_today_usd": round(self.client.spent_today, 4),
            },
        )
        return result, f"{len(result.findings)} finding(s)"
