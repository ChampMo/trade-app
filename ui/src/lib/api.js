// Everything the UI knows about the core goes through here (D7).
// The UI has no MT5 client, no broker credentials and no way to place an order — it can only ask
// the core, which is what keeps the trading rules in one auditable place.

const BASE = "/api";

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    // The core answers 409 when the state machine says no and 400 when a reason is missing.
    // Both are answers, not failures, so carry the message through to the button that asked.
    const error = new Error(body?.detail || `${response.status} ${response.statusText}`);
    error.status = response.status;
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
