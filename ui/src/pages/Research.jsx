import React, { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { Card, Empty, Pill, Stat } from "../components";
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
  const [runs, setRuns] = useState([]);
  const [selected, setSelected] = useState(null);
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
  }, [loadRuns]);

  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed") return undefined;
    const timer = setInterval(async () => {
      try {
        const next = await api.job(job.id);
        setJob(next);
        if (next.status === "done") loadRuns();
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
    try {
      setSelected(await api.run(id));
    } catch (e) {
      setError(e.message);
    }
  };

  const showDrift = async (id) => {
    try {
      setDrift(await api.drift(id, 30));
    } catch (e) {
      setError(e.message);
    }
  };

  const field = (name, type = "number", step) => (
    <label className="flex flex-col gap-px">
      <span className="stat-k">{name.replace(/_/g, " ")}</span>
      <input
        className="input font-mono"
        type={type}
        step={step}
        value={form[name]}
        onChange={(e) => setForm({ ...form, [name]: type === "number" ? Number(e.target.value) : e.target.value })}
      />
    </label>
  );

  return (
    <div className="flex flex-col gap-4">
      <Card title="Run a backtest" right={job ? `job #${job.id} ${job.status}` : null}>
        <div className="grid grid-cols-6 gap-3">
          {field("strategy", "text")}
          {field("symbol", "text")}
          {field("timeframe", "text")}
          {field("balance")}
          {field("spread_points")}
          {field("slippage_points", "number", "0.1")}
          {field("commission", "number", "0.1")}
          {field("warmup")}
          {field("monte_carlo")}
          {field("label", "text")}
          <label className="flex items-end gap-2 pb-1">
            <input
              type="checkbox"
              checked={form.use_bar_spread}
              onChange={(e) => setForm({ ...form, use_bar_spread: e.target.checked })}
            />
            <span className="text-xs">spread from bars</span>
          </label>
          <div className="flex items-end">
            <button className="btn w-full" onClick={start} disabled={job && job.status === "running"}>
              {job && job.status === "running" ? "running…" : "Run backtest"}
            </button>
          </div>
        </div>
        {job?.status === "failed" && <p className="text-xs text-neg mt-2">{job.error}</p>}
        {job?.status === "done" && <p className="text-xs text-muted mt-2 font-mono">{job.summary}</p>}
        {error && <p className="text-xs text-neg mt-2">{error}</p>}
        <p className="text-xs text-muted mt-2">
          The replay goes through the same Risk Engine and the same kill switch as live trading, against costs
          measured on this broker. A result that only works with zero spread is not a result.
        </p>
      </Card>

      <div className="flex flex-col gap-4">
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

        <RunDetail run={selected} drift={drift} onDrift={showDrift} />
      </div>
    </div>
  );
}

function RunDetail({ run, drift, onDrift }) {
  if (!run) {
    return (
      <Card title="Run detail">
        <Empty>pick a run above</Empty>
      </Card>
    );
  }
  const s = run.stats || {};
  const mc = run.monte_carlo;
  const wf = run.walk_forward;
  return (
    <Card
      title={`Run #${run.id}`}
      right={
        <button className="btn" onClick={() => onDrift(run.id)}>
          Compare with live
        </button>
      }
    >
      <div className="text-xs text-muted mb-2">
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

      <div className="text-xs text-muted mt-3">
        Exits {JSON.stringify(s.exits || {})} · rejections {JSON.stringify(run.rejections || {})}
      </div>
      {mc && <div className="text-xs mt-2 font-mono">{mc.summary}</div>}
      {wf && <div className="text-xs mt-1 font-mono">{wf.summary}</div>}
      {run.killed && <div className="text-xs text-neg mt-2">stopped early: {run.killed}</div>}

      {drift && drift.run_id === run.id && (
        <div className="mt-3 border-t-2 border-ink pt-2">
          <div className="flex items-center gap-2 mb-1">
            <h4 className="card-title mb-0">Live vs backtest</h4>
            <Pill className={drift.meaningful ? "border-line text-muted" : "border-warn text-warn bg-amber-50"}>
              {drift.live_trades} live trades
            </Pill>
          </div>
          {!drift.meaningful && (
            <p className="text-xs text-warn">
              Too few live trades to conclude anything. The numbers are here to watch accumulate, not to act on.
            </p>
          )}
          <table className="w-full text-xs mt-1">
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
            <p key={n} className="text-xs text-muted mt-1">
              {n}
            </p>
          ))}
        </div>
      )}
    </Card>
  );
}
