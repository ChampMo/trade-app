# BACKLOG

Ordered queue. One item = one PR = finishable in one session. Each item has a **Done when** that a test or a command can show.
Owner-only items are marked **[owner]** — automated sessions skip them and note the blocker.

Legend: `[ ]` todo · `[~]` in progress (write the branch name) · `[x]` done (write the PR)

## Phase 0 — foundations (week 1)

- [x] P0-01 Repo skeleton: `pyproject`, `src/tradeapp`, `tests`, CI, `.env.example`, `.gitignore`, CLAUDE.md, BACKLOG.md, docs. Done when: `pytest` green on a clean checkout without MT5 installed.
- [x] P0-02 Contracts v0: `Side`, `Intent`, `OrderRequest` (stop mandatory), `OrderResult`, `Position`, `Broker` Protocol. Done when: `tests/test_contracts.py` passes.
- [x] P0-03 Journal v0: SQLite via SQLAlchemy, tables `events`, `orders`, `decisions`, `ai_calls`, `schema_version`. Done when: `tests/test_journal.py` passes and `python -m tradeapp journal --tail` prints rows.
- [x] P0-04 MT5 bridge v0 with live guard: connect/account/symbol/tick/market_order/position/modify_sltp/close, refuses REAL unless `ALLOW_LIVE=true`. Done when: `tests/test_live_guard.py` passes (guard exercised through a fake `MetaTrader5` module).
- [x] P0-05 Smoke flow: open 0.01 lot → verify SL at broker (modify, else close) → hold → close → journal every step; `FakeBroker` covers happy path, dropped SL, failed modify, reject. Done when: `python -m tradeapp smoke --fake` reports ok and `tests/test_smoke_flow.py` passes.
- [x] P0-06 **[owner]** Enable *Algo Trading* and prove the bridge reaches a live terminal. Done 2026-09-02: `check` prints `10012497359@MetaQuotes-Demo mode=demo algo_trading=True`. XM demo account #318639355 (MT5 Standard, $10k, 1000:1) was opened the same day but the terminal is not logged into it yet.
- [x] P0-07 **[owner]** First real smoke on a demo account. Done 2026-09-02 on MetaQuotes-Demo: ticket 152575367124, fill 1.15909, SL 1.15709 verified at the broker, closed 1.15910, pnl +0.01, no position left open. Journal holds the open+close pair with `sl_verified=True`.
- [ ] P0-07b **[owner]** Repeat the smoke on the **XM** demo (#318639355) — XM is the broker of record (D1) and MetaQuotes-Demo quoted a 0-point spread, which no real broker does. Log the terminal into XM (its server name appears in File > Login to Trade Account), optionally pin the account in `.env`, then run `check` and `smoke`. Done when: journal shows an XM open+close pair with `sl_verified=True` and a realistic spread recorded in the `symbol` event.
- [x] P0-08 Broker time offset. Done 2026-09-02: `broker/servertime.py` measures the offset from one tick (ceiling to the next half hour, so a stale tick still resolves; refuses to guess once the tick is hours old), the bridge measures it on connect and re-exposes `refresh_server_offset()` for reconnects and DST, `Tick` now carries `time_server` + `server_utc_offset_min` alongside a real-UTC `time_utc`, and both `check` and `smoke` journal the offset. Measured on MetaQuotes-Demo: UTC+3. 12 tests cover it.
- [ ] P0-09 First commit + push `main`, install the Claude GitHub App on `ChampMo/trade-app` (needed for cloud routines later). **[owner]** Done when: CI runs green on GitHub.

## Phase 1 — engine + risk (weeks 2–3)

- [x] P1-01 Config & profiles. Done 2026-09-02: `Profile` = demo | paper | live, each with its own journal (`data/journal-<profile>.db`) and port (8001/8001/8002, overridable via `API_PORT`); `ALLOW_LIVE` on a non-live profile is now a load-time error, and `live_enabled` is the single value handed to the broker guard. The "two cores side by side" half of the check needs the API, so it is verified under P1-09.
- [ ] P1-01b MT5 startup diagnostics beyond `initialize`: retry on `-10003` (terminal busy) and detect a terminal that is running but logged out, so the core does not spin. Done when: a test drives both paths. (The one-line hints for `-2/-4/-5/-6/-8` landed with P1-01.)
- [ ] P1-02 Strategy runtime: load strategy plugins from `src/tradeapp/strategies/`, each in its own asyncio task, exception in one → that one disabled + event, others continue. Done when: test injects an exception and only that strategy stops.
- [ ] P1-03 Context v0: recent bars (from MT5 or cache), precomputed indicators (EMA, ATR, RSI), `ctx.ai` stub returning neutral values. Done when: a trivial strategy runs `on_bar` against a recorded bar file.
- [ ] P1-04 Risk Engine v0: position sizing from `risk_pct` × `size_mult`, daily loss limit, max drawdown, max positions, max open risk, per-currency netting, trading hours, news-block hook (stub). Every rejection has a reason and is journaled as a `decisions` row. Done when: one test per rejection reason; all pass.
- [ ] P1-05 Kill switch: triggers = daily loss, drawdown, MT5 disconnect > 60 s, consecutive rejects ≥ 3, reconcile mismatch, manual. On trigger close all, drop intents, event CRIT, state KILLED; unlock (reason required) → PAUSED. Done when: fault-injection tests for every trigger pass.
- [ ] P1-06 Execution layer: `OrderRequest` → bridge with retry on requote/price-changed, slippage recorded in points, SL verification (from smoke) reused. Done when: tests cover retry exhaustion and slippage math.
- [ ] P1-07 Paper execution: same `Broker` interface, fills simulated from live ticks with configured spread/slippage, switchable by config. Done when: smoke flow passes on `PaperBroker`.
- [ ] P1-08 Reconcile: every 60 s and at startup compare MT5 positions to journal; mismatch → freeze new entries + event WARN; recovery reads positions from MT5, never from own files. Done when: test simulates an orphan position and the engine freezes.
- [ ] P1-09 Core API v0 (FastAPI): `/status`, `/positions`, `/events`, `/kill`, `/unlock`, `/pause`, `/resume`, WebSocket for events. Done when: tests hit every endpoint with the fake broker.
- [ ] P1-10 Core as a background process: start script + Windows Task Scheduler "at logon" instructions; UI is never required for trading. Done when: core runs with no console attached and the API answers.

## Phase 2 — backtest, UI, first two strategies (weeks 3–5)

- [ ] P2-01 Historical data cache: pull EURUSD M1 from MT5 into parquet, incremental sync, gap report. Done when: `python -m tradeapp data sync` fills the cache and a test reads it.
- [ ] P2-02 Event-driven backtest engine calling the same `on_bar`; cost model = spread from data (news ×3 option), commission per lot, swap table, fixed slippage. Done when: a known toy strategy reproduces hand-computed PnL.
- [ ] P2-03 Walk-forward (train/test windows, efficiency ratio) and Monte Carlo (trade-order shuffle, DD percentiles). Done when: outputs match a reference run within tolerance.
- [ ] P2-04 Strategy `trend_h4` (EMA cross + pullback, ATR stop) and `meanrev_m15`; variants A (rules) / B (+calendar) / C (+AI) as separate magic numbers. Done when: both backtest end to end.
- [ ] P2-05 Strategy lifecycle state machine: research → backtested → forward → live_small → live → retired, with numeric gates enforced in code (see DECISIONS D3, D8). Done when: promotion without gates is refused by a test.
- [ ] P2-06 UI shell (Electron + React + Tailwind): connection profiles, top bar with KILL, Dashboard, Strategies, Journal browser (decision chain), Events. Follow `docs/design/wireframes`. Done when: UI runs against the demo core and KILL works end to end.
- [ ] P2-07 UI: Research page (backtest config + summary + walk-forward + Monte Carlo) and Risk page (read-only while RUNNING). Done when: a backtest can be launched and read from the UI.

## Phase 3 — AI layer (weeks 5–7)

- [ ] P3-01 Economic calendar ingest + news-block windows (no LLM). Done when: Risk Engine rejects intents inside a HIGH-impact window in tests.
- [ ] P3-02 DeepSeek client via httpx: timeout, retry budget, daily USD cap, raw prompt/response stored in `ai_calls`, Pydantic schema validation, invalid → keep previous context. Done when: tests cover cap exceeded and schema failure.
- [ ] P3-03 Agents: Scout (15 min, dedupe → events), Analyst (hourly + on HIGH news → regime/bias/size_mult/block with expiry), Reviewer (daily). Aggregation is plain code (`block = calendar or analyst`, `size_mult = min(analyst, regime_cap)`). Done when: recorded fixtures replay through the pipeline deterministically.
- [ ] P3-04 A/B/C on demo: variants wired to calendar/AI flags; comparison report by magic number. Done when: `python -m tradeapp report ab` prints the table.
- [ ] P3-05 Remove the DeepSeek key → system keeps trading on rules. Done when: a test runs the engine with `deepseek_api_key=None`.

## Phase 4 — hardening + forward test (weeks 7–10)

- [ ] P4-01 Watchdog + auto-reconnect + crash recovery from MT5 state. Done when: kill the terminal mid-run in a drill and the core recovers or kills cleanly.
- [ ] P4-02 Telegram: heartbeat every 15 min, CRIT immediately with repeat, WARN batched, two-way `/status` and `/kill`. Done when: `/kill` from Telegram closes positions on demo.
- [ ] P4-03 Nightly post-mortem job (local scheduled task): reads journal, classifies losses, writes a report to `reports/`; proposals only with evidence, never auto-applied. Done when: a report is generated from a recorded day.
- [ ] P4-04 Weekly drift report: live vs backtest on the same period. Done when: report shows divergence metrics.
- [ ] P4-05 Forward test on demo ≥ 90 days without parameter changes (gate 1). **[owner]** Track in DECISIONS D3.

## Phase 5–6 — real money, autonomy

- [ ] P5-01 **[owner]** Live terminal on a separate install, `ALLOW_LIVE=true` only there, `live_small` at 0.25% risk, 1 strategy, 1 symbol, 1 month; compare weekly with demo.
- [ ] P6-01 Move core to a Windows VPS; UI points to its address. Done when: the owner closes the PC for a week and everything still works.
