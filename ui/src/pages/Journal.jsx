import React, { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { Card, Empty, Pill, VerdictPill } from "../components";
import { bothClocks, signed, utc } from "../lib/format";

/**
 * The journal browser: the most valuable screen in the app.
 *
 * A row is one decision, including the ones that were refused — those are the evidence that a
 * limit did its job, and a list that only showed trades would hide the entire risk engine. Click
 * one and the right pane walks the whole chain that produced it.
 */
export default function Journal() {
  const [decisions, setDecisions] = useState([]);
  const [orders, setOrders] = useState([]);
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    const load = async () => {
      try {
        const [d, o] = await Promise.all([api.decisions(200), api.orders(200)]);
        setDecisions(d.reverse());
        setOrders(o);
      } catch {
        /* header reports it */
      }
    };
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, []);

  const shown = useMemo(
    () => decisions.filter((d) => (filter === "all" ? true : filter === "approved" ? d.verdict === "APPROVED" : d.verdict !== "APPROVED")),
    [decisions, filter],
  );

  const current = selected ? decisions.find((d) => d.id === selected) : shown[0];
  const order = current?.order_id ? orders.find((o) => o.id === current.order_id) : null;
  const related = order ? orders.filter((o) => o.client_ref === order.client_ref) : [];

  return (
    <div className="grid grid-cols-[1fr_460px] gap-4 h-full min-h-0">
      <Card
        title={`Decisions · ${shown.length}`}
        right={
          <span className="flex gap-1">
            {["all", "approved", "rejected"].map((f) => (
              <button
                key={f}
                className={`btn text-[11px] px-2 py-0.5 ${filter === f ? "bg-ink text-white" : ""}`}
                onClick={() => setFilter(f)}
              >
                {f}
              </button>
            ))}
          </span>
        }
        className="min-h-0"
      >
        {shown.length === 0 ? (
          <Empty>no decisions yet — the engine records one every time a strategy speaks</Empty>
        ) : (
          <div className="overflow-auto min-h-0">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface">
                <tr>
                  {["UTC", "Strategy", "Side", "Verdict", "Lots", "Reason"].map((h) => (
                    <th key={h} className="th">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.map((d) => (
                  <tr
                    key={d.id}
                    className={`cursor-pointer hover:bg-slate-50 ${current?.id === d.id ? "bg-slate-100" : ""}`}
                    onClick={() => setSelected(d.id)}
                  >
                    <td className="td font-mono text-muted">{utc(d.ts_utc)}</td>
                    <td className="td">{d.strategy_id}</td>
                    <td className="td">{d.side ?? "—"}</td>
                    <td className="td"><VerdictPill verdict={d.verdict} reason={d.verdict_reason} /></td>
                    <td className="td font-mono">{d.size_lots ?? "—"}</td>
                    <td className="td max-w-[16rem] truncate" title={d.reason}>{d.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title={current ? `Decision #${current.id}` : "Decision"} className="min-h-0 overflow-auto">
        {!current ? (
          <Empty>select a row</Empty>
        ) : (
          <div className="flex flex-col gap-3 text-xs">
            <div className="flex items-center gap-2">
              <VerdictPill verdict={current.verdict} reason={current.verdict_reason} />
              <span className="font-semibold">
                {current.strategy_id}
                {current.variant ? ` · ${current.variant}` : ""}
              </span>
              <span className="ml-auto text-muted font-mono">{bothClocks(current.ts_utc)}</span>
            </div>

            <Step n={1} title="What the strategy saw">
              {current.context ? (
                <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 font-mono">
                  {Object.entries(current.context).map(([k, v]) => (
                    <React.Fragment key={k}>
                      <span className="text-muted">{k}</span>
                      <span>{String(v)}</span>
                    </React.Fragment>
                  ))}
                </div>
              ) : (
                "—"
              )}
            </Step>

            <Step n={2} title="AI context">
              {current.ai?.regime || current.ai?.bias ? (
                <span className="font-mono">
                  regime {current.ai.regime ?? "—"} · bias {current.ai.bias ?? 0} · size ×{current.ai.size_mult ?? 1} ·{" "}
                  {current.ai.block ? "BLOCK" : "no block"}
                </span>
              ) : (
                <span className="text-muted">neutral — no AI view was in force</span>
              )}
            </Step>

            <Step n={3} title="Intent">
              <span className="font-mono">
                {current.side} · confidence {current.confidence} · stop {current.stop_price} · take{" "}
                {current.take_price ?? "—"}
              </span>
              <div className="italic mt-1">“{current.reason}”</div>
            </Step>

            <Step n={4} title="Risk Engine">
              <span className={current.verdict === "APPROVED" ? "text-pos" : "text-neg"}>{current.verdict}</span>{" "}
              {current.verdict_reason}
            </Step>

            <Step n={5} title="Execution">
              {related.length === 0 ? (
                <span className="text-muted">nothing was sent</span>
              ) : (
                <div className="flex flex-col gap-1">
                  {related.map((o) => (
                    <div key={o.id} className="font-mono flex gap-2 flex-wrap">
                      <Pill className={o.ok ? "border-pos text-pos" : "border-neg text-neg"}>{o.kind}</Pill>
                      <span>{o.retcode_desc}</span>
                      {o.price_filled && <span>fill {o.price_filled}</span>}
                      {o.slippage_points !== null && <span>slip {o.slippage_points}pt</span>}
                      {o.sl_verified !== null && (
                        <span className={o.sl_verified ? "text-pos" : "text-neg"}>
                          SL {o.sl_verified ? "verified" : "NOT verified"}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </Step>

            <Step n={6} title="Outcome">
              {order?.position_ticket ? (
                <span className="font-mono">ticket {order.position_ticket}</span>
              ) : (
                <span className="text-muted">—</span>
              )}
              {current.tag && <Pill className="border-line text-muted ml-2">{current.tag}</Pill>}
            </Step>
          </div>
        )}
      </Card>
    </div>
  );
}

function Step({ n, title, children }) {
  return (
    <div className="flex gap-2 border-b border-dashed border-line pb-2">
      <span className="w-5 h-5 shrink-0 border-[1.5px] border-ink rounded-full grid place-items-center text-[10px] font-bold">
        {n}
      </span>
      <div className="min-w-0">
        <div className="font-bold">{title}</div>
        <div className="break-words">{children}</div>
      </div>
    </div>
  );
}

export { signed };
