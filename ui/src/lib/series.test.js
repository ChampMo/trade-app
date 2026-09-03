import { describe, expect, test } from "vitest";
import { equitySeries, extent, holdHours, linePath, scaler, tradePoints } from "./series";

const trade = (net, closed, side = "LONG", entry = 1.1, exit = 1.11) => ({
  opened_utc: "2026-01-01T00:00:00+00:00",
  closed_utc: closed,
  side,
  entry,
  exit,
  net,
});

describe("equitySeries", () => {
  test("money accumulates in the order the trades closed, not the order they were stored", () => {
    const series = equitySeries(
      [trade(50, "2026-01-03T00:00:00+00:00"), trade(-20, "2026-01-02T00:00:00+00:00")],
      10000,
    );
    expect(series.map((p) => p.equity)).toEqual([9980, 10030]);
    expect(series.map((p) => p.i)).toEqual([1, 2]);
  });

  test("drawdown is measured from the highest point reached so far", () => {
    const series = equitySeries(
      [trade(1000, "2026-01-01T00:00:00+00:00"), trade(-550, "2026-01-02T00:00:00+00:00")],
      10000,
    );
    expect(series[0].drawdown).toBe(0);
    expect(series[1].drawdown).toBeCloseTo(5.0, 6);
  });

  test("a trade that never closed is not on the curve", () => {
    expect(equitySeries([trade(10, null), trade(5, "2026-01-02T00:00:00+00:00")], 100)).toHaveLength(1);
  });

  test("no trades is an empty curve, not a crash", () => {
    expect(equitySeries([], 10000)).toEqual([]);
    expect(equitySeries(undefined, 10000)).toEqual([]);
  });

  test("wins and losses are marked so the chart can colour them", () => {
    const series = equitySeries([trade(5, "2026-01-01T00:00:00+00:00"), trade(-5, "2026-01-02T00:00:00+00:00")], 0);
    expect(series.map((p) => p.win)).toEqual([true, false]);
  });
});

describe("drawing helpers", () => {
  test("a flat series still gets a band to draw in", () => {
    expect(extent([5, 5, 5])).toEqual([4, 6]);
  });

  test("an empty series does not divide by zero", () => {
    expect(extent([])).toEqual([0, 1]);
  });

  test("the y scale is flipped, because SVG counts downwards", () => {
    const y = scaler([0, 100], 200, true);
    expect(y(0)).toBe(200);
    expect(y(100)).toBe(0);
    expect(y(50)).toBe(100);
  });

  test("a path starts with a move and continues with lines", () => {
    const path = linePath([{ v: 0 }, { v: 1 }], (p) => p.v * 10, (p) => p.v * 2);
    expect(path).toBe("M0.00,0.00 L10.00,2.00");
  });
});

describe("per-trade numbers", () => {
  test("a short that fell is a winner, in points", () => {
    expect(tradePoints(trade(0, "x", "SHORT", 1.1, 1.09))).toBeCloseTo(1000, 6);
  });

  test("a long that fell is a loser, in points", () => {
    expect(tradePoints(trade(0, "x", "LONG", 1.1, 1.09))).toBeCloseTo(-1000, 6);
  });

  test("a trade with no exit has no points", () => {
    expect(tradePoints({ side: "LONG", entry: 1.1, exit: null })).toBeNull();
  });

  test("hold time comes out in hours", () => {
    expect(holdHours(trade(0, "2026-01-01T12:00:00+00:00"))).toBe(12);
  });
});
