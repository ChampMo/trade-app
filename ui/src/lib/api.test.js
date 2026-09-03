import { afterEach, describe, expect, test, vi } from "vitest";
import { api } from "./api";

// The second half of the same bug: the UI reported "500 Internal Server Error" when the real
// story was that no core was running. The 500 comes from the dev proxy failing to connect, not
// from the core — and a core that is running always answers JSON, even when it says no.

const jsonResponse = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: "",
  text: async () => JSON.stringify(body),
});

const rawResponse = (status, statusText, text = "") => ({
  ok: false,
  status,
  statusText,
  text: async () => text,
});

afterEach(() => vi.unstubAllGlobals());

describe("what the client concludes when things go wrong", () => {
  test("a request that never arrives means no core", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api.status()).rejects.toMatchObject({ coreDown: true });
  });

  test("a 5xx with no JSON in it is the proxy, not the core", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(rawResponse(500, "Internal Server Error")));
    await expect(api.status()).rejects.toMatchObject({ coreDown: true, status: 500 });
  });

  test("an HTML error page does not crash the parser", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(rawResponse(502, "Bad Gateway", "<html>nope</html>")));
    const error = await api.status().catch((e) => e);
    expect(error.coreDown).toBe(true);
    expect(error.message).toContain("502"); // not a JSON.parse SyntaxError
  });

  test("a core saying no is an answer, not a dead core", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(409, { detail: "engine is KILLED" })));
    const error = await api.unlock("looked at it").catch((e) => e);
    expect(error.coreDown).toBe(false);
    expect(error.status).toBe(409);
    expect(error.message).toBe("engine is KILLED");
  });

  test("a 500 that carries JSON is the core failing, and says so", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(500, { detail: "broker unreadable" })));
    const error = await api.positions().catch((e) => e);
    expect(error.coreDown).toBe(false);
    expect(error.message).toBe("broker unreadable");
  });

  test("a 400 without a reason still reads as an answer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(400, { detail: "unlock requires a reason" })));
    await expect(api.unlock("")).rejects.toMatchObject({ coreDown: false, status: 400 });
  });
});

describe("the ordinary path", () => {
  test("a healthy answer comes back parsed", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, { state: "RUNNING", equity: 10000 })));
    await expect(api.status()).resolves.toEqual({ state: "RUNNING", equity: 10000 });
  });

  test("every read goes to the core's own prefix and nowhere else", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, []));
    vi.stubGlobal("fetch", fetchMock);
    await api.runs(5, "ema_cross");
    const [url] = fetchMock.mock.calls[0];
    expect(url.startsWith("/api/")).toBe(true);
    expect(url).toContain("strategy=ema_cross");
  });

  test("an empty body is null, not a parse error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, statusText: "", text: async () => "" }));
    await expect(api.resume()).resolves.toBeNull();
  });
});
