import { describe, expect, test } from "vitest";
import { profitableShare, windowsLabel } from "./walkforward";

const row = (t) => ({ test_from: "2026-01-01", train_return_pct: 0.1, test_return_pct: t, test_trades: 3 });

describe("profitableShare", () => {
  test("nothing to count is null, not zero", () => {
    expect(profitableShare(null)).toBeNull();
    expect(profitableShare({ rows: [] })).toBeNull();
    expect(windowsLabel(undefined)).toBe("—");
  });

  test("a run stored by the current core carries the share", () => {
    expect(profitableShare({ windows: 9, profitable_windows: 4, profitable_share: 0.444, rows: [] })).toBe(0.444);
  });

  test("an older run derives it from the rows", () => {
    const wf = { rows: [row(5), row(-1), row(-1), row(-1)] };
    expect(profitableShare(wf)).toBe(0.25);
    expect(windowsLabel(wf)).toBe("1/4 (25%)");
  });

  test("the GBPUSD case reads as the coin flip it was", () => {
    expect(windowsLabel({ windows: 9, profitable_windows: 4, profitable_share: 0.444 })).toBe("4/9 (44%)");
  });
});
