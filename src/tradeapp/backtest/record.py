"""Storing a backtest so it can be argued with later (P4-04).

A backtest that lives only in a terminal scrollback cannot be compared with anything. The weekly
drift report needs the same window, the same costs and the same statistics the live period is
measured with, and RESEARCH.md needs a run id to point at rather than a paragraph of remembered
numbers.

What is stored is the result, not the run: the equity curve (one point per bar) is left out on
purpose, and the closed trades are kept because every drift metric is computed from them.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from tradeapp.backtest.broker import ClosedTrade
from tradeapp.backtest.engine import BacktestResult
from tradeapp.journal import Journal


def trade_rows(trades: list[ClosedTrade]) -> list[dict[str, Any]]:
    """Compact, JSON-safe, and enough to recompute every statistic in the report."""
    return [
        {
            "opened_utc": t.opened_utc.isoformat(),
            "closed_utc": t.closed_utc.isoformat(),
            "side": t.side.value,
            "volume": t.volume,
            "entry": t.entry,
            "exit": t.exit,
            "sl": t.sl,
            "tp": t.tp,
            "net": t.net,
            "gross": t.gross,
            "costs": round(t.commission + t.swap + t.spread_cost, 2),
            "exit_reason": t.exit_reason,
            "magic": t.magic,
            "comment": t.comment,
        }
        for t in trades
    ]


def stats_dict(result: BacktestResult) -> dict[str, Any]:
    out = asdict(result.stats)
    out["cost_share_of_gross"] = result.stats.cost_share_of_gross
    out["net_before_costs"] = result.stats.net_before_costs
    return out


def save_run(
    journal: Journal,
    result: BacktestResult,
    *,
    strategy: str,
    params: dict | None = None,
    costs: dict | None = None,
    label: str | None = None,
    walk_forward: Any = None,
    monte_carlo: Any = None,
    gates: dict | None = None,
) -> int:
    """Write one run and return its id. The id is what RESEARCH.md and the drift report cite."""
    wf = None
    if walk_forward is not None:
        wf = {
            "windows": len(walk_forward.windows),
            "profitable_windows": walk_forward.profitable_windows,
            "profitable_share": walk_forward.profitable_share,
            "efficiency": walk_forward.efficiency,
            "summary": walk_forward.summary(),
            # The windows themselves, because "efficiency 0.5" is a number to argue with and a
            # list of in-sample/out-of-sample pairs is the evidence behind it.
            "rows": [
                {
                    "test_from": w.test_from.isoformat(),
                    "train_return_pct": w.train_return_pct,
                    "test_return_pct": w.test_return_pct,
                    "test_trades": w.test_trades,
                    "params": w.params,
                }
                for w in walk_forward.windows
            ],
        }
    mc = None
    if monte_carlo is not None:
        mc = {**asdict(monte_carlo), "summary": monte_carlo.summary()}

    return journal.backtest(
        label=label,
        strategy=strategy,
        params=params or {},
        symbol=result.symbol,
        timeframe=result.timeframe.value,
        data_from=result.start_utc.replace(tzinfo=None) if result.start_utc else None,
        data_to=result.end_utc.replace(tzinfo=None) if result.end_utc else None,
        bars=result.bars,
        start_balance=result.start_balance,
        end_balance=result.end_balance,
        costs=costs or {},
        stats=stats_dict(result),
        trades=trade_rows(result.trades),
        walk_forward=wf,
        monte_carlo=mc,
        gates=gates,
        killed=result.killed,
        rejections=result.rejections,
    )
