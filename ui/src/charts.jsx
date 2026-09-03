import React, { useMemo, useState } from "react";
import { equitySeries, extent, holdHours, linePath, pad, scaler, tradePoints } from "./lib/series";
import { money, signed, utc } from "./lib/format";

// Hand-drawn SVG rather than a charting library: two shapes are needed, a line and some dots, and
// a dependency that ships a whole grammar of graphics to draw them would be the larger cost. The
// visual language matches the rest of the app — hairline axes, mono numerals, colour only where it
// carries meaning.

const CHART = { w: 900, h: 220, left: 56, right: 12, top: 12, bottom: 22 };

export function EquityCurve({ trades, startBalance, onPick, selected }) {
  const series = useMemo(() => equitySeries(trades, startBalance), [trades, startBalance]);
  const [hover, setHover] = useState(null);

  if (!series.length) {
    return <div className="text-xs text-muted py-8 text-center">this run closed no trades</div>;
  }

  const innerW = CHART.w - CHART.left - CHART.right;
  const innerH = CHART.h - CHART.top - CHART.bottom;
  const x = scaler([1, series.length], innerW);
  const y = scaler(pad(extent([startBalance, ...series.map((p) => p.equity)])), innerH, true);
  const path = linePath(series, (p) => x(p.i), (p) => y(p.equity));
  const base = y(startBalance);
  const shown = hover ?? (selected ? series.find((p) => p.trade === selected) : null) ?? series[series.length - 1];

  return (
    <div className="flex flex-col gap-1">
      <svg viewBox={`0 0 ${CHART.w} ${CHART.h}`} className="w-full" role="img" aria-label="equity by trade">
        <g transform={`translate(${CHART.left},${CHART.top})`}>
          {/* the starting balance: above it the run made money, below it lost */}
          <line x1="0" y1={base} x2={innerW} y2={base} className="stroke-line" strokeDasharray="3 3" strokeWidth="1" />
          <path d={path} fill="none" className="stroke-ink" strokeWidth="1.5" />
          {series.map((p) => (
            <circle
              key={p.i}
              cx={x(p.i)}
              cy={y(p.equity)}
              r={p.trade === selected ? 4 : 2.5}
              className={p.win ? "fill-pos" : "fill-neg"}
              stroke={p.trade === selected ? "currentColor" : "none"}
              strokeWidth="1.5"
              onMouseEnter={() => setHover(p)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onPick?.(p.trade)}
              style={{ cursor: onPick ? "pointer" : "default" }}
            />
          ))}
          <line x1="0" y1={innerH} x2={innerW} y2={innerH} className="stroke-ink" strokeWidth="1" />
          <line x1="0" y1="0" x2="0" y2={innerH} className="stroke-ink" strokeWidth="1" />
        </g>
        <text x="4" y={CHART.top + 8} className="fill-muted" fontSize="9" fontFamily="ui-monospace, monospace">
          {money(Math.max(startBalance, ...series.map((p) => p.equity)), 0)}
        </text>
        <text x="4" y={CHART.top + innerH} className="fill-muted" fontSize="9" fontFamily="ui-monospace, monospace">
          {money(Math.min(startBalance, ...series.map((p) => p.equity)), 0)}
        </text>
      </svg>
      <div className="flex justify-between text-[11px] text-muted font-mono">
        <span>trade 1 · {utc(series[0].t, true)}</span>
        <span>
          {shown ? `#${shown.i} ${signed(shown.net)} → ${money(shown.equity)} · dd ${shown.drawdown.toFixed(2)}%` : ""}
        </span>
        <span>
          trade {series.length} · {utc(series[series.length - 1].t, true)}
        </span>
      </div>
      <p className="text-[11px] text-muted">
        Equity <b>by trade</b>, not by bar: it moves when a position closes. Each dot is one round trip — green made
        money, red lost it. Click one to see the bars around it.
      </p>
    </div>
  );
}

const PRICE = { w: 900, h: 260, left: 58, right: 14, top: 12, bottom: 20 };

export function TradeChart({ bars, trade, point = 0.00001 }) {
  if (!trade) return <div className="text-xs text-muted py-8 text-center">pick a trade to see its bars</div>;
  if (!bars) return <div className="text-xs text-muted py-8 text-center">loading bars…</div>;
  if (!bars.length) {
    return <div className="text-xs text-muted py-8 text-center">no stored bars for this window</div>;
  }

  const innerW = PRICE.w - PRICE.left - PRICE.right;
  const innerH = PRICE.h - PRICE.top - PRICE.bottom;
  const times = bars.map((b) => new Date(b.t).getTime());
  const levels = [trade.entry, trade.exit, trade.sl, trade.tp].filter((v) => v > 0);
  const x = scaler([Math.min(...times), Math.max(...times)], innerW);
  const y = scaler(pad(extent([...bars.map((b) => b.h), ...bars.map((b) => b.l), ...levels])), innerH, true);
  const at = (iso) => x(new Date(iso).getTime());
  const width = Math.max(2, (innerW / bars.length) * 0.6);

  const level = (value, cls, label, dashed = true) =>
    value > 0 ? (
      <g key={label}>
        <line
          x1="0"
          y1={y(value)}
          x2={innerW}
          y2={y(value)}
          className={cls}
          strokeWidth="1"
          strokeDasharray={dashed ? "4 3" : undefined}
        />
        <text x={innerW - 2} y={y(value) - 3} textAnchor="end" fontSize="9" className="fill-muted" fontFamily="ui-monospace, monospace">
          {label} {value}
        </text>
      </g>
    ) : null;

  return (
    <div className="flex flex-col gap-1">
      <svg viewBox={`0 0 ${PRICE.w} ${PRICE.h}`} className="w-full" role="img" aria-label="price around the trade">
        <g transform={`translate(${PRICE.left},${PRICE.top})`}>
          {level(trade.sl, "stroke-neg", "SL")}
          {level(trade.tp, "stroke-pos", "TP")}
          {bars.map((b) => {
            const cx = x(new Date(b.t).getTime());
            const up = b.c >= b.o;
            return (
              <g key={b.t} className={up ? "stroke-pos fill-pos" : "stroke-neg fill-neg"}>
                <line x1={cx} y1={y(b.h)} x2={cx} y2={y(b.l)} strokeWidth="1" />
                <rect
                  x={cx - width / 2}
                  y={Math.min(y(b.o), y(b.c))}
                  width={width}
                  height={Math.max(1, Math.abs(y(b.o) - y(b.c)))}
                  fillOpacity={up ? 0.25 : 1}
                  strokeWidth="1"
                />
              </g>
            );
          })}
          {/* the two moments that cost or made the money */}
          <g className="stroke-ink">
            <line x1={at(trade.opened_utc)} y1="0" x2={at(trade.opened_utc)} y2={innerH} strokeWidth="1" strokeDasharray="2 3" />
            <line x1={at(trade.closed_utc)} y1="0" x2={at(trade.closed_utc)} y2={innerH} strokeWidth="1" strokeDasharray="2 3" />
          </g>
          <Marker x={at(trade.opened_utc)} y={y(trade.entry)} side={trade.side} label="in" />
          <Marker x={at(trade.closed_utc)} y={y(trade.exit)} side={trade.side === "LONG" ? "SHORT" : "LONG"} label="out" />
          <line x1="0" y1={innerH} x2={innerW} y2={innerH} className="stroke-ink" strokeWidth="1" />
          <line x1="0" y1="0" x2="0" y2={innerH} className="stroke-ink" strokeWidth="1" />
        </g>
      </svg>
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] font-mono text-muted">
        <span>{trade.side} {trade.volume} lots</span>
        <span>in {utc(trade.opened_utc, true)} @ {trade.entry}</span>
        <span>out {utc(trade.closed_utc, true)} @ {trade.exit}</span>
        <span className={trade.net >= 0 ? "text-pos" : "text-neg"}>{signed(trade.net)}</span>
        <span>{tradePoints(trade, point)?.toFixed(0)} pt</span>
        <span>{holdHours(trade)?.toFixed(1)}h</span>
        <span>exit: {trade.exit_reason}</span>
      </div>
    </div>
  );
}

function Marker({ x, y, side, label }) {
  const up = side === "LONG";
  const d = up ? `M${x},${y - 9} l5,9 l-10,0 z` : `M${x},${y + 9} l5,-9 l-10,0 z`;
  return (
    <g className={up ? "fill-pos" : "fill-neg"}>
      <path d={d} />
      <text x={x + 7} y={y + (up ? -6 : 12)} fontSize="9" className="fill-muted" fontFamily="ui-monospace, monospace">
        {label}
      </text>
    </g>
  );
}

const MINI = { w: 900, h: 180, left: 52, right: 12, top: 10, bottom: 16 };

/** Recent bars for one market, with the last close called out. No trade, no markers, no fuss. */
export function PriceChart({ bars, digits = 5 }) {
  if (!bars) return <div className="text-xs text-muted py-6 text-center">loading bars…</div>;
  if (!bars.length) return <div className="text-xs text-muted py-6 text-center">no stored bars for this market</div>;

  const innerW = MINI.w - MINI.left - MINI.right;
  const innerH = MINI.h - MINI.top - MINI.bottom;
  const highs = bars.map((b) => b.h);
  const lows = bars.map((b) => b.l);
  const y = scaler(pad(extent([...highs, ...lows]), 0.05), innerH, true);
  const x = scaler([0, Math.max(1, bars.length - 1)], innerW);
  const last = bars[bars.length - 1];
  const width = Math.max(1.5, (innerW / bars.length) * 0.62);

  return (
    <div className="flex flex-col gap-1">
      <svg viewBox={`0 0 ${MINI.w} ${MINI.h}`} className="w-full" role="img" aria-label="recent bars">
        <g transform={`translate(${MINI.left},${MINI.top})`}>
          <line
            x1="0"
            y1={y(last.c)}
            x2={innerW}
            y2={y(last.c)}
            className="stroke-line"
            strokeDasharray="3 3"
            strokeWidth="1"
          />
          {bars.map((b, i) => {
            const cx = x(i);
            const up = b.c >= b.o;
            return (
              <g key={b.t} className={up ? "stroke-pos fill-pos" : "stroke-neg fill-neg"}>
                <line x1={cx} y1={y(b.h)} x2={cx} y2={y(b.l)} strokeWidth="1" />
                <rect
                  x={cx - width / 2}
                  y={Math.min(y(b.o), y(b.c))}
                  width={width}
                  height={Math.max(1, Math.abs(y(b.o) - y(b.c)))}
                  fillOpacity={up ? 0.25 : 1}
                  strokeWidth="1"
                />
              </g>
            );
          })}
          <line x1="0" y1={innerH} x2={innerW} y2={innerH} className="stroke-ink" strokeWidth="1" />
        </g>
        <text x="4" y={MINI.top + 8} className="fill-muted" fontSize="9" fontFamily="ui-monospace, monospace">
          {Math.max(...highs).toFixed(digits)}
        </text>
        <text x="4" y={MINI.top + innerH} className="fill-muted" fontSize="9" fontFamily="ui-monospace, monospace">
          {Math.min(...lows).toFixed(digits)}
        </text>
      </svg>
      <div className="flex justify-between text-[11px] text-muted font-mono">
        <span>{utc(bars[0].t, true)}</span>
        <span>{bars.length} bars</span>
        <span>{utc(last.t, true)}</span>
      </div>
    </div>
  );
}
