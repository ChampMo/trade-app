import React, { useCallback, useEffect, useState } from "react";
import { api, eventSocket } from "./lib/api";
import { FrozenBanner, KilledBanner, ReasonDialog } from "./components";
import Header from "./Header";
import Dashboard from "./pages/Dashboard";
import Strategies from "./pages/Strategies";
import Journal from "./pages/Journal";
import Events from "./pages/Events";

const PAGES = [
  { id: "dashboard", label: "Dashboard", Component: Dashboard },
  { id: "strategies", label: "Strategies", Component: Strategies },
  { id: "journal", label: "Journal", Component: Journal },
  { id: "events", label: "Events", Component: Events },
];

export default function App() {
  const [page, setPage] = useState("dashboard");
  const [status, setStatus] = useState(null);
  const [live, setLive] = useState([]);
  const [connection, setConnection] = useState("connecting");
  const [error, setError] = useState(null);
  const [dialog, setDialog] = useState(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.status());
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    return eventSocket(
      (event) => setLive((prev) => [...prev.slice(-299), event]),
      (state) => setConnection(state),
    );
  }, []);

  const act = async (fn) => {
    await fn();
    await refresh();
    setDialog(null);
  };

  const Current = PAGES.find((p) => p.id === page)?.Component ?? Dashboard;
  const reachable = !error;

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <Header status={status} connection={connection} reachable={reachable} onKill={() => setDialog("kill")} />

      <KilledBanner status={status} onUnlock={() => setDialog("unlock")} />
      <FrozenBanner status={status} />

      <div className="flex flex-1 min-h-0">
        <nav className="w-44 shrink-0 border-r-2 border-ink bg-surface flex flex-col py-2">
          {PAGES.map((p) => (
            <button
              key={p.id}
              className={`px-4 py-2 text-left text-[15px] font-semibold ${
                page === p.id ? "bg-ink text-white" : "hover:bg-slate-100"
              }`}
              onClick={() => setPage(p.id)}
            >
              {p.label}
            </button>
          ))}
          <div className="mt-auto px-4 py-3 text-[11px] text-muted leading-relaxed">
            {status?.state === "PAUSED" ? (
              <button className="btn w-full mb-2" onClick={() => act(() => api.resume())}>
                Resume
              </button>
            ) : status?.state === "RUNNING" ? (
              <button className="btn w-full mb-2" onClick={() => setDialog("pause")}>
                Pause
              </button>
            ) : null}
            ticks {status?.service?.ticks ?? "—"}
            <br />
            core :8001
          </div>
        </nav>

        <main className="flex-1 min-w-0 overflow-auto p-4">
          {error ? (
            <div className="card border-neg">
              <h3 className="card-title text-neg">Cannot reach the core</h3>
              <p className="text-sm">{error}</p>
              <p className="text-xs text-muted">
                The core runs separately from this window — that is deliberate, so closing the UI never stops
                trading. Start it with <code className="font-mono">python -m tradeapp serve</code>.
              </p>
            </div>
          ) : (
            <Current status={status} live={live} refresh={refresh} />
          )}
        </main>
      </div>

      <ReasonDialog
        open={dialog === "kill"}
        title="Kill switch"
        danger
        requireText="KILL"
        confirmLabel="KILL"
        body={
          <>
            Close <b>every</b> open position and stop trading. Pending intents are dropped and unlocking will
            need a reason.
          </>
        }
        onConfirm={(reason) => act(() => api.kill(reason))}
        onCancel={() => setDialog(null)}
      />
      <ReasonDialog
        open={dialog === "unlock"}
        title="Unlock trading"
        confirmLabel="Unlock → PAUSED"
        body={<>Unlocking lands in PAUSED, not RUNNING. You then have to press Resume deliberately.</>}
        onConfirm={(reason) => act(() => api.unlock(reason))}
        onCancel={() => setDialog(null)}
      />
      <ReasonDialog
        open={dialog === "pause"}
        title="Pause trading"
        confirmLabel="Pause"
        body={<>No new positions. Open ones keep their stops at the broker.</>}
        onConfirm={(reason) => act(() => api.pause(reason))}
        onCancel={() => setDialog(null)}
      />
    </div>
  );
}
