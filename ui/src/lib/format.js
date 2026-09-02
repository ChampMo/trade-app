export const money = (n, digits = 2) =>
  n === null || n === undefined ? "—" : Number(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const signed = (n, digits = 2) =>
  n === null || n === undefined ? "—" : `${n >= 0 ? "+" : ""}${money(n, digits)}`;

export const pct = (n, digits = 2) => (n === null || n === undefined ? "—" : `${Number(n).toFixed(digits)}%`);

export const utc = (iso, withDate = false) => {
  if (!iso) return "—";
  const d = new Date(iso);
  const time = d.toISOString().slice(11, 19);
  return withDate ? `${d.toISOString().slice(0, 10)} ${time}` : time;
};

export const local = (iso) => (iso ? new Date(iso).toLocaleTimeString() : "—");

// Both clocks, always. The journal is UTC (D13) and the trader lives somewhere else, and confusing
// the two is how a decision gets read against the wrong bar.
export const bothClocks = (iso) => (iso ? `${utc(iso)} UTC · ${local(iso)}` : "—");

export const severityClass = (severity) =>
  ({
    CRIT: "border-neg text-neg bg-red-50",
    WARN: "border-warn text-warn bg-amber-50",
    INFO: "border-line text-muted bg-slate-50",
  })[severity] || "border-line text-muted";

export const verdictClass = (verdict) =>
  verdict === "APPROVED" ? "border-pos text-pos bg-emerald-50" : "border-neg text-neg bg-red-50";

export const stateClass = (state) =>
  ({
    RUNNING: "border-pos text-pos bg-emerald-50",
    PAUSED: "border-warn text-warn bg-amber-50",
    KILLED: "border-neg text-neg bg-red-50",
    STARTING: "border-line text-muted bg-slate-50",
  })[state] || "border-line text-muted";
