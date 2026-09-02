"""CLI: python -m tradeapp {check|smoke|journal}"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime

from tradeapp.config import load_settings
from tradeapp.contracts import LiveAccountBlocked, Side
from tradeapp.journal import Journal


def _mt5_broker(settings):
    from tradeapp.broker.mt5_bridge import MT5Broker

    return MT5Broker(
        path=settings.mt5_path,
        login=settings.mt5_login,
        password=settings.mt5_password_plain,
        server=settings.mt5_server,
        allow_live=settings.live_enabled,
        timeout_ms=settings.mt5_timeout_ms,
        reference_symbol=settings.reference_symbol,
    )


def cmd_check(args: argparse.Namespace) -> int:
    settings = load_settings()
    journal = Journal(settings.journal_path)
    broker = _mt5_broker(settings)
    print(f"config    {settings.describe()}")
    try:
        acct = broker.connect()
    except LiveAccountBlocked as e:
        print(f"BLOCKED: {e}")
        journal.event("CRIT", "core", "live account blocked", {"error": str(e)})
        return 2
    print(f"account   {acct.login}@{acct.server}")
    print(f"mode      {acct.mode.value}")
    print(f"balance   {acct.balance:.2f} {acct.currency}   equity {acct.equity:.2f}   leverage 1:{acct.leverage}")
    print(f"algo_trading {acct.algo_trading}   (must be True before smoke)")
    sym = broker.symbol_info(args.symbol)
    tick = broker.tick(args.symbol)
    print(
        f"{args.symbol}  bid {tick.bid} ask {tick.ask}  spread {sym.spread_points}pt  stops_level {sym.stops_level_points}pt  vol_min {sym.volume_min}"
    )
    print(f"clock     {broker.server_offset.describe()}")
    print(f"          broker shows {tick.time_server}  =  {tick.time_utc:%Y-%m-%d %H:%M:%S} UTC")
    journal.event(
        "INFO",
        "core",
        "check ok",
        {
            "login": acct.login,
            "mode": acct.mode.value,
            "symbol": args.symbol,
            "spread_points": sym.spread_points,
            "server_utc_offset_min": broker.server_offset.minutes,
            "server_offset_confident": broker.server_offset.confident,
        },
    )
    broker.disconnect()
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    from tradeapp.smoke import run_smoke

    settings = load_settings()
    if args.fake:
        from tradeapp.broker.fake import FakeBroker

        broker = FakeBroker()
        # Simulated runs never touch the real journal: their fills are invented, and mixing them
        # into the record would poison every later report and post-mortem.
        journal = Journal(":memory:" if args.no_db else settings.simulated_journal_path)
    else:
        broker = _mt5_broker(settings)
        journal = Journal(settings.journal_path)
    report = run_smoke(
        broker,
        journal,
        symbol=args.symbol,
        volume=args.volume,
        hold_seconds=args.hold,
        magic=settings.magic_base,
        side=Side.SHORT if args.short else Side.LONG,
    )
    for s in report.steps:
        print(" -", s)
    print("RESULT", "OK" if report.ok else "FAILED", f"ref={report.client_ref}")
    return 0 if report.ok else 1


def cmd_risk(args: argparse.Namespace) -> int:
    """Ask the Risk Engine what it would do with a hypothetical intent, against live prices.

    Read-only by construction: it builds an OrderRequest and prints it, and there is no code path
    from here to the broker. Use it to see the sizing on the account as it stands right now.
    """
    from tradeapp.contracts import Intent
    from tradeapp.risk import RiskContext, RiskEngine, RiskLimits

    settings = load_settings()
    broker = _mt5_broker(settings)
    acct = broker.connect()
    sym = broker.symbol_info(args.symbol)
    tick = broker.tick(args.symbol)
    positions = broker.positions()
    broker.disconnect()

    side = Side.SHORT if args.short else Side.LONG
    entry = tick.bid if side is Side.SHORT else tick.ask
    distance = args.stop_points * sym.point
    stop = round(entry + distance, sym.digits) if side is Side.SHORT else round(entry - distance, sym.digits)
    take = round(entry - 2 * distance, sym.digits) if side is Side.SHORT else round(entry + 2 * distance, sym.digits)

    intent = Intent(
        symbol=args.symbol,
        side=side,
        confidence=args.confidence,
        stop_price=stop,
        take_price=take,
        reason="risk preview from the command line",
    )
    ctx = RiskContext(
        account=acct,
        symbols={args.symbol: sym},
        tick=tick,
        positions=positions,
        now_utc=datetime.now(UTC),
        # A preview has no history to draw on, so it assumes a clean slate: today's equity is also
        # the day's opening and the all-time peak. The live engine reads both from the journal.
        day_start_equity=acct.equity,
        peak_equity=acct.equity,
    )
    decision = RiskEngine(RiskLimits(), magic_base=settings.magic_base).evaluate(intent, args.strategy, ctx)

    print(f"account   {acct.login}@{acct.server} equity {acct.equity:.2f} {acct.currency}")
    print(
        f"intent    {side.value} {args.symbol} entry~{entry} stop {stop} ({args.stop_points:.0f} points) conf {args.confidence}"
    )
    print(f"open      {len(positions)} position(s)")
    print(f"verdict   {decision.verdict.value}" + (f" [{decision.reason.value}]" if decision.reason else ""))
    print(f"          {decision.detail}")
    if decision.order:
        o = decision.order
        print(f"order     {o.volume} lots  sl {o.stop_price}  tp {o.take_price}  magic {o.magic}")
        print("          not sent: this command never reaches the broker")
    return 0 if decision.approved else 1


def cmd_signals(args: argparse.Namespace) -> int:
    """Walk the whole chain on live bars: bars -> context -> strategies -> Risk Engine. Sends nothing."""
    from tradeapp.context import build_context
    from tradeapp.contracts import TF
    from tradeapp.risk import RiskContext, RiskEngine, RiskLimits
    from tradeapp.runtime import StrategyRuntime
    from tradeapp.strategies import create, discover

    settings = load_settings()
    tf = TF(args.tf.upper())
    broker = _mt5_broker(settings)
    acct = broker.connect()
    ctx = build_context(broker, args.symbol, tf, count=args.bars)
    positions = broker.positions()
    broker.disconnect()

    print(f"account   {acct.login}@{acct.server} equity {acct.equity:.2f} {acct.currency}")
    print(f"bars      {len(ctx)} x {tf.value} up to {ctx.bar.time_utc:%Y-%m-%d %H:%M} UTC  close {ctx.close()}")
    ema_f, ema_s = ctx.ema(args.fast), ctx.ema(args.slow)
    print(
        f"indicators EMA{args.fast} {ema_f:.5f}  EMA{args.slow} {ema_s:.5f}  "
        f"ATR14 {ctx.atr(14):.5f}  RSI14 {ctx.rsi(14):.1f}"
    )

    runtime = StrategyRuntime()
    known = discover()
    ids = [args.strategy] if args.strategy else sorted(known)
    for sid in ids:
        params = {"fast": args.fast, "slow": args.slow} if sid == "ema_cross" else {}
        runtime.register(create(sid, **params))

    signals = runtime.on_bar(ctx)
    print(f"strategies {', '.join(ids)} -> {len(signals)} signal(s) on the last closed bar")
    if not signals:
        print("           no cross on this bar; the chain is wired, there is simply nothing to do")
        return 0

    engine = RiskEngine(RiskLimits(), magic_base=settings.magic_base)
    risk_ctx = RiskContext(
        account=acct,
        symbols={args.symbol: ctx.symbol_info},
        tick=ctx.tick,
        positions=positions,
        now_utc=datetime.now(UTC),
        day_start_equity=acct.equity,
        peak_equity=acct.equity,
    )
    for sig in signals:
        i = sig.intent
        print(f"\nsignal    {sig.key}: {i.side.value} stop {i.stop_price} take {i.take_price} conf {i.confidence}")
        print(f"          {i.reason}")
        d = engine.evaluate(i, sig.strategy_id, risk_ctx, variant=sig.variant)
        print(f"verdict   {d.verdict.value}" + (f" [{d.reason.value}]" if d.reason else ""))
        print(f"          {d.detail}")
    print("\nnothing was sent: this command has no path to the broker's trading calls")
    return 0


def cmd_data(args: argparse.Namespace) -> int:
    """Pull history from MT5 into the local store, or report what is already there."""
    from tradeapp.contracts import TF
    from tradeapp.data import BarStore

    settings = load_settings()
    store = BarStore(args.db)

    if args.action == "info":
        rows = store.symbols()
        if not rows:
            print("store is empty; run: python -m tradeapp data sync")
            return 0
        for symbol, tf_name, count in rows:
            tf = TF(tf_name)
            first, last = store.range(symbol, tf)
            gaps = store.gaps(symbol, tf)
            print(f"{symbol:<10} {tf_name:<4} {count:>7} bars  {first:%Y-%m-%d} → {last:%Y-%m-%d}  {len(gaps)} gap(s)")
            for gap in gaps[: args.show_gaps]:
                print(f"           gap {gap}")
        return 0

    tf = TF(args.tf.upper())
    broker = _mt5_broker(settings)
    broker.connect()
    report = store.sync_from_broker(broker, args.symbol, tf, count=args.count)
    broker.disconnect()
    print(report)
    gaps = store.gaps(args.symbol, tf)
    print(f"gaps      {len(gaps)} (weekends excluded)")
    for gap in gaps[: args.show_gaps]:
        print(f"          {gap}")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    """Replay stored history through the live decision path."""
    from tradeapp.backtest import CostModel, gate_report, monte_carlo, run_backtest
    from tradeapp.contracts import TF
    from tradeapp.data import BarStore
    from tradeapp.strategies import create

    tf = TF(args.tf.upper())
    bars = BarStore(args.db).load(args.symbol, tf)
    if len(bars) <= args.warmup + 1:
        print(f"only {len(bars)} bars stored for {args.symbol} {tf.value}; run: python -m tradeapp data sync")
        return 1

    costs = CostModel(
        spread_points=args.spread,
        use_bar_spread=not args.flat_spread,
        slippage_points=args.slippage,
        commission_per_lot_round_trip=args.commission,
    )
    result = run_backtest(
        bars,
        [create(args.strategy)],
        symbol=args.symbol,
        timeframe=tf,
        costs=costs,
        start_balance=args.balance,
        warmup=args.warmup,
    )

    print(f"data      {len(bars)} bars  {bars[0].time_utc:%Y-%m-%d} → {bars[-1].time_utc:%Y-%m-%d}")
    print(
        f"costs     spread {'from bars' if not args.flat_spread else args.spread} · slippage {args.slippage}pt · commission {args.commission}/lot"
    )
    print(f"result    {result.summary()}")
    s = result.stats
    print(f"          win {s.wins}/{s.trades}  PF {s.profit_factor:.2f}  expectancy {s.expectancy:+.2f}")
    print(
        f"          maxDD {s.max_drawdown_pct:.2f}% ({s.max_drawdown_abs:.2f})  worst losing streak {s.longest_losing_streak}"
    )
    print(f"          avg hold {s.avg_hold_hours:.1f}h  exits {s.exits}")
    print(f"costs     {s.costs:.2f} total (spread {s.spread_cost:.2f})  net before costs {s.net_before_costs:+.2f}")
    if result.rejections:
        print(f"rejected  {result.rejections}")

    wf = None
    if args.walk_forward:
        from datetime import timedelta

        from tradeapp.backtest import walk_forward

        grid = [{"fast": f, "slow": sl} for f in (10, 20, 30) for sl in (50, 100) if f < sl]
        print(f"\nwalk-forward over {len(grid)} parameter sets, train 180d / test 60d ...")
        wf = walk_forward(
            bars,
            build=lambda prm: [create(args.strategy, **prm)],
            param_grid=grid,
            train=timedelta(days=180),
            test=timedelta(days=60),
            step=timedelta(days=60),
            symbol=args.symbol,
            timeframe=tf,
            costs=costs,
            start_balance=args.balance,
            warmup=args.warmup,
        )
        print(f"          {wf.summary()}")
        for w in wf.windows[: args.show_windows]:
            print(
                f"          {w.test_from:%Y-%m-%d} in {w.train_return_pct:+.2f}% -> out {w.test_return_pct:+.2f}% "
                f"({w.test_trades} trades) {w.params}"
            )

    if s.trades:
        mc = monte_carlo(result.trades, result.start_balance, runs=args.monte_carlo)
        print(f"montecarlo {mc.summary()}")
        print(f"gates     {gate_report(result, mc, wf)}")
    return 0


def cmd_lifecycle(args: argparse.Namespace) -> int:
    """Where each strategy stands, and what is between it and the next step."""
    from tradeapp.lifecycle import Evidence, Lifecycle, LifecycleState, PromotionRefused, evaluate

    settings = load_settings()
    journal = Journal(settings.journal_path)
    lc = Lifecycle(journal)

    if args.action == "list":
        known = lc.all_states()
        from tradeapp.strategies import discover

        for sid in sorted(set(discover()) | set(known)):
            print(f"{sid:<16} {known.get(sid, LifecycleState.RESEARCH.value)}")
        if not known:
            print("\nnothing promoted yet; everything starts in research")
        return 0

    if args.action == "show":
        record = lc.record(args.strategy)
        print(f"{args.strategy}: {record['state']}")
        for h in record.get("history", []):
            print(f"  {h['at_utc'][:19]}  {h['from']} → {h['to']}  {h['reason']}")
        result = evaluate(lc.state(args.strategy), Evidence())
        if result.to is not result.frm:
            print(f"\nto reach {result.to.value}, with no evidence supplied yet:")
            for gate in result.gates:
                print(f"  {gate}")
        return 0

    if args.action == "retire":
        if not args.reason:
            print("retiring needs --reason; it goes in the journal")
            return 1
        print(f"{args.strategy}: {lc.retire(args.strategy, args.reason).value}")
        return 0

    if args.action == "demote":
        if not args.reason:
            print("demoting needs --reason")
            return 1
        print(f"{args.strategy}: {lc.demote_to_research(args.strategy, args.reason).value}")
        return 0

    # promote, using evidence from a fresh backtest of the stored history
    from tradeapp.backtest import CostModel, monte_carlo, run_backtest
    from tradeapp.contracts import TF
    from tradeapp.data import BarStore
    from tradeapp.lifecycle import evidence_from_backtest
    from tradeapp.strategies import create

    tf = TF(args.tf.upper())
    bars = BarStore(args.db).load(args.symbol, tf)
    if len(bars) <= 101:
        print(f"only {len(bars)} bars stored; run: python -m tradeapp data sync")
        return 1
    costs = CostModel()
    result = run_backtest(bars, [create(args.strategy)], symbol=args.symbol, timeframe=tf, costs=costs, warmup=100)
    mc = monte_carlo(result.trades, result.start_balance, runs=500)
    evidence = evidence_from_backtest(result, monte_carlo=mc)
    print(f"evidence  {result.stats.summary()}")
    try:
        state = lc.promote(args.strategy, evidence)
        print(f"promoted  {args.strategy} → {state.value}")
        return 0
    except PromotionRefused as e:
        print(str(e))
        return 1


def cmd_run(args: argparse.Namespace) -> int:
    """Start the trading loop. This one really trades, on whatever account the profile points at."""
    from tradeapp.contracts import TF
    from tradeapp.core import Core, CoreConfig
    from tradeapp.risk import RiskLimits
    from tradeapp.runtime import StrategyRuntime
    from tradeapp.strategies import create, discover

    settings = load_settings()
    tf = TF(args.tf.upper())

    if args.fake:
        from tradeapp.broker.fake import FakeBroker

        broker = FakeBroker()
        journal = Journal(settings.simulated_journal_path)
    else:
        broker = _mt5_broker(settings)
        journal = Journal(settings.journal_path)

    runtime = StrategyRuntime(journal)
    ids = [args.strategy] if args.strategy else sorted(discover())
    for sid in ids:
        runtime.register(create(sid))

    core = Core(
        broker,
        journal,
        runtime=runtime,
        config=CoreConfig(
            symbol=args.symbol, timeframe=tf, tick_interval_s=args.interval, reconcile_every_s=args.reconcile_every
        ),
        limits=RiskLimits(),
        magic_base=settings.magic_base,
    )

    where = settings.simulated_journal_path if args.fake else settings.journal_path
    print(f"config    profile={settings.profile.value} journal={where} live_enabled={settings.live_enabled}")
    print(f"strategies {', '.join(ids)} on {args.symbol} {tf.value}" + ("  [simulated broker]" if args.fake else ""))
    try:
        acct = core.start()
    except LiveAccountBlocked as e:
        print(f"BLOCKED: {e}")
        return 2
    print(f"account   {acct.login}@{acct.server} {acct.mode.value} equity {acct.equity:.2f} {acct.currency}")
    print(f"running   tick every {args.interval}s, reconcile every {args.reconcile_every}s — Ctrl+C to stop\n")

    ticks = 0
    try:
        while args.max_ticks is None or ticks < args.max_ticks:
            report = core.tick()
            ticks += 1
            flags = "".join(
                [
                    "K" if report.killed else "",
                    "F" if report.frozen else "",
                    "R" if report.reconciled else "",
                    "B" if report.new_bar else "",
                ]
            )
            line = (
                f"{report.at_utc:%H:%M:%S} {report.state.value:<8} eq {report.equity:>10.2f} "
                f"[{flags:<4}] signals {report.signals} sent {report.sent}"
            )
            print(line)
            for note in report.notes:
                print(f"           {note}")
            if report.killed:
                print("\nKILLED — unlock by hand before trading resumes")
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopping on Ctrl+C")
    finally:
        core.shutdown()
    print("\nopen positions are left alone; their stops are at the broker (rule 03)")
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Compare what the broker actually holds against what the journal believes."""
    from tradeapp.reconcile import Reconciler

    settings = load_settings()
    journal = Journal(settings.journal_path)
    broker = _mt5_broker(settings)
    acct = broker.connect()
    rec = Reconciler(broker, journal)
    result = rec.run()
    broker.disconnect()

    print(f"account   {acct.login}@{acct.server}")
    print(f"result    {result.summary()}")
    for pos in result.orphans:
        print(f"  ORPHAN      {pos.ticket} {pos.symbol} {pos.side.value} {pos.volume} sl {pos.sl} magic {pos.magic}")
    for pos in result.unprotected:
        print(f"  NO STOP     {pos.ticket} {pos.symbol} {pos.volume}  (rule 03)")
    for ticket in result.ghosts:
        print(f"  CLOSED      {ticket} closed at the broker without us; recorded")
    for pos in result.foreign:
        print(f"  NOT OURS    {pos.ticket} {pos.symbol} magic {pos.magic}")
    if rec.frozen:
        print(f"\nFROZEN    {rec.freeze_reason}")
        print("          no new entries until this is resolved by hand")
    return 0 if result.ok else 1


def cmd_drill(args: argparse.Namespace) -> int:
    """Fire every kill-switch trigger on purpose and report what happened."""
    from tradeapp.drill import run_drills

    settings = load_settings()
    journal = Journal(settings.simulated_journal_path)
    results = run_drills(journal)
    width = max(len(r.name) for r in results)
    for r in results:
        print(f"  {'PASS' if r.passed else 'FAIL'}  {r.name:<{width}}  {r.got}")
    passed = sum(1 for r in results if r.passed)
    print(f"\n{passed}/{len(results)} drills passed")
    print("simulated broker only: pulling the network cable is P4-01 and gate 5 in DECISIONS D3")
    return 0 if passed == len(results) else 1


def cmd_journal(args: argparse.Namespace) -> int:
    settings = load_settings()
    journal = Journal(settings.simulated_journal_path if args.fake else settings.journal_path)
    for e in journal.tail_events(args.tail):
        print(f"{e.ts_utc:%Y-%m-%d %H:%M:%S}  {e.severity:<4} {e.source:<10} {e.message}  {e.data or ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tradeapp")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="connect to MT5 and print account / symbol info (no orders)")
    c.add_argument("--symbol", default="EURUSD")
    c.set_defaults(fn=cmd_check)

    s = sub.add_parser("smoke", help="open one small DEMO position, verify SL at broker, close it")
    s.add_argument("--symbol", default="EURUSD")
    s.add_argument("--volume", type=float, default=0.01)
    s.add_argument("--hold", type=float, default=3.0, help="seconds to hold before closing")
    s.add_argument("--short", action="store_true")
    s.add_argument("--fake", action="store_true", help="use FakeBroker instead of MT5")
    s.add_argument("--no-db", action="store_true", help="with --fake: use an in-memory journal")
    s.set_defaults(fn=cmd_smoke)

    r = sub.add_parser("risk", help="show what the Risk Engine would do with an intent (sends nothing)")
    r.add_argument("--symbol", default="EURUSD")
    r.add_argument("--stop-points", type=float, default=200.0, help="stop distance in points")
    r.add_argument("--confidence", type=float, default=1.0)
    r.add_argument("--strategy", default="preview")
    r.add_argument("--short", action="store_true")
    r.set_defaults(fn=cmd_risk)

    g = sub.add_parser("signals", help="run strategies over live bars and show the Risk Engine verdict (sends nothing)")
    g.add_argument("--symbol", default="EURUSD")
    g.add_argument("--tf", default="H4", help="M1 M5 M15 M30 H1 H4 D1")
    g.add_argument("--bars", type=int, default=300)
    g.add_argument("--strategy", default=None, help="one strategy id; default runs all registered")
    g.add_argument("--fast", type=int, default=20)
    g.add_argument("--slow", type=int, default=50)
    g.set_defaults(fn=cmd_signals)

    dt = sub.add_parser("data", help="sync and inspect the local history store")
    dt.add_argument("action", choices=["sync", "info"])
    dt.add_argument("--symbol", default="EURUSD")
    dt.add_argument("--tf", default="H4")
    dt.add_argument("--count", type=int, default=5000, help="bars to pull from MT5")
    dt.add_argument("--db", default="data/history.db")
    dt.add_argument("--show-gaps", type=int, default=5)
    dt.set_defaults(fn=cmd_data)

    bt = sub.add_parser("backtest", help="replay stored history through the live decision path")
    bt.add_argument("--symbol", default="EURUSD")
    bt.add_argument("--tf", default="H4")
    bt.add_argument("--strategy", default="ema_cross")
    bt.add_argument("--balance", type=float, default=10_000.0)
    bt.add_argument("--warmup", type=int, default=100)
    bt.add_argument("--spread", type=int, default=20, help="fallback spread in points (XM measured 20)")
    bt.add_argument("--flat-spread", action="store_true", help="ignore per-bar spread and use --spread")
    bt.add_argument("--slippage", type=float, default=0.3)
    bt.add_argument("--commission", type=float, default=0.0, help="per lot, round trip")
    bt.add_argument("--monte-carlo", type=int, default=1000, help="shuffles")
    bt.add_argument("--walk-forward", action="store_true", help="also fit and test on rolling windows")
    bt.add_argument("--show-windows", type=int, default=8)
    bt.add_argument("--db", default="data/history.db")
    bt.set_defaults(fn=cmd_backtest)

    lf = sub.add_parser("lifecycle", help="where each strategy stands on the way to real money")
    lf.add_argument("action", choices=["list", "show", "promote", "retire", "demote"])
    lf.add_argument("--strategy", default="ema_cross")
    lf.add_argument("--reason", default="")
    lf.add_argument("--symbol", default="EURUSD")
    lf.add_argument("--tf", default="H4")
    lf.add_argument("--db", default="data/history.db")
    lf.set_defaults(fn=cmd_lifecycle)

    rn = sub.add_parser("run", help="start the trading loop (this one really trades)")
    rn.add_argument("--symbol", default="EURUSD")
    rn.add_argument("--tf", default="H4")
    rn.add_argument("--strategy", default=None)
    rn.add_argument("--interval", type=float, default=5.0, help="seconds between ticks")
    rn.add_argument("--reconcile-every", type=float, default=60.0)
    rn.add_argument("--max-ticks", type=int, default=None)
    rn.add_argument("--fake", action="store_true", help="drive a simulated broker instead of MT5")
    rn.set_defaults(fn=cmd_run)

    rc = sub.add_parser("reconcile", help="compare broker positions against the journal")
    rc.set_defaults(fn=cmd_reconcile)

    d = sub.add_parser("drill", help="fire every kill-switch trigger against a simulated broker")
    d.set_defaults(fn=cmd_drill)

    j = sub.add_parser("journal", help="print the last events")
    j.add_argument("--tail", type=int, default=20)
    j.add_argument("--fake", action="store_true", help="read the simulated-runs journal instead")
    j.set_defaults(fn=cmd_journal)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
