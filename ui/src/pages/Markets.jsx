import React, { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { Card, Empty, Pill } from "../components";
import { utc } from "../lib/format";

// What the bot is allowed to trade. Two different things live on this page and the difference
// matters more than the layout: switching a market off is an operating decision and costs
// nothing, while attaching a market a strategy was never written for is a research decision that
// drops it back to `research` and off any real account until it earns its way back (D29).
export default function Markets({ status }) {
  const [body, setBody] = useState(null);
  // Two error channels on purpose. The page polls every five seconds, and a poll that clears the
  // message from the button you just pressed means you never get to read why it said no.
  const [loadError, setLoadError] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ strategy: "", symbol: "", timeframe: "H4" });

  const load = useCallback(async () => {
    try {
      setBody(await api.markets());
      setLoadError(null);
    } catch (e) {
      setLoadError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const act = async (fn, args) => {
    setBusy(true);
    setError(null);
    try {
      setBody(await fn(args));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const rows = body?.markets ?? [];
  const trading = new Set((body?.trading ?? []).map((m) => `${m.symbol}|${m.timeframe}`));
  const strategies = [...new Set(rows.map((r) => r.strategy))];

  return (
    <div className="flex flex-col gap-4">
      <Card title="Markets" right={`${body?.trading?.length ?? 0} being traded`}>
        {rows.length === 0 ? (
          <Empty>{loadError || "loading"}</Empty>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr>
                {["Symbol", "TF", "Strategy", "Source", "Stored bars", "History", "In the loop", ""].map((h) => (
                  <th key={h} className="th">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const key = `${r.strategy}|${r.symbol}|${r.timeframe}`;
                const live = trading.has(`${r.symbol}|${r.timeframe}`) && r.enabled;
                return (
                  <tr key={key}>
                    <td className="td font-mono">{r.symbol}</td>
                    <td className="td font-mono">{r.timeframe}</td>
                    <td className="td">{r.strategy}</td>
                    <td className="td">
                      {r.declared ? (
                        <span className="text-muted">declared by the strategy</span>
                      ) : (
                        <Pill className="border-warn text-warn bg-amber-50">attached by you</Pill>
                      )}
                    </td>
                    <td className="td font-mono">{r.bars ? r.bars.toLocaleString() : "none"}</td>
                    <td className="td text-muted">
                      {r.first_utc ? `${utc(r.first_utc, true).slice(0, 10)} → ${utc(r.last_utc, true).slice(0, 10)}` : "—"}
                    </td>
                    <td className="td">
                      {live ? (
                        <Pill className="border-pos text-pos bg-emerald-50">trading</Pill>
                      ) : r.enabled ? (
                        <span className="text-muted">on, waiting for the loop</span>
                      ) : (
                        <Pill className="border-line text-muted">off</Pill>
                      )}
                    </td>
                    <td className="td">
                      <div className="flex gap-1 justify-end">
                        <button
                          className="btn"
                          disabled={busy}
                          onClick={() =>
                            act(r.enabled ? api.marketDisable : api.marketEnable, {
                              strategy: r.strategy,
                              symbol: r.symbol,
                              timeframe: r.timeframe,
                              reason: "from the Markets page",
                            })
                          }
                        >
                          {r.enabled ? "Turn off" : "Turn on"}
                        </button>
                        {!r.declared && (
                          <button
                            className="btn"
                            disabled={busy}
                            onClick={() =>
                              act(api.marketRemove, {
                                strategy: r.strategy,
                                symbol: r.symbol,
                                timeframe: r.timeframe,
                              })
                            }
                          >
                            Remove
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <p className="text-xs text-muted">
          Turning a market off stops new decisions there and nothing else: open positions keep the stops they
          already have at the broker, and reconcile still watches them. The loop picks changes up within a minute,
          without a restart.
        </p>
      </Card>

      <Card title="Attach a market to a strategy">
        <div className="grid grid-cols-4 gap-3 items-end">
          <label className="flex flex-col gap-px">
            <span className="stat-k">strategy</span>
            <select
              className="input font-mono"
              value={form.strategy}
              onChange={(e) => setForm({ ...form, strategy: e.target.value })}
            >
              <option value="">pick one…</option>
              {strategies.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-px">
            <span className="stat-k">symbol</span>
            <input
              className="input font-mono"
              placeholder="GBPUSD"
              value={form.symbol}
              onChange={(e) => setForm({ ...form, symbol: e.target.value.toUpperCase() })}
            />
          </label>
          <label className="flex flex-col gap-px">
            <span className="stat-k">timeframe</span>
            <select
              className="input font-mono"
              value={form.timeframe}
              onChange={(e) => setForm({ ...form, timeframe: e.target.value })}
            >
              {["M1", "M5", "M15", "M30", "H1", "H4", "D1"].map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <button
            className="btn"
            disabled={busy || !form.strategy || !form.symbol}
            onClick={() => act(api.marketAdd, form)}
          >
            Attach
          </button>
        </div>

        {error && (
          <p className="text-xs text-neg border-l-4 border-neg pl-2 py-1">
            {error}
            <button className="ml-2 underline" onClick={() => setError(null)}>
              dismiss
            </button>
          </p>
        )}

        <div className="text-xs text-muted flex flex-col gap-1">
          <p>
            <b>This is a research decision, not a setting.</b> A strategy that passed its gates on EURUSD H4 has
            proved nothing about another pair, so attaching one drops it back to <code className="font-mono">research</code>,
            and a research strategy may not touch a real account. On demo it starts trading within a minute.
          </p>
          <p>
            It is refused outright if there are no stored bars for that market: a market that cannot be backtested
            can never climb the ladder. Sync the history first:
          </p>
          <pre className="font-mono bg-paper border-[1.5px] border-line rounded p-2 overflow-x-auto">
python -m tradeapp data sync --symbol GBPUSD --tf H4
          </pre>
          <p>
            Then backtest it before letting it run:{" "}
            <span className="font-mono">Research → strategy, symbol, timeframe → Run backtest</span>. Look at the
            cost share: on this broker&apos;s 20-point spread, a one-minute chart spent 296% of its gross profit on
            spread alone.
          </p>
        </div>
      </Card>

      <Card title="What the loop is trading right now">
        <div className="flex flex-wrap gap-2">
          {(body?.trading ?? []).length === 0 ? (
            <Empty>nothing — every market is switched off</Empty>
          ) : (
            (body?.trading ?? []).map((m) => (
              <Pill key={`${m.symbol}${m.timeframe}`} className="border-pos text-pos bg-emerald-50">
                {m.symbol} {m.timeframe}
              </Pill>
            ))
          )}
        </div>
        <p className="text-xs text-muted">
          Read from the core itself, not from this page&apos;s own state. If a market is on above but missing here,
          the loop has not picked it up yet — it re-reads on the same timer as reconcile. Engine state:{" "}
          {status?.state ?? "…"}.
        </p>
      </Card>
    </div>
  );
}
