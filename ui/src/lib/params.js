// Strategy parameters typed as text, the same shape the command line takes:
//   trail_atr_mult=2.5, break_even_r=1
//
// Deliberately not a form with a field per parameter: each strategy has its own, they change with
// the code, and a form generated from a schema would be a second place for that schema to drift.
// Text that mirrors `--param` is honest about what it is.

export function parseParams(text) {
  const out = {};
  for (const piece of String(text || "").split(/[,\n]/)) {
    const pair = piece.trim();
    if (!pair) continue;
    const eq = pair.indexOf("=");
    if (eq < 1) continue;
    const key = pair.slice(0, eq).trim();
    const raw = pair.slice(eq + 1).trim();
    if (!key || !raw) continue;
    if (raw === "true" || raw === "false") {
      out[key] = raw === "true";
    } else if (raw !== "" && Number.isFinite(Number(raw))) {
      out[key] = Number(raw);
    } else {
      out[key] = raw;
    }
  }
  return out;
}

export function describeParams(params) {
  const entries = Object.entries(params || {});
  return entries.length ? entries.map(([k, v]) => `${k}=${v}`).join(", ") : "defaults";
}
