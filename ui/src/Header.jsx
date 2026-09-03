import React from "react";
import { Gauge, Pill, Stat, StatePill, riskNumbers } from "./components";
import { bothClocks, money, signed } from "./lib/format";

// D3's numbers. The core enforces them; the header only shows how close they are.
const LIMITS = { dailyLossPct: 3.0, drawdownPct: 30.0 };

export default function Header({ status, connection, reachable, onKill }) {
  const risk = riskNumbers(status);

  return (
    <header className="shrink-0 bg-surface border-b-2 border-ink px-4 py-2 flex items-center gap-4">
      <div className="flex items-center gap-2 shrink-0">
        <span className="font-bold text-base tracking-tight">trade-app</span>
        {/* Colour follows the mode, so there is never a doubt which account is being touched (D8). */}
        <Pill className="border-demo text-demo bg-blue-50">DEMO</Pill>
        <StatePill state={status?.state ?? "…"} />
      </div>

      <div className="w-px h-8 bg-line shrink-0" />

      <div className="flex items-center gap-5 shrink-0">
        <Stat label="Equity" value={money(risk.equity)} />
        <Stat
          label="Today"
          value={signed(risk.todayPnl)}
          className={!risk.known ? "" : risk.todayPnl >= 0 ? "text-pos" : "text-neg"}
        />
      </div>

      <div className="flex items-center gap-4 flex-1 min-w-0 max-w-lg">
        <div className="flex-1 min-w-[8rem]">
          <Gauge label="Daily loss" value={risk.dailyLossPct} limit={LIMITS.dailyLossPct} />
        </div>
        <div className="flex-1 min-w-[8rem]">
          <Gauge label="Drawdown" value={risk.drawdownPct} limit={LIMITS.drawdownPct} />
        </div>
      </div>

      <div className="flex items-center gap-3 shrink-0 ml-auto">
        <Stat label="Last tick" value={bothClocks(status?.service?.last_tick_utc)} />
        <div className="flex gap-1">
          <Pill className={reachable ? "border-pos text-pos bg-emerald-50" : "border-neg text-neg bg-red-50"}>
            {reachable ? "core" : "no core"}
          </Pill>
          <Pill
            className={
              connection === "connected" ? "border-pos text-pos bg-emerald-50" : "border-warn text-warn bg-amber-50"
            }
          >
            {connection === "connected" ? "live" : connection}
          </Pill>
        </div>
        <button
          className="btn border-2 border-neg text-neg tracking-widest px-4 py-1.5 text-sm"
          onClick={onKill}
          disabled={!reachable}
        >
          KILL
        </button>
      </div>
    </header>
  );
}
