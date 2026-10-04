import { useEffect, useState } from "react";
import { api, fmt, useBot } from "./useBot.js";
import Cockpit from "./Cockpit.jsx";
import Nodes from "./Nodes.jsx";
import Experiments from "./Experiments.jsx";
import { Sparkline } from "./charts.jsx";

const TABS = ["cockpit", "nodes", "experiments"];

export default function App() {
  const { state, connected, pulse } = useBot();
  const [tab, setTab] = useState(() => {
    try { return localStorage.getItem("tb-tab") || "cockpit"; } catch { return "cockpit"; }
  });
  useEffect(() => { try { localStorage.setItem("tb-tab", tab); } catch { /* private mode */ } }, [tab]);

  if (!state) {
    return <div className="boot"><div className="boot-text">CONNECTING TO TRADINGBOTTY{connected ? "…" : " (is run.py running?)"}</div></div>;
  }
  return (
    <div className={`app ${state.mode === "live" ? "is-live" : ""}`}>
      <TopBar state={state} connected={connected} tab={tab} setTab={setTab} />
      <Ticker items={state.ticker || []} />
      <main>
        {tab === "cockpit" && <Cockpit state={state} pulse={pulse} />}
        {tab === "nodes" && <Nodes nodes={state.nodes || []} />}
        {tab === "experiments" && <Experiments state={state} />}
      </main>
    </div>
  );
}

function TopBar({ state, connected, tab, setTab }) {
  const [busy, setBusy] = useState(false);
  const b = state.budget || {};
  const toggleMode = async () => {
    setBusy(true);
    try {
      if (state.mode === "live") {
        await api("mode", { mode: "paper" });
      } else {
        const typed = prompt(
          "LIVE TRADING uses REAL MONEY on Bitpanda.\n\nThe champion strategy's trades will be copied to your account.\nSpot only: it can lose money but can never put you in debt.\n\nType REAL MONEY to continue:");
        if (typed !== "REAL MONEY") return;
        const r = await api("mode", { mode: "live", confirm: typed });
        if (!r.ok) alert(r.error);
      }
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  };
  const kill = () => api("kill", { on: !state.kill_switch });
  return (
    <header className="topbar">
      <div className="logo">TRADING<span>BOTTY</span></div>
      <nav>
        {TABS.map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>
        ))}
      </nav>
      <div className="spacer" />
      {state.simulate && <span className="badge warn" title="Random-walk prices for testing">SIMULATED DATA</span>}
      <span className={`badge ${connected ? "ok" : "bad"}`}>{connected ? "● LINK" : "○ OFFLINE"}</span>
      <div className="budget" title="AI spend: last 24h vs daily allowance, and total vs your hard cap">
        <span>AI {state.ai ? "" : "(off) "}{fmt.usd(b.spent_today)}/{fmt.usd(b.cap_today)} today</span>
        <div className="bar"><div style={{ width: `${Math.min(100, (b.spent_total / (b.cap_total || 1)) * 100)}%` }} /></div>
        <span className="dim">{fmt.usd(b.spent_total)} of {fmt.usd(b.cap_total)} total</span>
      </div>
      <button className={`mode ${state.mode}`} onClick={toggleMode} disabled={busy}>
        {state.mode === "live" ? "● LIVE" : "PAPER"}
      </button>
      <button className={`kill ${state.kill_switch ? "on" : ""}`} onClick={kill} title="Stops all new buys immediately">
        {state.kill_switch ? "KILL SWITCH ON" : "KILL"}
      </button>
    </header>
  );
}

function Ticker({ items }) {
  if (!items.length) return <div className="ticker" />;
  const row = items.map((q) => (
    <span className="tick" key={q.symbol}>
      <b>{q.symbol}</b> {fmt.price(q.price)}
      <i className={q.change >= 0 ? "up" : "down"}>{q.change >= 0 ? "▲" : "▼"} {Math.abs(q.change).toFixed(2)}%</i>
      <Sparkline values={q.spark} />
    </span>
  ));
  return (
    <div className="ticker">
      <div className="ticker-track">{row}{row}</div>
    </div>
  );
}
