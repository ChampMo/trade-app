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
- `src/tradeapp/broker/` — `mt5_bridge.py` (real MT5), `fake.py` (deterministic fake for tests), `guard.py` (live-account guard).
- `src/tradeapp/risk/` — **the only door to the market** (rule 02). `limits.py` holds D3's numbers, the engine state and the AI context; `sizing.py` is the pure money arithmetic; `engine.py` turns an `Intent` into an `OrderRequest` or a journaled rejection; `killswitch.py` is the emergency brake (rule 06, D12a) and is the one module allowed to close positions directly, never to open them. `tests/test_rule_02_single_door.py` fails the build if anything else builds an order or calls the broker's trading methods.
- `src/tradeapp/journal/` — SQLAlchemy models + `Journal` store (SQLite, all timestamps naive UTC).
- `src/tradeapp/smoke.py` — Phase 0 proof: open and close one DEMO order with every step journaled.
- `docs/` — `DECISIONS.md` (locked decisions), `plan-v1.md`, `plan-review.md`, `design/` (UX wireframes + generator).
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

# Phase 0 checks
python -m tradeapp check          # connect to MT5, print account/terminal/symbol info, no orders
python -m tradeapp risk           # what the Risk Engine would do with an intent right now (sends nothing)
python -m tradeapp drill          # fire every kill-switch trigger against a simulated broker
python -m tradeapp smoke --fake   # full smoke flow against FakeBroker (no MT5 needed)
python -m tradeapp smoke          # real smoke on the DEMO account: open 0.01 lot, verify SL, close
python -m tradeapp journal --tail 30
```

## Working agreements for any Claude session (interactive or scheduled)

- Work on a branch. Never push to `main`. Open a PR; the owner merges and restarts anything.
- Pick the top item in `BACKLOG.md` whose dependencies are done; do exactly its scope; stop when its "Done when" holds. Vague item = write a clarification into the item instead of guessing.
- Before opening a PR: `ruff check` clean, `pytest` green, and `tests/test_no_claude_dependency.py` + `tests/test_no_secrets.py` still present and passing.
- Never weaken a test to make it pass. Never change risk limits, gates or kill triggers without a matching edit to `docs/DECISIONS.md` and an explicit note in the PR.
- Never add a runtime dependency on Claude, Anthropic SDKs or the `claude` CLI. Never add `anthropic` to `pyproject.toml`.
- Never run `python -m tradeapp smoke` against anything but a DEMO account. Never set `ALLOW_LIVE`.
- A losing streak is not evidence. Post-mortems classify losses as variance / execution / regime / bug; only execution and bug lead to code changes, and parameter changes need a full-history backtest plus walk-forward, on a fixed cadence.
- Code, identifiers and commit messages in English. Docs for the owner may be Thai.

## Definition of done for a PR

Scope matches one backlog item · tests added or updated · CI green · `BACKLOG.md` item ticked · any new decision recorded in `docs/DECISIONS.md` · no secrets, no `.env`, no data files.
