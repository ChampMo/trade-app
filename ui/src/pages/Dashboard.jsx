import React, { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Card, Empty, Pill, SeverityPill, Stat, StatePill } from "../components";
import { bothClocks, money, signed, utc } from "../lib/format";

export default function Dashboard({ status, live }) {
  const [positions, setPositions] = useState([]);
  const [ticks, setTicks] = useState([]);

  useEffect(() => {
    const load = async () => {
      try {
        setPositions(await api.positions());
        setTicks(await api.ticks());
      } catch {
        /* the header already says the core is unreachable */
      }
    };
    load();
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, []);

  const recent = live.slice(-8).reverse();

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-4 gap-4">
        <Card title="System">
          <div className="flex items-center gap-2">
            <StatePill state={status?.state ?? "…"} />
            <span className="text-xs">{status?.service?.ticks ?? 0} ticks</span>
          </div>
          <div className="text-xs text-muted">
            last tick {bothClocks(status?.service?.last_tick_utc)}
            <br />
            {status?.service?.last_error ? (
              <span className="text-neg">loop stopped: {status.service.last_error}</span>
            ) : (
              "loop healthy"
            )}
          </div>
        </Card>
        <Card title="Reconcile">
          <div className="flex items-center gap-2">
            <Pill className={status?.frozen ? "border-neg text-neg bg-red-50" : "border-pos text-pos bg-emerald-50"}>
              {status?.frozen ? "FROZEN" : "OK"}
            </Pill>
          </div>
          <div className="text-xs text-muted">
            {status?.freeze_reason || "broker and journal agree"}
          </div>
        </Card>
        <Card title="Marks">
          <div className="flex gap-4">
            <Stat label="Day start" value={money(status?.day_start_equity)} />
            <Stat label="Peak" value={money(status?.peak_equity)} />
          </div>
          <div className="text-xs text-muted">kept in the journal, so a restart cannot erase them</div>
        </Card>
        <Card title="Execution & terminal">
          <div className="flex gap-4">
            <Stat label="Rejects in a row" value={status?.consecutive_rejects ?? 0} />
            <Stat label="Reconnects" value={status?.reconnects ?? 0} />
          </div>
          <div className="text-xs text-muted">
            three rejects in a row trips the kill switch; a dropped terminal is reconnected on its own
            <br />
            broker contact {bothClocks(status?.last_broker_contact_utc)}
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-[2fr_1fr] gap-4">
        <Card title="Open positions" right={`${positions.length} open`}>
          {positions.length === 0 ? (
            <Empty>nothing open</Empty>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr>
                  {["Ticket", "Symbol", "Side", "Lots", "Entry", "SL", "TP", "PnL", "Magic"].map((h) => (
                    <th key={h} className="th">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.ticket}>
                    <td className="td font-mono">{p.ticket}</td>
                    <td className="td">{p.symbol}</td>
                    <td className="td">{p.side}</td>
                    <td className="td font-mono">{p.volume}</td>
                    <td className="td font-mono">{p.price_open}</td>
                    <td className={`td font-mono ${p.sl > 0 ? "" : "text-neg font-bold"}`}>
                      {p.sl > 0 ? p.sl : "NO STOP"}
                    </td>
                    <td className="td font-mono">{p.tp || "—"}</td>
                    <td className={`td font-mono ${p.profit >= 0 ? "text-pos" : "text-neg"}`}>{signed(p.profit)}</td>
                    <td className="td font-mono text-muted">{p.magic}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Recent loop activity">
          {ticks.length === 0 ? (
            <Empty>no ticks yet</Empty>
          ) : (
            <div className="flex flex-col gap-1 text-xs font-mono max-h-64 overflow-auto">
              {ticks.slice(-14).reverse().map((t, i) => (
                <div key={i} className="flex gap-2 border-b border-line pb-1">
                  <span className="text-muted">{utc(t.at_utc)}</span>
                  <span>{t.state}</span>
                  <span className="ml-auto">{t.new_bar ? "bar" : ""}</span>
                  <span>{t.sent ? `sent ${t.sent}` : ""}</span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card title="Live events" right="streamed over the websocket">
        {recent.length === 0 ? (
          <Empty>waiting for the next event</Empty>
        ) : (
          <div className="flex flex-col gap-1">
            {recent.map((e) => (
              <div key={e.id} className="flex gap-2 items-center text-xs border-b border-line pb-1">
                <span className="font-mono text-muted">{utc(e.ts_utc)}</span>
                <SeverityPill severity={e.severity} />
                <span className="font-mono text-muted w-20">{e.source}</span>
                <span>{e.message}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
