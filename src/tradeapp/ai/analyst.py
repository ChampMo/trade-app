"""The Analyst: the one agent whose output can change a trade (D6).

It reads market context and the calendar and returns four bounded numbers. It is not asked what
to do, because a model that is asked what to do will answer, confidently, and a system built on
that cannot be backtested.

The failure behaviour is the important part. Every way this can go wrong — no key, budget spent,
network down, a reply that does not fit the schema — ends the same way: the previous view stands
until it expires, and then everything is neutral. A silent AI must never stop the bot, and must
never keep steering it with an old opinion.

What is deliberately not in the prompt: the account balance, open positions, equity, and ticket
numbers. A model has no use for them and there is every reason not to send them anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from tradeapp.ai.client import BudgetExceeded, DeepSeekClient
from tradeapp.ai.schemas import AnalystView
from tradeapp.calendar import CalendarStore, Impact
from tradeapp.context import Context
from tradeapp.journal import Journal
from tradeapp.risk.limits import AIContext

SOURCE = "ai"

SYSTEM = """You are the analyst in an automated FX trading system.

You do not decide trades. You describe conditions, and a rules engine decides what to do with your
description. Reply with a single JSON object and nothing else:

{
  "regime": "short label, e.g. risk-off or USD strength",
  "bias": -1.0 to 1.0,        // negative favours short, positive favours long, 0 if unclear
  "size_mult": 0.0 to 1.5,    // 1.0 is normal size; below 1 when conditions are poor
  "block": true or false,     // true only when new positions should not be opened at all
  "valid_minutes": 5 to 1440, // how long this view should be trusted
  "note": "one sentence explaining the call"
}

Be conservative. 0 bias and 1.0 size_mult is the correct answer when nothing stands out; you are
not rewarded for having an opinion. Set block only for a genuine reason such as an imminent
high-impact release, not for ordinary uncertainty."""


@dataclass
class AnalystResult:
    context: AIContext
    used_model: bool
    detail: str


def build_prompt(ctx: Context, calendar: CalendarStore | None, now: datetime) -> str:
    """Market context only. No balance, no positions, no tickets."""
    lines = [
        f"Symbol: {ctx.symbol}",
        f"Timeframe: {ctx.timeframe.value}",
        f"Time now: {now:%Y-%m-%d %H:%M} UTC",
        f"Last close: {ctx.close():.5f}",
    ]
    if ctx.has_history(60):
        ema20, ema50 = ctx.ema(20), ctx.ema(50)
        atr, rsi = ctx.atr(14), ctx.rsi(14)
        if None not in (ema20, ema50, atr, rsi):
            lines += [
                f"EMA20: {ema20:.5f}   EMA50: {ema50:.5f}   ({'above' if ema20 > ema50 else 'below'})",
                f"ATR14: {atr:.5f}   RSI14: {rsi:.1f}",
                f"Change over the last 20 bars: {(ctx.close() - ctx.close(19)) / ctx.point:.0f} points",
            ]
    if calendar is not None:
        events = calendar.upcoming(now, hours=24, min_impact=Impact.MEDIUM)
        if events:
            lines.append("Scheduled releases in the next 24 hours:")
            lines += [f"  {e.time_utc:%H:%M} UTC  {e.currency}  {e.impact.value}  {e.title}" for e in events[:12]]
        else:
            lines.append("No medium or high impact releases scheduled in the next 24 hours.")
    return "\n".join(lines)


class Analyst:
    def __init__(
        self,
        client: DeepSeekClient,
        journal: Journal,
        *,
        calendar: CalendarStore | None = None,
        now=lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.journal = journal
        self.calendar = calendar
        self._now = now
        self.current = AIContext.neutral()

    @property
    def view(self) -> AIContext:
        """What the Risk Engine should use right now; expired views collapse to neutral themselves."""
        return self.current.effective(self._now())

    def refresh(self, ctx: Context) -> AnalystResult:
        """Ask for a new view. Every failure keeps the old one rather than stopping anything."""
        now = self._now()
        if not self.client.available:
            reason = "no API key" if not self.client.api_key else "daily budget spent"
            return AnalystResult(self.view, False, f"kept the previous view: {reason}")

        prompt = build_prompt(ctx, self.calendar, now)
        try:
            view: AnalystView = self.client.ask_json("analyst", SYSTEM, prompt, AnalystView)
        except BudgetExceeded as e:
            return AnalystResult(self.view, False, f"kept the previous view: {e}")
        except (RuntimeError, ValueError) as e:
            self.journal.event("WARN", SOURCE, "analyst call failed; keeping the previous view", {"error": str(e)})
            return AnalystResult(self.view, False, f"kept the previous view: {e}")

        self.current = AIContext.valid_for(
            view.valid_minutes,
            now,
            regime=view.regime,
            bias=view.bias,
            size_mult=view.size_mult,
            block=view.block,
            source="analyst",
        )
        self.journal.event(
            "INFO",
            SOURCE,
            f"analyst: {view.regime} bias {view.bias:+.2f} size x{view.size_mult:.2f}"
            + (" BLOCK" if view.block else ""),
            {
                "regime": view.regime,
                "bias": view.bias,
                "size_mult": view.size_mult,
                "block": view.block,
                "valid_minutes": view.valid_minutes,
                "note": view.note,
                "spent_today_usd": round(self.client.spent_today, 4),
            },
        )
        return AnalystResult(self.current, True, view.note or view.regime)
