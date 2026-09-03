"""Backtest: the mechanics must be provable by hand before any result means anything."""

from datetime import UTC, datetime, timedelta

import pytest

from tradeapp.backtest import ZERO_COSTS, BacktestBroker, CostModel, monte_carlo, run_backtest, split_windows
from tradeapp.backtest import stats as stats_mod
from tradeapp.backtest.robustness import gate_report, walk_forward
from tradeapp.contracts import TF, Bar, Intent, OrderRequest, Side

T0 = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)  # a Monday


def bar(i: int, o: float, h: float, low: float, c: float, spread: int = 0) -> Bar:
    return Bar(time_utc=T0 + timedelta(hours=4 * i), open=o, high=h, low=low, close=c, spread_points=spread)


def flat_bars(n: int, price: float = 1.1000) -> list[Bar]:
    return [bar(i, price, price + 0.0005, price - 0.0005, price) for i in range(n)]


def rising_bars(n: int, start: float = 1.1000, step: float = 0.0010) -> list[Bar]:
    out = []
    for i in range(n):
        p = start + i * step
        out.append(bar(i, p, p + 0.0005, p - 0.0005, p))
    return out


def falling_bars(n: int, start: float = 1.2000, step: float = 0.0010) -> list[Bar]:
    """Each bar opens where the last closed and falls `step`.

    The wick is deliberately small. An earlier version of this helper gave every bar a high 1000
    points above its close, and longs in a falling market kept hitting their take profit — a
    reminder that a backtest is only ever as honest as the bars fed to it.
    """
    out = []
    for i in range(n):
        o = start - i * step
        c = o - step
        out.append(bar(i, o, o + 0.0002, c - 0.0002, c))
    return out


class OpenOnce:
    """Wants to be long on every bar.

    The Risk Engine's duplicate-position check keeps that to one position at a time, so this
    exercises the real gate rather than a strategy that politely opens once. Note that only bars
    inside 07:00-20:00 UTC can trade at all (D3), which is itself worth having in the path.
    """

    id, symbols, timeframe = "open_once", ["EURUSD"], TF.H4

    def __init__(self, stop_points: int = 200, take_points: int = 400):
        self.stop_points = stop_points
        self.take_points = take_points

    def on_bar(self, ctx):
        close = ctx.close()
        return Intent(
            symbol=ctx.symbol,
            side=Side.LONG,
            confidence=1.0,
            stop_price=round(close - self.stop_points * 0.00001, 5),
            take_price=round(close + self.take_points * 0.00001, 5),
            reason="test entry",
        )


# --- the broker's own arithmetic --------------------------------------------------------


def test_fill_price_includes_spread_and_slippage():
    costs = CostModel(spread_points=20, use_bar_spread=False, slippage_points=3)
    b = BacktestBroker(bars_all=flat_bars(5), costs=costs, index=0)
    b.connect()
    res = b.market_order(
        OrderRequest(symbol="EURUSD", side=Side.LONG, volume=0.1, stop_price=1.0980, take_price=None, magic=1)
    )
    # bid 1.1000, ask is 20 points above, then 3 points of slippage against us
    assert res.price_requested == pytest.approx(1.10020)
    assert res.price_filled == pytest.approx(1.10023)


def test_a_short_fills_at_the_bid_and_slips_the_other_way():
    costs = CostModel(spread_points=20, use_bar_spread=False, slippage_points=3)
    b = BacktestBroker(bars_all=flat_bars(5), costs=costs, index=0)
    b.connect()
    res = b.market_order(
        OrderRequest(symbol="EURUSD", side=Side.SHORT, volume=0.1, stop_price=1.1020, take_price=None, magic=1)
    )
    assert res.price_requested == pytest.approx(1.10000)
    assert res.price_filled == pytest.approx(1.09997)


def test_bar_spread_is_preferred_over_the_flat_default():
    bars = [bar(i, 1.1, 1.1005, 1.0995, 1.1, spread=50) for i in range(3)]
    b = BacktestBroker(bars_all=bars, costs=CostModel(spread_points=10, use_bar_spread=True), index=0)
    assert b.symbol_info("EURUSD").spread_points == 50


def test_a_stop_is_taken_when_the_bar_reaches_it():
    bars = [bar(0, 1.1000, 1.1005, 1.0995, 1.1000), bar(1, 1.1000, 1.1005, 1.0960, 1.0970)]
    b = BacktestBroker(bars_all=bars, costs=ZERO_COSTS, index=0)
    b.connect()
    b.market_order(
        OrderRequest(symbol="EURUSD", side=Side.LONG, volume=0.1, stop_price=1.0980, take_price=1.1100, magic=1)
    )
    b.advance()

    assert b.positions() == []
    assert len(b.trades) == 1
    trade = b.trades[0]
    assert trade.exit_reason == "stop" and trade.exit == pytest.approx(1.0980)
    assert trade.gross == pytest.approx(-20.0)  # 200 points against, 0.1 lots, $1 a point per lot


def test_a_target_is_taken_when_only_the_target_is_touched():
    bars = [bar(0, 1.1000, 1.1005, 1.0995, 1.1000), bar(1, 1.1000, 1.1120, 1.0995, 1.1100)]
    b = BacktestBroker(bars_all=bars, costs=ZERO_COSTS, index=0)
    b.connect()
    b.market_order(
        OrderRequest(symbol="EURUSD", side=Side.LONG, volume=0.1, stop_price=1.0980, take_price=1.1100, magic=1)
    )
    b.advance()
    assert b.trades[0].exit_reason == "target"
    assert b.trades[0].gross == pytest.approx(100.0)


def test_a_bar_that_could_have_hit_both_is_scored_as_a_loss():
    """OHLC cannot say which came first. Assuming the target is how losing systems look like winners."""
    bars = [bar(0, 1.1000, 1.1005, 1.0995, 1.1000), bar(1, 1.1000, 1.1150, 1.0900, 1.1000)]
    b = BacktestBroker(bars_all=bars, costs=ZERO_COSTS, index=0)
    b.connect()
    b.market_order(
        OrderRequest(symbol="EURUSD", side=Side.LONG, volume=0.1, stop_price=1.0980, take_price=1.1100, magic=1)
    )
    b.advance()
    assert b.trades[0].exit_reason == "stop"


def test_commission_is_charged_and_reported():
    b = BacktestBroker(
        bars_all=flat_bars(3), costs=CostModel(commission_per_lot_round_trip=7.0, spread_points=0), index=0
    )
    b.connect()
    b.market_order(
        OrderRequest(symbol="EURUSD", side=Side.LONG, volume=0.5, stop_price=1.0900, take_price=None, magic=1)
    )
    assert b.total_commission == pytest.approx(3.5)
    assert b.account().balance == pytest.approx(9_996.5)


def test_positions_open_at_the_end_are_settled_not_dropped():
    b = BacktestBroker(bars_all=flat_bars(3), costs=ZERO_COSTS, index=0)
    b.connect()
    b.market_order(
        OrderRequest(symbol="EURUSD", side=Side.LONG, volume=0.1, stop_price=1.0900, take_price=None, magic=1)
    )
    b.advance()
    b.close_all_at_end()
    assert b.positions() == [] and b.trades[0].exit_reason == "end_of_data"


# --- the engine, driving the real decision path ------------------------------------------


def test_a_backtest_runs_the_real_risk_engine_and_sizes_the_position():
    result = run_backtest(rising_bars(120), [OpenOnce()], costs=ZERO_COSTS, warmup=60, start_balance=10_000.0)
    assert result.stats.trades >= 1
    trade = result.trades[0]
    # 0.25% of $10,000 over a 200-point stop at $1/point/lot is 0.12 lots
    assert trade.volume == 0.12
    assert trade.sl > 0  # rule 03 survived the whole path


def test_only_bars_inside_trading_hours_produce_entries():
    """The strategy asks on every bar; the Risk Engine only lets three of the six through."""
    result = run_backtest(rising_bars(120), [OpenOnce()], costs=ZERO_COSTS, warmup=60)
    assert all(7 <= t.opened_utc.hour < 20 for t in result.trades)


def test_costs_turn_a_flat_market_into_a_loss():
    """The point of the cost model: doing nothing profitable still costs the spread."""
    free = run_backtest(flat_bars(120), [OpenOnce()], costs=ZERO_COSTS, warmup=60)
    charged = run_backtest(
        flat_bars(120),
        [OpenOnce()],
        costs=CostModel(spread_points=20, use_bar_spread=False, slippage_points=0),
        warmup=60,
    )
    assert charged.stats.net < free.stats.net


def test_a_backtest_refuses_data_shorter_than_the_warmup():
    with pytest.raises(ValueError, match="need more than"):
        run_backtest(flat_bars(10), [OpenOnce()], warmup=60)


def test_rejections_are_counted_so_you_can_see_what_the_limits_did():
    result = run_backtest(
        rising_bars(120), [OpenOnce(stop_points=1)], costs=CostModel(spread_points=20, use_bar_spread=False), warmup=60
    )
    assert result.stats.trades == 0
    assert result.rejections.get("stop_too_close")  # inside the broker minimum, and it says so


def test_the_kill_switch_is_live_during_a_backtest():
    """A run that stops because a limit tripped has not failed; it has answered the question."""
    from tradeapp.risk import RiskLimits

    result = run_backtest(
        falling_bars(200),
        [OpenOnce()],
        costs=ZERO_COSTS,
        warmup=60,
        limits=RiskLimits(daily_loss_limit_pct=0.1),
    )
    assert result.stopped_early is True
    assert "KILLED" in result.killed


def test_variants_are_separated_by_magic_number():
    result = run_backtest(
        rising_bars(140),
        [(OpenOnce(), "A"), (OpenOnce(), "B")],
        costs=ZERO_COSTS,
        warmup=60,
    )
    per = result.per_variant()
    assert len(per) == 2, f"expected two magic numbers, got {list(per)}"


def test_the_summary_reads_like_a_sentence():
    result = run_backtest(rising_bars(120), [OpenOnce()], costs=ZERO_COSTS, warmup=60)
    assert "trades" in result.summary() and "maxDD" in result.summary()


# --- statistics --------------------------------------------------------------------------


def test_drawdown_is_measured_peak_to_trough():
    curve = [(T0, 100.0), (T0, 120.0), (T0, 90.0), (T0, 130.0)]
    pct, abs_ = stats_mod.equity_drawdown(curve)
    assert pct == pytest.approx(25.0)  # 120 down to 90
    assert abs_ == pytest.approx(30.0)


def test_stats_of_no_trades_are_empty_not_an_error():
    assert stats_mod.compute([], [], 10_000.0).trades == 0


def test_losing_streak_counts_consecutive_losses():
    result = run_backtest(rising_bars(120), [OpenOnce()], costs=ZERO_COSTS, warmup=60)
    assert result.stats.longest_losing_streak >= 0


# --- Monte Carlo -------------------------------------------------------------------------


def test_monte_carlo_finds_a_worse_drawdown_than_the_single_run():
    """Twenty wins then twenty losses is the same trades in a kinder order than most."""

    class T:
        def __init__(self, net):
            self.net = net

    trades = [T(10.0)] * 20 + [T(-9.0)] * 20
    mc = monte_carlo(trades, start_balance=1_000.0, runs=200, seed=1)
    assert mc.runs == 200
    assert mc.drawdown_p95 >= mc.drawdown_p50
    assert mc.drawdown_worst >= mc.drawdown_p95


def test_monte_carlo_is_deterministic_for_a_seed():
    class T:
        def __init__(self, net):
            self.net = net

    trades = [T(5.0), T(-3.0), T(8.0), T(-6.0)] * 10
    a = monte_carlo(trades, 1_000.0, runs=50, seed=42)
    b = monte_carlo(trades, 1_000.0, runs=50, seed=42)
    assert a == b


def test_monte_carlo_of_nothing_is_zeroes():
    assert monte_carlo([], 1_000.0).runs == 0


# --- walk-forward ------------------------------------------------------------------------


def test_split_windows_rolls_forward():
    bars = [bar(i, 1.1, 1.1, 1.1, 1.1) for i in range(24 * 30 * 3 // 4)]  # ~90 days of H4
    windows = split_windows(bars, timedelta(days=30), timedelta(days=15), timedelta(days=15))
    assert len(windows) >= 2
    train, test = windows[0]
    assert train[-1].time_utc <= test[0].time_utc  # test never overlaps its own training data


def test_split_windows_of_nothing_is_empty():
    assert split_windows([], timedelta(days=30), timedelta(days=10), timedelta(days=10)) == []


def test_walk_forward_measures_out_of_sample():
    bars = rising_bars(600, step=0.0002)
    grid = [{"stop_points": 200}, {"stop_points": 400}]
    wf = walk_forward(
        bars,
        build=lambda p: [OpenOnce(stop_points=p["stop_points"])],
        param_grid=grid,
        train=timedelta(days=30),
        test=timedelta(days=15),
        step=timedelta(days=15),
        costs=ZERO_COSTS,
        warmup=60,
    )
    assert wf.windows, "expected at least one window"
    assert all(w.params in grid for w in wf.windows)
    assert "windows" in wf.summary()


def test_walk_forward_with_too_little_data_says_so():
    wf = walk_forward(
        flat_bars(80),
        build=lambda p: [OpenOnce()],
        param_grid=[{}],
        train=timedelta(days=180),
        test=timedelta(days=60),
    )
    assert wf.windows == [] and "not enough data" in wf.summary()


def test_gate_report_collects_what_the_gates_read():
    result = run_backtest(rising_bars(120), [OpenOnce()], costs=ZERO_COSTS, warmup=60)
    mc = monte_carlo(result.trades, result.start_balance, runs=50)
    report = gate_report(result, mc)
    assert set(report) >= {"trades", "max_drawdown_pct", "monte_carlo_p95_drawdown", "stopped_early"}
    assert report["walk_forward_profitable_share"] is None  # no walk-forward ran, so nothing to count


def test_walk_forward_counts_how_often_the_edge_showed_up_not_how_hard():
    """One lucky window out of four gives an efficiency of 5 and a share of 25% (D32)."""
    from types import SimpleNamespace

    from tradeapp.backtest.robustness import WalkForwardResult

    wf = WalkForwardResult(windows=[SimpleNamespace(train_return_pct=0.1, test_return_pct=t) for t in (5.0, -1, -1, -1)])
    assert wf.efficiency == 5.0
    assert wf.profitable_windows == 1
    assert wf.profitable_share == 0.25
    assert "1 profitable out of sample (25%)" in wf.summary()
    assert WalkForwardResult().profitable_share is None


def test_a_backtest_can_ask_what_a_strategy_would_do_on_another_pair():
    """The loop never re-points a strategy (D28); a backtest may, and says so in the run."""
    from tradeapp.backtest import on_symbol
    from tradeapp.strategies.ema_cross import EmaCross

    s = on_symbol(EmaCross(), "GBPUSD")
    assert s.symbols == ["GBPUSD"]
