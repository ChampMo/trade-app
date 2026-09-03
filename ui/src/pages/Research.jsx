import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { Card, Empty, Pill, Stat } from "../components";
import { EquityCurve, TradeChart } from "../charts";
import { holdHours, tradePoints } from "../lib/series";
import { money, signed, utc } from "../lib/format";

const DEFAULTS = {
  strategy: "ema_cross",
  symbol: "EURUSD",
  timeframe: "H4",
  balance: 10000,
  warmup: 100,
  spread_points: 20,
  use_bar_spread: true,
  slippage_points: 0.3,
  commission: 0,
  monte_carlo: 1000,
  label: "",
};

// A backtest is minutes of CPU, so this page never waits on one: it starts a job and polls. The
// core refuses a second while one is running, which is deliberate — the machine is also running
// the thing that trades.
export default function Research() {
  const [form, setForm] = useState(DEFAULTS);
  const [options, setOptions] = useState(null);
  const [runs, setRuns] = useState([]);
  const [selected, setSelected] = useState(null);
  const [trade, setTrade] = useState(null);
  const [bars, setBars] = useState(null);
  const [drift, setDrift] = useState(null);
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);

  const loadRuns = useCallback(async () => {
    try {
      setRuns(await api.runs(25));
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    loadRuns();
    api.backtestOptions().then(setOptions).catch((e) => setError(e.message));
  }, [loadRuns]);

  // Poll a running job. The core answers immediately with an id; the work happens on its own thread.
  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed") return undefined;
    const timer = setInterval(async () => {
      try {
        const next = await api.job(job.id);
        setJob(next);
        if (next.status === "done") {
          await loadRuns();
          if (next.run_id) open(next.run_id); // land on the result rather than making them hunt for it
        }
      } catch (e) {
        setError(e.message);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [job, loadRuns]);

  const start = async () => {
    setError(null);
    try {
      setJob(await api.startBacktest({ ...form, balance: Number(form.balance) }));
    } catch (e) {
      setError(e.message);
    }
  };

  const open = async (id) => {
    setDrift(null);
    setTrade(null);
    setBars(null);
    try {
      setSelected(await api.run(id));
    } catch (e) {
      setError(e.message);
    }
  };

  // One trade's own window: enough bars around it to see what the price was doing, no more.
  const pickTrade = async (t) => {
    setTrade(t);
    setBars(null);
    if (!t || !selected) return;
    const span = new Date(t.closed_utc) - new Date(t.opened_utc);
    const margin = Math.max(span * 0.6, 36 * 3600 * 1000);
    try {
      const body = await api.bars({
        symbol: selected.symbol,
        timeframe: selected.timeframe,
        start: new Date(new Date(t.opened_utc).getTime() - margin).toISOString(),
        end: new Date(new Date(t.closed_utc).getTime() + margin).toISOString(),
        limit: 300,
      });
      setBars(body.bars);
    } catch (e) {
      setError(e.message);
      setBars([]);
    }
  };

  const symbols = useMemo(() => [...new Set((options?.data ?? []).map((d) => d.symbol))], [options]);
  const timeframes = useMemo(
    () => (options?.data ?? []).filter((d) => d.symbol === form.symbol).map((d) => d.timeframe),
    [options, form.symbol],
  );
  const dataset = (options?.data ?? []).find((d) => d.symbol === form.symbol && d.timeframe === form.timeframe);

  const set = (name, value) => setForm((f) => ({ ...f, [name]: value }));

  const field = (name, input) => (
    <label className="flex flex-col gap-px">
      <span className="stat-k">{name.replace(/_/g, " ")}</span>
      {input}
    </label>
  );

  const number = (name, step) =>
    field(
      name,
      <input
        className="input font-mono"
        type="number"
        step={step}
        value={form[name]}
        onChange={(e) => set(name, Number(e.target.value))}
      />,
    );

  const choose = (name, values) =>
    field(
      name,
      <select className="input font-mono" value={form[name]} onChange={(e) => set(name, e.target.value)}>
        {values.length === 0 && <option value={form[name]}>{form[name]}</option>}
        {values.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>,
    );

  return (
    <div className="flex flex-col gap-4">
      <Card title="Run a backtest" right={job ? `job #${job.id} ${job.status}` : null}>
        <div className="grid grid-cols-6 gap-3">
          {choose("strategy", options?.strategies ?? [])}
          {choose("symbol", symbols)}
          {choose("timeframe", timeframes)}
          {number("balance")}
          {number("spread_points")}
          {number("slippage_points", "0.1")}
          {number("commission", "0.1")}
          {number("warmup")}
          {number("monte_carlo")}
          {field(
            "label",
            <input className="input font-mono" value={form.label} onChange={(e) => set("label", e.target.value)} />,
          )}
          <label className="flex items-end gap-2 pb-1">
            <input
              type="checkbox"
              checked={form.use_bar_spread}
              onChange={(e) => set("use_bar_spread", e.target.checked)}
            />
            <span className="text-xs">spread from bars</span>
          </label>
          <div className="flex items-end">
            <button className="btn w-full" onClick={start} disabled={job?.status === "running"}>
              {job?.status === "running" ? "running…" : "Run backtest"}
            </button>
          </div>
        </div>

        {dataset && (
          <p className="text-xs text-muted">
            {dataset.bars.toLocaleString()} bars stored · {utc(dataset.from, true)} → {utc(dataset.to, true)}
          </p>
        )}
        {job?.status === "failed" && <p className="text-xs text-neg mt-1">{job.error}</p>}
        {job?.status === "done" && <p className="text-xs text-muted mt-1 font-mono">{job.summary}</p>}
        {error && <p className="text-xs text-neg mt-1">{error}</p>}
        <p className="text-xs text-muted">
          The replay goes through the same Risk Engine and the same kill switch as live trading, against costs measured
          on this broker. A result that only works with zero spread is not a result.
        </p>
      </Card>

      <Card title="Stored runs" right={`${runs.length}`}>
        {runs.length === 0 ? (
          <Empty>nothing stored yet</Empty>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr>
                {["#", "When (UTC)", "Strategy", "Trades", "Net", "Return", "PF", "maxDD", "Label"].map((h) => (
                  <th key={h} className="th">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr
                  key={r.id}
                  className={`cursor-pointer ${selected?.id === r.id ? "bg-slate-100" : ""}`}
                  onClick={() => open(r.id)}
                >
                  <td className="td font-mono">{r.id}</td>
                  <td className="td">{utc(r.ts_utc, true)}</td>
                  <td className="td">
                    {r.strategy} <span className="text-muted">{r.symbol} {r.timeframe}</span>
                  </td>
                  <td className="td font-mono">{r.stats?.trades ?? 0}</td>
                  <td className={`td font-mono ${(r.stats?.net ?? 0) >= 0 ? "text-pos" : "text-neg"}`}>
                    {signed(r.stats?.net)}
                  </td>
                  <td className="td font-mono">{(r.stats?.return_pct ?? 0).toFixed(2)}%</td>
                  <td className="td font-mono">{(r.stats?.profit_factor ?? 0).toFixed(2)}</td>
                  <td className="td font-mono">{(r.stats?.max_drawdown_pct ?? 0).toFixed(2)}%</td>
                  <td className="td text-muted">{r.label || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {selected && (
        <RunDetail
          run={selected}
          trade={trade}
          bars={bars}
          drift={drift}
          onPickTrade={pickTrade}
          onDrift={async (id) => {
            try {
              setDrift(await api.drift(id, 30));
            } catch (e) {
              setError(e.message);
            }
          }}
        />
      )}
    </div>
  );
}

function RunDetail({ run, trade, bars, drift, onPickTrade, onDrift }) {
  const s = run.stats || {};
  const trades = run.trades || [];
  const point = run.symbol?.toUpperCase().includes("JPY") ? 0.001 : 0.00001;

  return (
    <div className="flex flex-col gap-4">
      <Card
        title={`Run #${run.id}`}
        right={
          <button className="btn" onClick={() => onDrift(run.id)}>
            Compare with live
          </button>
        }
      >
        <div className="text-xs text-muted">
          {run.label || "no label"} · {run.bars} bars · {utc(run.data_from, true)} → {utc(run.data_to, true)}
        </div>
        <div className="grid grid-cols-4 gap-3">
          <Stat label="Net" value={signed(s.net)} className={(s.net ?? 0) >= 0 ? "text-pos" : "text-neg"} />
          <Stat label="Return" value={`${(s.return_pct ?? 0).toFixed(2)}%`} />
          <Stat label="Trades" value={`${s.wins ?? 0}/${s.trades ?? 0}`} />
          <Stat label="Win rate" value={`${(s.win_rate ?? 0).toFixed(1)}%`} />
          <Stat label="Profit factor" value={(s.profit_factor ?? 0).toFixed(2)} />
          <Stat label="Expectancy" value={signed(s.expectancy)} />
          <Stat label="Max drawdown" value={`${(s.max_drawdown_pct ?? 0).toFixed(2)}%`} />
          <Stat label="Losing streak" value={s.longest_losing_streak ?? 0} />
          <Stat label="Costs" value={money(s.costs)} />
          <Stat label="Cost share" value={`${s.cost_share_of_gross ?? 0}%`} />
          <Stat label="Net before costs" value={signed(s.net_before_costs)} />
          <Stat label="Avg hold" value={`${(s.avg_hold_hours ?? 0).toFixed(1)}h`} />
        </div>
        <div className="text-xs text-muted">
          Exits {JSON.stringify(s.exits || {})} · rejections {JSON.stringify(run.rejections || {})}
        </div>
        {run.monte_carlo && <div className="text-xs font-mono">{run.monte_carlo.summary}</div>}
        {run.walk_forward && <div className="text-xs font-mono">{run.walk_forward.summary}</div>}
        {run.killed && <div className="text-xs text-neg">stopped early: {run.killed}</div>}
      </Card>

      <Card title="Equity by trade" right={`${trades.length} round trips`}>
        <EquityCurve
          trades={trades}
          startBalance={run.start_balance}
          selected={trade}
          onPick={onPickTrade}
        />
      </Card>

      {/* The table carries ten columns and is the thing being read; the chart scales to whatever
          width is left over. */}
      <div className="grid grid-cols-[1.45fr_1fr] gap-4">
        <Card title="Trades" right={trade ? `#${trades.indexOf(trade) + 1} selected` : "click a row"}>
          <div className="max-h-[26rem] overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface">
                <tr>
                  {["#", "Opened (UTC)", "Side", "Lots", "In", "Out", "Points", "Net", "Hold", "Exit"].map((h) => (
                    <th key={h} className="th">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr
                    key={`${t.opened_utc}-${i}`}
                    className={`cursor-pointer ${t === trade ? "bg-slate-100" : ""}`}
                    onClick={() => onPickTrade(t)}
                  >
                    <td className="td font-mono">{i + 1}</td>
                    <td className="td">{utc(t.opened_utc, true)}</td>
                    <td className="td">{t.side}</td>
                    <td className="td font-mono">{t.volume}</td>
                    <td className="td font-mono">{t.entry}</td>
                    <td className="td font-mono">{t.exit}</td>
                    <td className={`td font-mono ${t.net >= 0 ? "text-pos" : "text-neg"}`}>
                      {tradePoints(t, point)?.toFixed(0)}
                    </td>
                    <td className={`td font-mono ${t.net >= 0 ? "text-pos" : "text-neg"}`}>{signed(t.net)}</td>
                    <td className="td font-mono">{holdHours(t)?.toFixed(0)}h</td>
                    <td className="td text-muted">{t.exit_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card title="The trade on the chart" right={trade ? `${trade.side} ${trade.volume}` : null}>
          <TradeChart bars={bars} trade={trade} point={point} />
        </Card>
      </div>

      {drift && drift.run_id === run.id && <DriftPanel drift={drift} />}
    </div>
  );
}

function DriftPanel({ drift }) {
  return (
    <Card title="Live vs backtest" right={`${drift.live_trades} live trades`}>
      {!drift.meaningful && (
        <Pill className="border-warn text-warn bg-amber-50 self-start">
          too few live trades to conclude anything
        </Pill>
      )}
      <table className="w-full text-xs">
        <thead>
          <tr>
            {["Metric", "Backtest", "Live", "Gap"].map((h) => (
              <th key={h} className="th">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {drift.metrics.map((m) => (
            <tr key={m.name}>
              <td className="td">
                {m.name}
                {m.worse && drift.meaningful ? " ⚠" : ""}
              </td>
              <td className="td font-mono">{m.backtest ?? "—"}</td>
              <td className="td font-mono">{m.live ?? "—"}</td>
              <td className="td font-mono">{m.gap ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {drift.notes.map((n) => (
        <p key={n} className="text-xs text-muted">
          {n}
        </p>
      ))}
    </Card>
  );
}
