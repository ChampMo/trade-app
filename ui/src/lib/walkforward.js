// The share of out-of-sample windows that made money (D32). The core stores it for new runs;
// runs saved before the field existed still carry their rows, so it is derived from those.
// Null when there is nothing to count, so the page shows a dash and never a 0%.
export function profitableShare(wf) {
  if (!wf) return null;
  if (typeof wf.profitable_share === "number") return wf.profitable_share;
  const rows = wf.rows || [];
  if (rows.length === 0) return null;
  return rows.filter((w) => w.test_return_pct > 0).length / rows.length;
}

// "4/9 (44%)": the count is the evidence, the percent is what the gate reads.
export function windowsLabel(wf) {
  const share = profitableShare(wf);
  if (share == null) return "—";
  const total = wf.windows ?? wf.rows?.length ?? 0;
  const won = wf.profitable_windows ?? Math.round(share * total);
  return `${won}/${total} (${Math.round(share * 100)}%)`;
}
