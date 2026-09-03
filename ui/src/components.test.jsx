import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { Gauge, riskNumbers } from "./components";
import Header from "./Header";

// The bug this file exists for: with no core, the header showed "Daily loss 100.00% / 3.00%" and
// "Drawdown 100.00% / 30.00%" with both bars full red. equity defaulted to 0 and the divisor
// defaulted to 1, so "we have not heard from the core" rendered as "your account is wiped out" —
// the most alarming thing this header can say, for the most ordinary reason there is.

describe("riskNumbers", () => {
  test("no status at all means no numbers, not a catastrophe", () => {
    for (const nothing of [null, undefined, {}]) {
      const r = riskNumbers(nothing);
      expect(r.known).toBe(false);
      expect(r.equity).toBeNull();
      expect(r.dailyLossPct).toBeNull();
      expect(r.drawdownPct).toBeNull();
      expect(r.todayPnl).toBeNull();
    }
  });

  test("a healthy account sits at zero on both gauges", () => {
    const r = riskNumbers({ equity: 10000, day_start_equity: 10000, peak_equity: 10000 });
    expect(r.known).toBe(true);
    expect(r.dailyLossPct).toBe(0);
    expect(r.drawdownPct).toBe(0);
    expect(r.todayPnl).toBe(0);
  });

  test("a real loss is measured against the day's start and the peak", () => {
    const r = riskNumbers({ equity: 9700, day_start_equity: 10000, peak_equity: 12000 });
    expect(r.dailyLossPct).toBeCloseTo(3.0, 6);
    expect(r.drawdownPct).toBeCloseTo(19.1666, 3);
    expect(r.todayPnl).toBe(-300);
  });

  test("a gain never shows as a negative loss", () => {
    const r = riskNumbers({ equity: 10500, day_start_equity: 10000, peak_equity: 10000 });
    expect(r.dailyLossPct).toBe(0);
    expect(r.drawdownPct).toBe(0);
    expect(r.todayPnl).toBe(500);
  });

  test("missing marks fall back to equity itself, never to a divisor of one", () => {
    // A core that has just started has equity but no stored marks yet. That is 0% used, not 100%.
    const r = riskNumbers({ equity: 10000 });
    expect(r.dailyLossPct).toBe(0);
    expect(r.drawdownPct).toBe(0);
    expect(r.todayPnl).toBe(0);
  });

  test("zero equity with a real day start is a real total loss and still reads as one", () => {
    const r = riskNumbers({ equity: 0, day_start_equity: 10000, peak_equity: 10000 });
    expect(r.known).toBe(true);
    expect(r.dailyLossPct).toBe(100);
  });
});

describe("Gauge", () => {
  const bar = (container) => container.querySelector("div.h-full");

  test("an unknown value draws an empty bar and says nothing about it", () => {
    const { container } = render(<Gauge label="Daily loss" value={null} limit={3} />);
    expect(screen.getByText(/—/)).toBeTruthy();
    expect(container.textContent).not.toContain("100.00");
    expect(bar(container).style.width).toBe("0%");
    expect(bar(container).className).toContain("bg-line"); // neutral, not red
  });

  test("a known value fills the bar in proportion to the limit", () => {
    const { container } = render(<Gauge label="Daily loss" value={1.5} limit={3} />);
    expect(container.textContent).toContain("1.50%");
    expect(bar(container).style.width).toBe("50%");
  });

  test("colour follows how close the limit is, not the raw number", () => {
    const tone = (value) => bar(render(<Gauge label="x" value={value} limit={3} />).container).className;
    expect(tone(0.3)).toContain("bg-pos"); // 10% of the budget
    expect(tone(2.0)).toContain("bg-warn"); // 67%
    expect(tone(2.9)).toContain("bg-neg"); // 97%
  });

  test("a value past the limit stops at full rather than overflowing", () => {
    const { container } = render(<Gauge label="x" value={9} limit={3} />);
    expect(bar(container).style.width).toBe("100%");
  });
});

describe("Header", () => {
  const noop = () => {};

  test("with no core it claims no loss at all", () => {
    const { container } = render(<Header status={null} connection="disconnected" reachable={false} onKill={noop} />);
    expect(container.textContent).not.toContain("100.00%");
    expect(container.textContent).toContain("no core");
    expect(container.querySelector("button").disabled).toBe(true); // KILL needs a core to kill with
  });

  test("with a core it shows the account as it is", () => {
    const status = { state: "RUNNING", equity: 10000, day_start_equity: 10000, peak_equity: 10000 };
    const { container } = render(<Header status={status} connection="connected" reachable onKill={noop} />);
    expect(container.textContent).toContain("10,000.00");
    expect(container.textContent).toContain("0.00%");
    expect(container.textContent).not.toContain("100.00%");
  });
});
