"""CLI: python -m tradeapp {check|smoke|journal}"""

from __future__ import annotations

import argparse
import sys
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

    j = sub.add_parser("journal", help="print the last events")
    j.add_argument("--tail", type=int, default=20)
    j.add_argument("--fake", action="store_true", help="read the simulated-runs journal instead")
    j.set_defaults(fn=cmd_journal)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
