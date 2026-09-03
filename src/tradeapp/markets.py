"""Which markets the bot trades, and who is allowed to change that (D29).

D28 made the loop work in markets and took the list from what the strategies declare, because a
strategy knows which symbols it was written and tested for. That is still the default. What this
module adds is a way for the **owner** to say otherwise from the UI, without turning the ladder in
`lifecycle.py` into a suggestion.

Three rules keep that honest:

- **Turning a market off is free.** It stops new decisions there; open positions keep the stops
  they have at the broker, and reconcile still watches them. Nothing about that needs a gate.
- **Adding a market needs stored history.** A market with no bars cannot be backtested, and a
  market that cannot be backtested cannot earn its way up the ladder. Refusing here is what makes
  "add a pair" different from "gamble on a pair".
- **Adding a market demotes the strategy to `research`.** A strategy that passed its gates on
  EURUSD H4 has proved nothing about GBPUSD M1. D3 already demotes on a parameter change; a new
  market is a bigger change than a parameter. By D26 that also means it cannot touch real money
  until it climbs back, which is the entire point.

The book lives in the journal's `state` table, so it survives a restart and every change is
journaled next to the trades it caused.
"""

from __future__ import annotations

from dataclasses import dataclass

from tradeapp.contracts import TF
from tradeapp.core import Market
from tradeapp.journal import Journal

SOURCE = "markets"
STATE_KEY = "markets:book"


class MarketRefused(Exception):
    """Raised with a reason a human can act on."""


@dataclass(frozen=True)
class MarketRow:
    """One market as the UI sees it."""

    symbol: str
    timeframe: str
    strategy: str
    declared: bool  # the strategy itself says it trades this
    enabled: bool
    bars: int = 0
    first_utc: str | None = None
    last_utc: str | None = None

    @property
    def market(self) -> Market:
        return Market(self.symbol, TF(self.timeframe))

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "strategy": self.strategy,
            "declared": self.declared,
            "enabled": self.enabled,
            "bars": self.bars,
            "first_utc": self.first_utc,
            "last_utc": self.last_utc,
        }


class MarketBook:
    """The owner's overrides on top of what the strategies declare."""

    def __init__(self, journal: Journal, store=None) -> None:
        self.journal = journal
        self.store = store  # a BarStore, or None when history cannot be checked

    # --- reading ------------------------------------------------------------------

    def _raw(self) -> dict:
        raw = self.journal.get_state(STATE_KEY) or {}
        return {"disabled": list(raw.get("disabled", [])), "added": list(raw.get("added", []))}

    def _save(self, book: dict) -> None:
        self.journal.set_state(STATE_KEY, book)

    @staticmethod
    def _id(strategy: str, symbol: str, timeframe: str) -> str:
        return f"{strategy}|{symbol}|{timeframe}"

    def rows(self, declared: dict[str, list[Market]]) -> list[MarketRow]:
        """Every market the UI should show: declared by a strategy, or added by the owner."""
        book = self._raw()
        disabled = set(book["disabled"])
        rows: dict[str, MarketRow] = {}

        for strategy, markets in declared.items():
            for market in markets:
                key = self._id(strategy, market.symbol, market.timeframe.value)
                rows[key] = MarketRow(
                    symbol=market.symbol,
                    timeframe=market.timeframe.value,
                    strategy=strategy,
                    declared=True,
                    enabled=key not in disabled,
                )
        for entry in book["added"]:
            key = self._id(entry["strategy"], entry["symbol"], entry["timeframe"])
            if key in rows:
                continue
            rows[key] = MarketRow(
                symbol=entry["symbol"],
                timeframe=entry["timeframe"],
                strategy=entry["strategy"],
                declared=False,
                enabled=key not in disabled,
            )

        out = []
        for row in rows.values():
            out.append(self._with_history(row))
        return sorted(out, key=lambda r: (r.symbol, r.timeframe, r.strategy))

    def _with_history(self, row: MarketRow) -> MarketRow:
        if self.store is None:
            return row
        try:
            tf = TF(row.timeframe)
            bars = self.store.count(row.symbol, tf)
            first, last = self.store.range(row.symbol, tf)
        except Exception:  # noqa: BLE001 - a missing store must not break the listing
            return row
        return MarketRow(
            **{
                **row.__dict__,
                "bars": bars,
                "first_utc": first.isoformat() if first else None,
                "last_utc": last.isoformat() if last else None,
            }
        )

    def active(self, declared: dict[str, list[Market]]) -> tuple[Market, ...]:
        """What the loop should trade: everything not switched off, deduplicated."""
        markets = {row.market for row in self.rows(declared) if row.enabled}
        return tuple(sorted(markets, key=lambda m: (m.symbol, m.timeframe.value)))

    def strategy_markets(self, declared: dict[str, list[Market]]) -> dict[str, set[Market]]:
        """Which markets each strategy may trade, for the runtime to route by."""
        out: dict[str, set[Market]] = {}
        for row in self.rows(declared):
            if row.enabled:
                out.setdefault(row.strategy, set()).add(row.market)
        return out

    # --- changing it --------------------------------------------------------------

    def disable(self, strategy: str, symbol: str, timeframe: str, reason: str = "") -> None:
        """Stop deciding on this market. Open positions keep their stops and reconcile still sees them."""
        key = self._id(strategy, symbol, timeframe)
        book = self._raw()
        if key not in book["disabled"]:
            book["disabled"].append(key)
            self._save(book)
        self.journal.event(
            "INFO", SOURCE, f"{strategy} will not trade {symbol} {timeframe}", {"reason": reason or "turned off"}
        )

    def enable(self, strategy: str, symbol: str, timeframe: str) -> None:
        book = self._raw()
        key = self._id(strategy, symbol, timeframe)
        if key in book["disabled"]:
            book["disabled"].remove(key)
            self._save(book)
        self.journal.event("INFO", SOURCE, f"{strategy} may trade {symbol} {timeframe} again", None)

    def add(
        self, strategy: str, symbol: str, timeframe: str, *, known_strategies: set[str], lifecycle=None
    ) -> MarketRow:
        """Let a strategy trade a market its author never declared. Two conditions, both hard.

        History must exist, because a market that cannot be backtested cannot climb the ladder;
        and the strategy drops to `research`, because passing a gate on EURUSD H4 proves nothing
        about this. By D26 that keeps it off real money until it earns its way back.
        """
        symbol = symbol.strip().upper()
        if strategy not in known_strategies:
            raise MarketRefused(f"no strategy called {strategy!r} is registered")
        try:
            tf = TF(timeframe.strip().upper())
        except ValueError as e:
            raise MarketRefused(f"{timeframe!r} is not a timeframe this system knows") from e

        bars = self.store.count(symbol, tf) if self.store is not None else 0
        if bars < 1:
            raise MarketRefused(
                f"no stored bars for {symbol} {tf.value}. Sync history first "
                f"(`data sync --symbol {symbol} --tf {tf.value}`): a market that cannot be "
                "backtested cannot be promoted, so it may not be added blind"
            )

        book = self._raw()
        entry = {"strategy": strategy, "symbol": symbol, "timeframe": tf.value}
        if entry not in book["added"]:
            book["added"].append(entry)
        key = self._id(strategy, symbol, tf.value)
        if key in book["disabled"]:
            book["disabled"].remove(key)
        self._save(book)

        demoted = None
        if lifecycle is not None:
            current = lifecycle.state(strategy)
            if current.value != "research":
                lifecycle.demote_to_research(strategy, f"added a new market: {symbol} {tf.value}")
                demoted = current.value

        self.journal.event(
            "WARN",
            SOURCE,
            f"{strategy} may now trade {symbol} {tf.value}, a market it was never written for",
            {"bars": bars, "demoted_from": demoted, "stage": "research"},
        )
        return MarketRow(symbol=symbol, timeframe=tf.value, strategy=strategy, declared=False, enabled=True, bars=bars)

    def remove(self, strategy: str, symbol: str, timeframe: str) -> None:
        """Forget an added market entirely. A declared one can only be turned off, not removed."""
        book = self._raw()
        entry = {"strategy": strategy, "symbol": symbol.upper(), "timeframe": timeframe.upper()}
        book["added"] = [e for e in book["added"] if e != entry]
        key = self._id(strategy, symbol.upper(), timeframe.upper())
        book["disabled"] = [k for k in book["disabled"] if k != key]
        self._save(book)
        self.journal.event("INFO", SOURCE, f"removed {symbol} {timeframe} from {strategy}", None)


def declared_markets(runtime) -> dict[str, list[Market]]:
    """What each registered strategy says it trades."""
    out: dict[str, list[Market]] = {}
    for slot in runtime.slots:
        tf = getattr(slot.strategy, "timeframe", None)
        if tf is None:
            continue
        out[slot.key] = [Market(symbol, tf) for symbol in getattr(slot.strategy, "symbols", [])]
    return out
