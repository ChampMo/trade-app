"""Turning a list of trades into the handful of numbers worth looking at.

Deliberately short. A page of ratios invites you to go shopping for the one that flatters the
strategy; the ones here are the ones a gate in DECISIONS D3 actually reads.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from tradeapp.backtest.broker import ClosedTrade


@dataclass(frozen=True)
class Stats:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    net: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    costs: float = 0.0  # commission + swap + spread
    spread_cost: float = 0.0
    return_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_abs: float = 0.0
    longest_losing_streak: int = 0
    avg_hold_hours: float = 0.0
    exits: dict[str, int] = field(default_factory=dict)

    @property
    def cost_share_of_gross(self) -> float:
        return round(self.costs / self.gross_profit * 100, 1) if self.gross_profit > 0 else 0.0

    @property
    def net_before_costs(self) -> float:
        """What the idea would have made against a broker that charged nothing."""
        return round(self.net + self.costs, 2)

    def summary(self) -> str:
        return (
            f"{self.trades} trades  net {self.net:+.2f} ({self.return_pct:+.2f}%)  "
            f"win {self.win_rate:.0f}%  PF {self.profit_factor:.2f}  "
            f"maxDD {self.max_drawdown_pct:.2f}%  costs {self.costs:.2f}"
        )


def equity_drawdown(curve: list[tuple[datetime, float]]) -> tuple[float, float]:
    """Worst peak-to-trough fall on the equity curve, as a percentage and in money."""
    peak = None
    worst_pct = 0.0
    worst_abs = 0.0
    for _, equity in curve:
        peak = equity if peak is None else max(peak, equity)
        if peak > 0:
            worst_pct = max(worst_pct, (peak - equity) / peak * 100.0)
            worst_abs = max(worst_abs, peak - equity)
    return round(worst_pct, 3), round(worst_abs, 2)


def longest_losing_streak(trades: list[ClosedTrade]) -> int:
    worst = current = 0
    for t in trades:
        current = current + 1 if t.net < 0 else 0
        worst = max(worst, current)
    return worst


def compute(trades: list[ClosedTrade], curve: list[tuple[datetime, float]], start_balance: float) -> Stats:
    if not trades:
        dd_pct, dd_abs = equity_drawdown(curve)
        return Stats(max_drawdown_pct=dd_pct, max_drawdown_abs=dd_abs)

    nets = [t.net for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    spread_cost = sum(t.spread_cost for t in trades)
    costs = sum(t.total_cost for t in trades)
    dd_pct, dd_abs = equity_drawdown(curve)

    exits: dict[str, int] = {}
    for t in trades:
        exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1

    net = sum(nets)
    return Stats(
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        net=round(net, 2),
        gross_profit=round(gross_profit, 2),
        gross_loss=round(gross_loss, 2),
        costs=round(costs, 2),
        spread_cost=round(spread_cost, 2),
        return_pct=round(net / start_balance * 100, 3) if start_balance else 0.0,
        win_rate=round(len(wins) / len(trades) * 100, 1),
        profit_factor=round(gross_profit / gross_loss, 3) if gross_loss else math.inf,
        expectancy=round(net / len(trades), 2),
        max_drawdown_pct=dd_pct,
        max_drawdown_abs=dd_abs,
        longest_losing_streak=longest_losing_streak(trades),
        avg_hold_hours=round(sum(t.bars_held for t in trades) / len(trades), 1),
        exits=exits,
    )


def by_magic(trades: list[ClosedTrade]) -> dict[int, list[ClosedTrade]]:
    """Split by magic number, which is how A/B/C variants are told apart (D9)."""
    out: dict[int, list[ClosedTrade]] = {}
    for t in trades:
        out.setdefault(t.magic, []).append(t)
    return out
