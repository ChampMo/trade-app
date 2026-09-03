# CLAUDE.md — trade-app

Windows desktop app (Electron, Phase 2) that controls a Python trading core talking to MetaTrader 5 (broker: XM).
Run-time AI is DeepSeek, bounded to three numbers. Claude Code is **build-time only** and must never appear in the runtime.

Read this file first in every session. If a task conflicts with a rule below, change this file (with the owner) before changing code.

## Iron rules (never violate)

1. **AI never sends orders.** DeepSeek may only set `bias` (-1..1), `size_mult` (0..1.5) and `block` (bool), each with an expiry. Stale AI context = neutral values. Calendar-based news blocking uses no LLM and always works.
2. **Risk Engine is the only path to the market.** Strategies emit `Intent`s. Only the Risk Engine converts an Intent into an `OrderRequest`. Nothing else calls `Broker.market_order`. (Phase 0 smoke script is the single documented exception and runs on DEMO only.)
3. **Every order carries a Stop Loss at the broker.** `OrderRequest.stop_price` is mandatory. After a fill the bridge verifies the SL is set on the position; if it cannot be set within seconds, the position is closed immediately.
4. **One strategy code path for backtest and live.** `Strategy.on_bar(ctx)` is the only entry point; backtest and live differ only in how `Context` is fed.
5. **Every decision is journaled to SQLite** (`src/tradeapp/journal`): bars/indicators seen, raw AI prompt+response, Intent, Risk verdict with reason, order request/result, fills, slippage. No log = cannot debug = cannot improve.
6. **Kill switch is deterministic code, never AI.** Numeric triggers only (daily loss, drawdown, MT5 disconnect seconds, consecutive rejects, reconcile mismatch, manual). On trigger: close all, drop intents, notify, wait for a human. After unlock the system is PAUSED, not RUNNING.
7. **Gates before real money are numbers, not feelings.** See `docs/DECISIONS.md` (D3) and `BACKLOG.md` Phase 4/5. Do not edit gates while excited about a backtest.
8. **Secrets and live accounts.** Secrets live only in `.env` on the core host (never committed, never shown in UI). The broker bridge refuses a REAL account unless `ALLOW_LIVE=true`; that variable is never set on dev machines or CI. Two separate MT5 terminals: demo for development, live (later) for money.

## Architecture (four layers, one direction)

```
UI (Electron/React, Phase 2)  --HTTP/WS on localhost-->  Core (FastAPI, Phase 1+)
Core: strategies -> Intent -> Risk Engine -> Execution -> Broker (MT5 bridge)   [journal everything]
AI layer (DeepSeek, Phase 3) feeds Context only: regime / bias / size_mult / block
```

- `src/tradeapp/contracts.py` — dataclasses and Protocols (`Intent`, `Strategy`, `Broker`, `OrderRequest`, ...). Change with care; everything depends on them.
- `src/tradeapp/broker/` — `mt5_bridge.py` (real MT5, plus the watchdog: `ensure_connected()` is called every tick and reattaches a dropped terminal; connect retries **only** -10003/-10005, never an auth failure, D22), `fake.py` (deterministic fake for tests), `guard.py` (live-account guard).
- `src/tradeapp/risk/` — **the only door to the market** (rule 02). `limits.py` holds D3's numbers, the engine state and the AI context; `sizing.py` is the pure money arithmetic (including the margin fallback); `correlation.py` is a coarse table that may only ever refuse (D23); `engine.py` turns an `Intent` into an `OrderRequest` or a journaled rejection; `killswitch.py` is the emergency brake (rule 06, D12a) and is the one module allowed to close positions directly, never to open them. `tests/test_rule_02_single_door.py` fails the build if anything else builds an order or calls the broker's trading methods.
- `src/tradeapp/backtest/` — the live system fed from a file. `broker.py` is a `Broker` made of history so the backtest drives the real Core/Risk/Executor; `costs.py` holds the measured XM numbers (D18); `stats.py` and `robustness.py` produce what the gates read. Never add a second decision path here — that is the whole point of the package.
- `src/tradeapp/ai/` — the run-time AI layer. `schemas.py` is the safety story: four bounded numbers, validated strictly, and a bad shape keeps the previous view rather than retrying. `client.py` is one httpx POST with a journal-backed daily budget. `analyst.py` is the only agent whose output can change a trade, and its prompt carries **no account data**. `scout.py` writes a briefing the analyst reads and **never touches the calendar** (D24); `reviewer.py` comments on a day and may not propose a change (D11). Absent or out of budget is a normal state.
- `src/tradeapp/reports.py` — the daily post-mortem, the A/B table and the live-vs-backtest drift report. All three explain and never propose; `regime` is deliberately not classified automatically, and drift refuses to conclude anything under 20 live trades (D11).
- `src/tradeapp/notify.py` — Telegram. Outbound never raises; inbound trusts exactly one chat id. `/kill` works from a phone, `/unlock` deliberately does not.
- `src/tradeapp/calendar.py` — economic calendar and news windows. **No LLM**: release times are known in advance, so this is a rule, not a prediction. Feeds in by file or by a URL the owner chose; there is deliberately no default.
- `src/tradeapp/lifecycle.py` — D3's gates as code that refuses. No force parameter; a parameter change demotes to research, and so does attaching a new market.
- `src/tradeapp/markets.py` — the owner's overrides on top of what strategies declare (D29). Turning a market off is free; **adding one needs stored history and demotes the strategy to research**, which by D26 keeps it off real money. Missing history is one button: the page cannot fetch bars itself (MetaTrader5 allows one connection per process and it belongs to the loop), so it queues the pull and the loop does it on its next tick, one per tick. Read by the loop on the reconcile timer, so a change needs no restart.
- `src/tradeapp/data.py` — SQLite bar store. Upsert by time, weekend-aware gap report. The loop refreshes it on its own slow timer (`serve --sync-history HOURS`, 6 by default, never in `--fake`) so research and the drift report do not quietly fall behind the market.
- `src/tradeapp/api.py` + `service.py` — the local API (D7) and the thread that runs the loop behind it. Localhost only, no auth, and `serve` refuses a non-loopback host because `/control/kill` is one POST away. Journal timestamps go out with an explicit `+00:00`: without it a browser reads them as local time and the whole UI shifts by the machine's zone (D13). `research.py` runs backtests for the UI on one worker thread — one at a time, never raising into the loop, and with no route to the broker.
- `src/tradeapp/broker/paper.py` — live prices, imaginary fills. Nothing leaves the machine.
- `src/tradeapp/core.py` — **the loop**, and the one place everything is wired together. It works in **markets** (one symbol on one timeframe): the account-wide steps run once a tick, then every market gets a context, its own bar memory, its own exit management and its own decisions. The set comes from what the registered strategies declare, so `ema_cross` (H4) and `meanrev_m15` (M15) run side by side; `--symbol` and `--tf` narrow that set and can never widen it (D28). Order is safety first: read the account, reconcile on a timer, let the kill switch fire, and only then look for a new closed bar. A tick that finds trouble never reaches the trading step. Peak and day-start equity live in the journal's `state` table so a restart cannot erase the drawdown history (D21).
- `src/tradeapp/strategies/` — plugins. One file per strategy, registered with `@register`; a new strategy touches nothing else. `src/tradeapp/runtime.py` runs them and disables any that raises or returns nonsense, without stopping the others.
- `src/tradeapp/context.py` + `indicators.py` — the only view a strategy has of the world (rule 04). Indicators match MT5's definitions (SMA-seeded EMA, Wilder ATR/RSI) and return `None` during warm-up.
- `src/tradeapp/reconcile.py` — the broker is the truth. Orphans (broker has it, ledger does not) freeze new entries; ghosts (ledger has it, broker does not) are a stop doing its job and are recorded quietly (D17). Feeds `reconcile_mismatch` and `positions_without_stop` to the kill switch.
- `src/tradeapp/exits.py` — break-even, ATR trail and step trail as pure functions a strategy composes in its optional `manage()` hook; `risk/stops.py` decides whether a proposed stop may replace the current one and refuses **anything that widens risk**, with no override.
- `src/tradeapp/execution.py` — the only code that opens a position, and the only code that moves a stop. Retries only the retcodes that mean the order never reached the book (never a TIMEOUT: it is ambiguous and a retry can double-open), records signed slippage, enforces rule 03 after the fill, and produces the counters the kill switch reads.
- `src/tradeapp/journal/` — SQLAlchemy models + `Journal` store (SQLite, all timestamps naive UTC). Tables: `events`, `orders`, `decisions`, `ai_calls`, `state` (D21) and `backtests` (every run, so a live period can be compared against one). Schema upgrades are additive and apply themselves; older code refuses a newer journal.
- `src/tradeapp/smoke.py` — Phase 0 proof: open and close one DEMO order with every step journaled.
- `ui/` — Vite + React + Tailwind, wrapped by Electron. It talks to the core **only** over the local API: no MT5 client, no credentials, no way to place an order. Closing the window never stops trading. Seven pages: Dashboard (account-wide health, then one market at a time: its price, its bars, its decisions), Strategies, Markets (turn a market off, or attach one with the gate attached), Research (run a backtest from dropdowns built out of the strategies registered and the bars actually stored, read a stored run's equity curve by trade, every round trip in a table, and the bars around any one of them), Risk (**read-only**, because a limit is a decision), Journal and Events. `npm test` runs vitest over the two things the UI can get wrong on its own: the arithmetic behind the risk gauges, and what the client concludes when the core does not answer. Both have been wrong once — an absent core rendered as a 100% loss.
- `docs/` — `DECISIONS.md` (locked decisions), `RESEARCH.md` (every backtest run and what it said, including the failures), `plan-v1.md`, `plan-review.md`, `design/`.
- `BACKLOG.md` — the ordered work queue. Take the top unblocked item.

The `MetaTrader5` package is blocking and Windows-only: import it lazily (only inside `mt5_bridge.py`), run it in a thread executor once the core is asyncio (Phase 1), and never import it in tests.

## Commands

```bash
# one-time
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -e ".[dev,mt5]"                          # mt5 extra only installs on Windows
copy .env.example .env                               # then fill MT5_* for the XM DEMO account

# every change
ruff check src tests
pytest -q

# the UI (needs a core running: python -m tradeapp serve --fake)
cd ui && npm install       # once
npm run dev                # browser at the port it prints
npm start                  # Vite + Electron together
npm test                   # vitest: the header's arithmetic and what the client concludes

# Phase 0 checks
python -m tradeapp check          # connect to MT5, print account/terminal/symbol info, no orders
python -m tradeapp risk           # what the Risk Engine would do with an intent right now (sends nothing)
python -m tradeapp signals        # bars -> strategies -> Risk Engine verdict on live data (sends nothing)
python -m tradeapp reconcile      # compare broker positions against the journal
python -m tradeapp drill          # fire every kill-switch trigger against a simulated broker
python -m tradeapp run --fake     # the trading loop against a simulated broker
python -m tradeapp run            # the trading loop for real, on the profile's account
python -m tradeapp serve --paper  # loop + API, live prices, nothing sent
python -m tradeapp serve          # loop + API, real orders
python -m tradeapp smoke --fake   # full smoke flow against FakeBroker (no MT5 needed)
python -m tradeapp smoke          # real smoke on the DEMO account: open 0.01 lot, verify SL, close
python -m tradeapp data sync --symbol EURUSD,GBPUSD --tf H4,M15   # the loop also does this on a timer
python -m tradeapp data info      # what is stored, and where the gaps are
python -m tradeapp backtest       # replay stored history through the live decision path
python -m tradeapp backtest --walk-forward
python -m tradeapp backtest --param trail_atr_mult=2.5   # measure an exit variant
python -m tradeapp calendar import --file week.json   # then: calendar show
python -m tradeapp run --fake --ai                    # the loop with the analyst enabled
python -m tradeapp report postmortem --day 2026-09-02 # what happened, classified
python -m tradeapp report ab                          # does the AI variant actually win
python -m tradeapp report runs                        # every stored backtest
python -m tradeapp report drift --strategy ema_cross  # live against the run it was promoted on
python -m tradeapp notify test                        # prove alerts work before you need them
python -m tradeapp serve --telegram --ai              # everything on
python -m tradeapp journal --tail 30
```

## Working agreements for any Claude session (interactive or scheduled)

- Work on a branch. Never push to `main`. Open a PR; the owner merges and restarts anything.
- Pick the top item in `BACKLOG.md` whose dependencies are done; do exactly its scope; stop when its "Done when" holds. Vague item = write a clarification into the item instead of guessing.
- Before opening a PR: `ruff check` clean, `pytest` green, `npm test` in `ui/` green when the UI changed, and `tests/test_no_claude_dependency.py` + `tests/test_no_secrets.py` still present and passing.
- Never weaken a test to make it pass. Never change risk limits, gates or kill triggers without a matching edit to `docs/DECISIONS.md` and an explicit note in the PR.
- Never add a runtime dependency on Claude, Anthropic SDKs or the `claude` CLI. Never add `anthropic` to `pyproject.toml`.
- Never run `python -m tradeapp smoke` against anything but a DEMO account. Never set `ALLOW_LIVE`.
- A losing streak is not evidence. Post-mortems classify losses as variance / execution / regime / bug; only execution and bug lead to code changes, and parameter changes need a full-history backtest plus walk-forward, on a fixed cadence.
- Code, identifiers and commit messages in English. Docs for the owner may be Thai.

## Definition of done for a PR

Scope matches one backlog item · tests added or updated · CI green · `BACKLOG.md` item ticked · any new decision recorded in `docs/DECISIONS.md` · no secrets, no `.env`, no data files.
