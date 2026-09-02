"""CLI: python -m tradeapp {check|smoke|journal}"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tradeapp.config import load_settings
from tradeapp.contracts import LiveAccountBlocked, Side
from tradeapp.journal import Journal


def fake_journal_path(journal_db) -> Path:
    """Sibling of the real journal, e.g. data/journal.db -> data/journal-fake.db."""
    p = Path(journal_db)
    return p.with_name(f"{p.stem}-fake{p.suffix}")


def _mt5_broker(settings):
    from tradeapp.broker.mt5_bridge import MT5Broker

    return MT5Broker(
        path=settings.mt5_path,
        login=settings.mt5_login,
        password=settings.mt5_password_plain,
        server=settings.mt5_server,
        allow_live=settings.allow_live,
        timeout_ms=settings.mt5_timeout_ms,
    )


def cmd_check(args: argparse.Namespace) -> int:
    settings = load_settings()
    journal = Journal(settings.journal_db)
    broker = _mt5_broker(settings)
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
        journal = Journal(":memory:" if args.no_db else fake_journal_path(settings.journal_db))
    else:
        broker = _mt5_broker(settings)
        journal = Journal(settings.journal_db)
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


def cmd_journal(args: argparse.Namespace) -> int:
    settings = load_settings()
    journal = Journal(fake_journal_path(settings.journal_db) if args.fake else settings.journal_db)
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

    j = sub.add_parser("journal", help="print the last events")
    j.add_argument("--tail", type=int, default=20)
    j.add_argument("--fake", action="store_true", help="read the simulated-runs journal instead")
    j.set_defaults(fn=cmd_journal)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
