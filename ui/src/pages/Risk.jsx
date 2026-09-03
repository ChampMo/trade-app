import React, { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Card, Empty, Gauge, Stat } from "../components";
import { money } from "../lib/format";

// Read-only, and the page says why in the first line the eye lands on. A limit is a decision
// written down in DECISIONS.md; a screen that let you drag one would turn a deliberate act into a
// slider you nudge after a bad afternoon. There is no write endpoint behind this page at all.
export default function Risk({ status }) {
  const [body, setBody] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const load = async () => {
      try {
        setBody(await api.riskLimits());
      } catch (e) {
        setError(e.message);
      }
    };
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  const limits = Object.fromEntries((body?.limits ?? []).map((row) => [row.name, row.value]));
  const equity = status?.equity ?? 0;
  const dayStart = status?.day_start_equity ?? 0;
  const peak = status?.peak_equity ?? 0;
  const dayLoss = dayStart > 0 ? Math.max(0, ((dayStart - equity) / dayStart) * 100) : 0;
  const drawdown = peak > 0 ? Math.max(0, ((peak - equity) / peak) * 100) : 0;

  return (
    <div className="flex flex-col gap-4">
      <Card title="Today, against the limits">
        {body ? (
          <div className="flex flex-col gap-3">
            <Gauge label="Daily loss" value={dayLoss} limit={limits.daily_loss_limit_pct ?? 3} />
            <Gauge label="Drawdown from peak" value={drawdown} limit={limits.max_drawdown_pct ?? 30} />
            <div className="flex gap-6 pt-1">
              <Stat label="Equity" value={money(equity)} />
              <Stat label="Day start" value={money(dayStart)} />
              <Stat label="Peak" value={money(peak)} />
              <Stat label="Open" value={`${status?.open_positions ?? 0} / ${limits.max_positions ?? "—"}`} />
            </div>
            <p className="text-xs text-muted">
              Both gauges are measured the way the kill switch measures them: the day starts at the broker&apos;s
              midnight, not yours, and the peak survives restarts.
            </p>
          </div>
        ) : (
          <Empty>{error || "loading"}</Empty>
        )}
      </Card>

      <Card title="Limits" right={body ? "read-only" : null}>
        <p className="text-xs text-muted mb-2">
          {body?.why_not || "limits are decisions, not settings"}
        </p>
        {body?.limits?.length ? (
          <table className="w-full text-xs">
            <thead>
              <tr>
                {["Limit", "Value", "Why it exists"].map((h) => (
                  <th key={h} className="th">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.limits.map((row) => (
                <tr key={row.name}>
                  <td className="td font-mono">{row.name}</td>
                  <td className="td font-mono">{String(row.value)}</td>
                  <td className="td text-muted">{row.why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>{error || "loading"}</Empty>
        )}
      </Card>
    </div>
  );
}
