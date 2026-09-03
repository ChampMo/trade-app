// Turning a stored backtest into something that can be drawn.
//
// The equity curve is deliberately not stored with a run: it is one point per bar, megabytes for
// something no comparison reads. What is stored is every closed trade, and a curve built from
// those is the one a trader actually reads — it moves when money moves, not when a bar closes.
// Say so on the axis rather than implying a per-bar curve.

export function equitySeries(trades = [], startBalance = 0) {
  const closed = [...trades]
    .filter((t) => t && t.closed_utc)
    .sort((a, b) => new Date(a.closed_utc) - new Date(b.closed_utc));

  let equity = startBalance;
  let peak = startBalance;
  return closed.map((trade, i) => {
    equity += trade.net ?? 0;
    peak = Math.max(peak, equity);
    return {
      i: i + 1,
      t: trade.closed_utc,
      equity,
      drawdown: peak > 0 ? ((peak - equity) / peak) * 100 : 0,
      net: trade.net ?? 0,
      win: (trade.net ?? 0) > 0,
      trade,
    };
  });
}

export function extent(values) {
  if (!values.length) return [0, 1];
  let min = values[0];
  let max = values[0];
  for (const v of values) {
    if (v < min) min = v;
    if (v > max) max = v;
  }
  // A flat series still needs a band to draw in, or every point lands on one line.
  return min === max ? [min - 1, max + 1] : [min, max];
}

export function pad([min, max], fraction = 0.08) {
  const room = (max - min) * fraction;
  return [min - room, max + room];
}

/** Maps a value in [min,max] onto [0,size], flipped for SVG's downward y axis. */
export function scaler([min, max], size, flip = false) {
  const span = max - min || 1;
  return (v) => (flip ? size - ((v - min) / span) * size : ((v - min) / span) * size);
}

export function linePath(points, x, y) {
  return points.map((p, i) => `${i === 0 ? "M" : "L"}${x(p).toFixed(2)},${y(p).toFixed(2)}`).join(" ");
}

/** Points, signed the way a trader reads them: what the move was worth, not what direction it went. */
export function tradePoints(trade, point = 0.00001) {
  if (!trade || trade.entry == null || trade.exit == null || !point) return null;
  const sign = trade.side === "LONG" ? 1 : -1;
  return (sign * (trade.exit - trade.entry)) / point;
}

export function holdHours(trade) {
  if (!trade?.opened_utc || !trade?.closed_utc) return null;
  return (new Date(trade.closed_utc) - new Date(trade.opened_utc)) / 3600000;
}
