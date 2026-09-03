import { describe, expect, test } from "vitest";
import { describeParams, parseParams } from "./params";

describe("parseParams", () => {
  test("numbers come out as numbers, so a strategy never has to defend itself", () => {
    expect(parseParams("trail_atr_mult=2.5, break_even_r=1")).toEqual({ trail_atr_mult: 2.5, break_even_r: 1 });
  });

  test("booleans are recognised", () => {
    expect(parseParams("use_trail=true, greedy=false")).toEqual({ use_trail: true, greedy: false });
  });

  test("anything else stays text", () => {
    expect(parseParams("mode=aggressive")).toEqual({ mode: "aggressive" });
  });

  test("blank input is no parameters, not a crash", () => {
    for (const nothing of ["", "   ", null, undefined, ",,"]) {
      expect(parseParams(nothing)).toEqual({});
    }
  });

  test("half-typed pairs are ignored rather than sent as nonsense", () => {
    expect(parseParams("trail_atr_mult=, =2.5, justtext, ok=1")).toEqual({ ok: 1 });
  });

  test("newlines separate pairs too", () => {
    expect(parseParams("a=1\nb=2")).toEqual({ a: 1, b: 2 });
  });

  test("spaces around the pieces do not matter", () => {
    expect(parseParams("  a = 1 ,  b=2  ")).toEqual({ a: 1, b: 2 });
  });
});

describe("describeParams", () => {
  test("nothing set reads as defaults", () => {
    expect(describeParams({})).toBe("defaults");
    expect(describeParams(undefined)).toBe("defaults");
  });

  test("what is set reads back the way it was typed", () => {
    expect(describeParams({ trail_atr_mult: 2.5 })).toBe("trail_atr_mult=2.5");
  });
});
