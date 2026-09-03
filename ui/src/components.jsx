import React, { useState } from "react";
import { money, pct, severityClass, stateClass, verdictClass } from "./lib/format";

export function Pill({ children, className = "" }) {
  return <span className={`pill ${className}`}>{children}</span>;
}

export function StatePill({ state }) {
  return <Pill className={stateClass(state)}>{state}</Pill>;
}

export function SeverityPill({ severity }) {
  return <Pill className={severityClass(severity)}>{severity}</Pill>;
}

export function VerdictPill({ verdict, reason }) {
  const label = verdict === "APPROVED" ? "APPROVED" : `REJECTED · ${(reason || "").split(":")[0]}`;
  return <Pill className={verdictClass(verdict)}>{label}</Pill>;
}

export function Stat({ label, value, className = "" }) {
  return (
    <div className="flex flex-col gap-px min-w-0">
      <span className="stat-k">{label}</span>
      <span className={`stat-v ${className}`}>{value}</span>
    </div>
  );
}

export function Gauge({ label, value, limit, unit = "%" }) {
  const known = typeof value === "number" && Number.isFinite(value);
  const ratio = known && limit > 0 ? Math.min(1, Math.max(0, value / limit)) : 0;
  // Colour by how close to the limit, not by the raw number: 2% of a 3% budget is nearly spent.
  // An unknown value gets an empty bar, never a full one.
  const tone = !known ? "bg-line" : ratio >= 0.9 ? "bg-neg" : ratio >= 0.6 ? "bg-warn" : "bg-pos";
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-xs">
        <span className="font-semibold">{label}</span>
        <span className="font-mono">
          {known ? value.toFixed(2) + unit : "—"} / {limit.toFixed(2)}
          {unit}
        </span>
      </div>
      <div className="h-2.5 border-[1.5px] border-ink rounded-sm bg-surface overflow-hidden">
        <div className={`h-full ${tone}`} style={{ width: `${ratio * 100}%` }} />
      </div>
    </div>
  );
}

export function Card({ title, right, children, className = "" }) {
  return (
    <div className={`card ${className}`}>
      {(title || right) && (
        <h3 className="card-title">
          {title}
          {right && <span className="ml-auto font-medium normal-case tracking-normal">{right}</span>}
        </h3>
      )}
      {children}
    </div>
  );
}

export function Empty({ children }) {
  return <div className="text-xs text-muted py-6 text-center">{children}</div>;
}

/** Anything that changes state asks for a reason, because the reason is what the journal keeps. */
export function ReasonDialog({ open, title, body, confirmLabel, requireText, onConfirm, onCancel, danger }) {
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  if (!open) return null;

  const ready = reason.trim().length > 0 && (!requireText || typed.trim() === requireText);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm(reason.trim());
      setReason("");
      setTyped("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="card w-[440px] max-w-full gap-3">
        <h3 className="card-title">{title}</h3>
        <div className="text-sm">{body}</div>
        <label className="flex flex-col gap-1">
          <span className="stat-k">Reason · required · goes in the journal</span>
          <textarea
            className="border-[1.5px] border-ink rounded px-2 py-1 text-sm h-16 resize-none"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            autoFocus
          />
        </label>
        {requireText && (
          <label className="flex flex-col gap-1">
            <span className="stat-k">Type {requireText} to confirm</span>
            <input
              className="border-[1.5px] border-ink rounded px-2 py-1 font-mono"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
            />
          </label>
        )}
        {error && <div className="text-xs text-neg border-[1.5px] border-neg rounded p-2 bg-red-50">{error}</div>}
        <div className="flex justify-end gap-2">
          <button className="btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            className={`btn ${danger ? "border-neg text-neg" : "bg-ink text-white"}`}
            onClick={confirm}
            disabled={!ready || busy}
          >
            {busy ? "…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export function KilledBanner({ status, onUnlock }) {
  if (status?.state !== "KILLED") return null;
  return (
    <div className="bg-neg text-white px-4 py-2 flex items-center gap-4 font-bold text-sm shrink-0">
      <span>KILLED — trading stopped. Positions were closed and nothing new will open.</span>
      <button className="btn border-white text-neg bg-white ml-auto" onClick={onUnlock}>
        Unlock…
      </button>
    </div>
  );
}

export function FrozenBanner({ status }) {
  if (!status?.frozen || status?.state === "KILLED") return null;
  return (
    <div className="bg-amber-100 border-b-[1.5px] border-warn text-warn px-4 py-2 text-sm font-semibold shrink-0">
      Frozen by reconcile: {status.freeze_reason}
    </div>
  );
}

export function riskNumbers(status) {
  // No core, no numbers. The previous version divided by a fallback of 1, which turned "we have
  // not heard from the core" into "you are down 100%" with both bars full red — the single most
  // alarming thing this header can say, shown for the most ordinary reason there is.
  const equity = status?.equity;
  if (typeof equity !== "number") {
    return { equity: null, dailyLossPct: null, drawdownPct: null, todayPnl: null, known: false };
  }
  const dayStart = status?.day_start_equity > 0 ? status.day_start_equity : equity;
  const peak = status?.peak_equity > 0 ? status.peak_equity : equity;
  return {
    equity,
    dailyLossPct: dayStart > 0 ? Math.max(0, ((dayStart - equity) / dayStart) * 100) : 0,
    drawdownPct: peak > 0 ? Math.max(0, ((peak - equity) / peak) * 100) : 0,
    todayPnl: equity - dayStart,
    known: true,
  };
}

export { money, pct };
