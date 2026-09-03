// Everything the UI knows about the core goes through here (D7).
// The UI has no MT5 client, no broker credentials and no way to place an order — it can only ask
// the core, which is what keeps the trading rules in one auditable place.

const BASE = "/api";

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (e) {
    // The request never arrived: nothing is listening, or the dev proxy could not connect.
    const error = new Error("nothing is listening on the core's port");
    error.coreDown = true;
    throw error;
  }

  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null; // an HTML error page from the proxy, not an answer from the core
  }

  if (!response.ok) {
    // The core answers 409 when the state machine says no and 400 when a reason is missing.
    // Both are answers, not failures, so carry the message through to the button that asked.
    const error = new Error(body?.detail || `${response.status} ${response.statusText}`);
    error.status = response.status;
    // A running core always answers JSON, even to say no. A 5xx with no JSON in it is the proxy
    // reporting that it found nothing to talk to — which is a different problem with a different fix.
    error.coreDown = !body && response.status >= 500;
    throw error;
  }
  return body;
}

export const api = {
  status: () => request("/status"),
  positions: () => request("/positions"),
  events: (afterId = 0, limit = 200) => request(`/events?after_id=${afterId}&limit=${limit}`),
  decisions: (limit = 100) => request(`/decisions?limit=${limit}`),
  orders: (limit = 100) => request(`/orders?limit=${limit}`),
  strategies: () => request("/strategies"),
  ticks: () => request("/ticks"),

  // Research is read-mostly: everything below reads, except startBacktest, which starts a job on
  // the core's own worker thread and answers immediately with something to poll.
  riskLimits: () => request("/risk/limits"),
  markets: () => request("/markets"),
  marketEnable: (body) => request("/markets/enable", { method: "POST", body: JSON.stringify(body) }),
  marketDisable: (body) => request("/markets/disable", { method: "POST", body: JSON.stringify(body) }),
  marketSync: (body) => request("/markets/sync", { method: "POST", body: JSON.stringify(body) }),
  marketAdd: (body) => request("/markets/add", { method: "POST", body: JSON.stringify(body) }),
  marketRemove: (body) => request("/markets/remove", { method: "POST", body: JSON.stringify(body) }),
  runs: (limit = 25, strategy) =>
    request(`/backtest/runs?limit=${limit}${strategy ? `&strategy=${encodeURIComponent(strategy)}` : ""}`),
  run: (id) => request(`/backtest/runs/${id}`),
  drift: (id, days = 30) => request(`/backtest/runs/${id}/drift?days=${days}`),
  backtestOptions: () => request("/backtest/options"),
  bars: ({ symbol, timeframe, start, end, limit = 400 }) => {
    // start/end are optional: without them the core returns the most recent `limit` bars.
    const q = new URLSearchParams({ symbol, timeframe, limit: String(limit) });
    if (start) q.set("start", start);
    if (end) q.set("end", end);
    return request(`/bars?${q}`);
  },
  jobs: () => request("/backtest/jobs"),
  job: (id) => request(`/backtest/jobs/${id}`),
  startBacktest: (body) => request("/backtest", { method: "POST", body: JSON.stringify(body) }),

  kill: (reason) => request("/control/kill", { method: "POST", body: JSON.stringify({ reason }) }),
  unlock: (reason) => request("/control/unlock", { method: "POST", body: JSON.stringify({ reason }) }),
  pause: (reason) => request("/control/pause", { method: "POST", body: JSON.stringify({ reason }) }),
  resume: () => request("/control/resume", { method: "POST" }),
};

export function eventSocket(onEvent, onState) {
  // Reconnects on its own. A UI that quietly stops updating is worse than one that says it is
  // disconnected, so connection state is reported rather than hidden.
  let socket = null;
  let closed = false;
  let retry = null;

  const connect = () => {
    if (closed) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${proto}://${window.location.host}/ws/events`);
    socket.onopen = () => onState?.("connected");
    socket.onmessage = (message) => {
      const payload = JSON.parse(message.data);
      if (payload.type === "event") onEvent(payload);
    };
    socket.onclose = () => {
      onState?.("disconnected");
      if (!closed) retry = setTimeout(connect, 2000);
    };
    socket.onerror = () => socket?.close();
  };

  connect();
  return () => {
    closed = true;
    clearTimeout(retry);
    socket?.close();
  };
}
