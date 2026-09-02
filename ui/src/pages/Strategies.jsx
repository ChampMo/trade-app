import React, { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Card, Empty, Pill } from "../components";

const STAGES = ["research", "backtested", "forward", "live_small", "live"];

const GATE_LABELS = {
  research: "backtest with costs, ≥30 trades, profit factor > 1",
  backtested: "walk-forward efficiency ≥ 0.5 · Monte Carlo p95 drawdown ≤ 15%",
  forward: "90 untouched days · ≥200 trades · drills 3/3 · 3 news events",
  live_small: "one month of live results matching demo",
  live: "—",
  retired: "—",
};

export default function Strategies() {
  const [rows, setRows] = useState([]);

  useEffect(() => {
    const load = async () => {
      try {
        setRows(await api.strategies());
      } catch {
        /* header reports it */
      }
    };
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="flex flex-col gap-4">
      {rows.length === 0 && <Empty>no strategies registered</Empty>}
      {rows.map((s) => (
        <Card key={s.key} title={s.key} right={`${s.symbols.join(", ")} · ${s.timeframe}`}>
          <div className="flex items-center gap-2 flex-wrap">
            <Pill className={s.enabled ? "border-pos text-pos bg-emerald-50" : "border-neg text-neg bg-red-50"}>
              {s.enabled ? "enabled" : "disabled"}
            </Pill>
            <Pill className="border-demo text-demo bg-blue-50">{s.lifecycle}</Pill>
            <span className="text-xs text-muted">
              {s.calls} calls · {s.signals} signals
            </span>
          </div>

          {/* The ladder is the whole discipline: promotion is gated by numbers, not by feeling. */}
          <div className="flex">
            {STAGES.map((stage, i) => {
              const reached = STAGES.indexOf(s.lifecycle) >= i;
              const current = s.lifecycle === stage;
              return (
                <div
                  key={stage}
                  className={`flex-1 border-[1.5px] border-ink -ml-[1.5px] first:ml-0 px-2 py-1 text-center text-[11px] font-bold ${
                    current ? "bg-ink text-white" : reached ? "bg-slate-200" : "bg-surface text-muted"
                  }`}
                >
                  {stage}
                </div>
              );
            })}
          </div>
          <div className="text-xs text-muted">
            to advance: {GATE_LABELS[s.lifecycle] ?? "—"}
          </div>

          {s.error && (
            <div className="text-xs text-neg border-[1.5px] border-neg rounded p-2 bg-red-50">
              disabled by the runtime: {s.error}
            </div>
          )}
        </Card>
      ))}
      <div className="text-xs text-muted">
        Promotion happens from the command line so the evidence is produced by a real backtest, never typed in:
        <code className="font-mono ml-1">python -m tradeapp lifecycle promote --strategy &lt;id&gt;</code>
      </div>
    </div>
  );
}
