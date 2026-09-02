"""Walk-forward and Monte Carlo: the two questions a single backtest number cannot answer.

A backtest tells you what a parameter set did on the data you fitted it to. That is the least
interesting thing you can know about it.

- **Walk-forward** asks whether the parameters that won on one stretch keep winning on the next
  stretch they have never seen. The efficiency ratio — out-of-sample result over in-sample result
  — is the honest headline. Above ~0.5 the edge survived; near zero it was curve fitting.
- **Monte Carlo** asks how much of the drawdown was luck of ordering. Shuffling the same trades
  produces equity curves that are all equally plausible, and the 95th percentile drawdown is a far
  better number to size against than the one run that happened.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from tradeapp.backtest.broker import ClosedTrade, split_windows
from tradeapp.backtest.engine import BacktestResult, run_backtest
from tradeapp.contracts import Bar


@dataclass(frozen=True)
class Window:
    index: int
    train_from: object
    train_to: object
    test_from: object
    test_to: object
    train_return_pct: float
    test_return_pct: float
    test_trades: int
    params: dict

    @property
    def efficiency(self) -> float | None:
        """Out-of-sample over in-sample. None when the training half made nothing to compare against."""
        if self.train_return_pct <= 0:
            return None
        return round(self.test_return_pct / self.train_return_pct, 3)


@dataclass
class WalkForwardResult:
    windows: list[Window] = field(default_factory=list)

    @property
    def efficiency(self) -> float | None:
        """Total out-of-sample over total in-sample, which is harder to game than a mean of ratios."""
        train = sum(w.train_return_pct for w in self.windows)
        test = sum(w.test_return_pct for w in self.windows)
        return round(test / train, 3) if train > 0 else None

    @property
    def profitable_windows(self) -> int:
        return sum(1 for w in self.windows if w.test_return_pct > 0)

    def summary(self) -> str:
        if not self.windows:
            return "no walk-forward windows (not enough data)"
        eff = self.efficiency
        return (
            f"{len(self.windows)} windows, {self.profitable_windows} profitable out of sample, "
            f"efficiency {eff if eff is not None else 'n/a'}"
        )


def walk_forward(
    bars: list[Bar],
    build: Callable[[dict], list],
    param_grid: Sequence[dict],
    *,
    train: timedelta = timedelta(days=180),
    test: timedelta = timedelta(days=60),
    step: timedelta = timedelta(days=60),
    **backtest_kwargs,
) -> WalkForwardResult:
    """Fit on each training window, then measure the winner on the untouched window that follows.

    `build(params)` returns the strategy list for one parameter set; `param_grid` is what to try.
    """
    result = WalkForwardResult()
    for i, (train_bars, test_bars) in enumerate(split_windows(bars, train, test, step)):
        best_params, best_train = None, None
        for params in param_grid:
            try:
                run = run_backtest(train_bars, build(params), **backtest_kwargs)
            except ValueError:
                continue  # window too short for the warm-up
            if best_train is None or run.stats.return_pct > best_train.stats.return_pct:
                best_params, best_train = params, run
        if best_params is None or best_train is None:
            continue
        try:
            out = run_backtest(test_bars, build(best_params), **backtest_kwargs)
        except ValueError:
            continue
        result.windows.append(
            Window(
                index=i,
                train_from=train_bars[0].time_utc,
                train_to=train_bars[-1].time_utc,
                test_from=test_bars[0].time_utc,
                test_to=test_bars[-1].time_utc,
                train_return_pct=best_train.stats.return_pct,
                test_return_pct=out.stats.return_pct,
                test_trades=out.stats.trades,
                params=dict(best_params),
            )
        )
    return result


@dataclass(frozen=True)
class MonteCarloResult:
    runs: int
    drawdown_p50: float
    drawdown_p95: float
    drawdown_p99: float
    drawdown_worst: float
    return_p05: float
    return_p50: float
    losing_run_probability: float

    def summary(self) -> str:
        return (
            f"{self.runs} shuffles: drawdown p50 {self.drawdown_p50:.2f}%  p95 {self.drawdown_p95:.2f}%  "
            f"worst {self.drawdown_worst:.2f}%  chance of a losing run {self.losing_run_probability:.0%}"
        )


def monte_carlo(
    trades: list[ClosedTrade],
    start_balance: float,
    runs: int = 1000,
    seed: int = 7,
) -> MonteCarloResult:
    """Reshuffle the same trades many times; every ordering was equally possible.

    The p95 drawdown is the number to size against. The one that actually happened is a sample of
    one, and treating it as the worst case is how accounts get blown up by a normal losing streak.
    """
    nets = [t.net for t in trades]
    if not nets:
        return MonteCarloResult(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    rng = random.Random(seed)
    drawdowns: list[float] = []
    returns: list[float] = []
    for _ in range(runs):
        order = nets[:]
        rng.shuffle(order)
        equity = start_balance
        peak = start_balance
        worst = 0.0
        for net in order:
            equity += net
            peak = max(peak, equity)
            if peak > 0:
                worst = max(worst, (peak - equity) / peak * 100.0)
        drawdowns.append(worst)
        returns.append((equity - start_balance) / start_balance * 100.0)

    drawdowns.sort()
    returns.sort()
    return MonteCarloResult(
        runs=runs,
        drawdown_p50=round(_percentile(drawdowns, 0.50), 3),
        drawdown_p95=round(_percentile(drawdowns, 0.95), 3),
        drawdown_p99=round(_percentile(drawdowns, 0.99), 3),
        drawdown_worst=round(drawdowns[-1], 3),
        return_p05=round(_percentile(returns, 0.05), 3),
        return_p50=round(_percentile(returns, 0.50), 3),
        losing_run_probability=round(sum(1 for r in returns if r < 0) / len(returns), 3),
    )


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def gate_report(result: BacktestResult, mc: MonteCarloResult, wf: WalkForwardResult | None = None) -> dict:
    """The numbers D3 and P2-05 actually check before a strategy may move forward."""
    return {
        "trades": result.stats.trades,
        "return_pct": result.stats.return_pct,
        "profit_factor": result.stats.profit_factor,
        "max_drawdown_pct": result.stats.max_drawdown_pct,
        "monte_carlo_p95_drawdown": mc.drawdown_p95,
        "walk_forward_efficiency": wf.efficiency if wf else None,
        "stopped_early": result.stopped_early,
        "cost_share_of_gross_pct": result.stats.cost_share_of_gross,
    }
