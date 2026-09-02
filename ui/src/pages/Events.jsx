import React, { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { Card, Empty, SeverityPill } from "../components";
import { utc } from "../lib/format";

export default function Events({ live }) {
  const [rows, setRows] = useState([]);
  const [severity, setSeverity] = useState("all");
  const [query, setQuery] = useState("");

  useEffect(() => {
    api.events(0, 500).then(setRows).catch(() => {});
  }, []);

  // History from the API, new arrivals from the socket, de-duplicated by id.
  const all = useMemo(() => {
    const byId = new Map();
    for (const e of [...rows, ...live]) byId.set(e.id, e);
    return [...byId.values()].sort((a, b) => b.id - a.id);
  }, [rows, live]);

  const shown = all.filter(
    (e) =>
      (severity === "all" || e.severity === severity) &&
      (query === "" || `${e.source} ${e.message}`.toLowerCase().includes(query.toLowerCase())),
  );

  return (
    <Card
      title={`Events · ${shown.length}`}
      right={
        <span className="flex gap-1 items-center">
          {["all", "CRIT", "WARN", "INFO"].map((s) => (
            <button
              key={s}
              className={`btn text-[11px] px-2 py-0.5 ${severity === s ? "bg-ink text-white" : ""}`}
              onClick={() => setSeverity(s)}
            >
              {s}
            </button>
          ))}
          <input
            className="border-[1.5px] border-ink rounded px-2 py-0.5 text-xs w-40"
            placeholder="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </span>
      }
    >
      {shown.length === 0 ? (
        <Empty>nothing matches</Empty>
      ) : (
        <div className="overflow-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface">
              <tr>
                {["UTC", "Sev", "Source", "Message", "Data"].map((h) => (
                  <th key={h} className="th">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.slice(0, 400).map((e) => (
                <tr key={e.id}>
                  <td className="td font-mono text-muted">{utc(e.ts_utc, true)}</td>
                  <td className="td"><SeverityPill severity={e.severity} /></td>
                  <td className="td font-mono">{e.source}</td>
                  <td className="td whitespace-normal">{e.message}</td>
                  <td className="td font-mono text-muted max-w-md truncate" title={JSON.stringify(e.data)}>
                    {e.data ? JSON.stringify(e.data) : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
