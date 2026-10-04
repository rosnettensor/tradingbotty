import { useEffect, useRef, useState } from "react";

// One live connection to the bot. Keeps the full dashboard state up to date.
export function useBot() {
  const [state, setState] = useState(null);
  const [connected, setConnected] = useState(false);
  const [pulse, setPulse] = useState(0); // bumps on every trade, the sphere reacts to it
  const wsRef = useRef(null);

  useEffect(() => {
    let stop = false;
    let retry;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws`);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!stop) retry = setTimeout(connect, 2000);
      };
      ws.onmessage = (ev) => {
        const { kind, data } = JSON.parse(ev.data);
        setState((s) => apply(s, kind, data));
        if (kind === "trade") setPulse((p) => p + 1);
      };
    };
    connect();
    return () => {
      stop = true;
      clearTimeout(retry);
      wsRef.current?.close();
    };
  }, []);

  return { state, connected, pulse };
}

function apply(s, kind, data) {
  if (kind === "hello") return data;
  if (!s) return s;
  switch (kind) {
    case "state":
      return { ...s, ...data };
    case "prices":
      return { ...s, ticker: data };
    case "node":
      return { ...s, nodes: (s.nodes || []).map((n) => (n.id === data.id ? data : n)) };
    case "log":
      return { ...s, log: [...(s.log || []).slice(-299), data] };
    case "trade":
      return { ...s, trades: [data, ...(s.trades || [])].slice(0, 100) };
    default:
      return s;
  }
}

export async function api(path, body) {
  const res = await fetch(`/api/${path}`, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.detail || res.statusText);
  return json;
}

export const fmt = {
  usd: (x) => (x == null ? "–" : `$${Number(x).toFixed(2)}`),
  pct: (x) => (x == null ? "–" : `${x >= 0 ? "+" : ""}${Number(x).toFixed(2)}%`),
  price: (x) => (x == null ? "–" : x >= 100 ? x.toFixed(2) : x >= 1 ? x.toFixed(4) : x.toPrecision(4)),
  time: (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
};

// Fetch a GET endpoint now and every `every` ms. Returns [data, reload].
export function usePoll(path, every = 30000, deps = []) {
  const [data, setData] = useState(null);
  const load = () => path && api(path).then(setData).catch(() => {});
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const run = () => api(path).then((d) => alive && setData(d)).catch(() => {});
    run();
    const t = every ? setInterval(run, every) : null;
    return () => { alive = false; if (t) clearInterval(t); };
  }, [path, ...deps]);
  return [data, load];
}

export const pctColor = (x) => (x > 0 ? "up" : x < 0 ? "down" : "dim");
export const ago = (ts) => {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return `${Math.max(0, Math.round(s))}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};
