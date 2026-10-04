import { useEffect, useState } from "react";
import { api, fmt, useBot, usePoll } from "./useBot.js";
import Cockpit from "./Cockpit.jsx";
import Markets from "./Markets.jsx";
import Radar from "./Radar.jsx";
import Agents from "./Agents.jsx";
import Lab from "./Lab.jsx";
import Controls from "./Controls.jsx";
import Insights from "./Insights.jsx";
import { Sparkline } from "./charts.jsx";

const TABS = ["cockpit", "insights", "radar", "markets", "agents", "lab", "controls"];

export default function App() {
  const { state, connected, pulse } = useBot();
  const [media] = usePoll("media", 0);
  const [tab, setTab] = useState(() => {
    try { return localStorage.getItem("tb-tab") || "cockpit"; } catch { return "cockpit"; }
  });
  const [focus, setFocus] = useState(() => {
    try { return localStorage.getItem("tb-focus") || null; } catch { return null; }
  });
  useEffect(() => { try { localStorage.setItem("tb-tab", tab); } catch { /* private mode */ } }, [tab]);
  useEffect(() => { try { if (focus) localStorage.setItem("tb-focus", focus); } catch { /* private mode */ } }, [focus]);
  useEffect(() => {  // keys 1-7 switch tabs
    const onKey = (e) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
      const i = Number(e.key) - 1;
      if (i >= 0 && i < TABS.length) setTab(TABS[i]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!state) {
    return <div className="boot"><div className="boot-text">CONNECTING TO TRADINGBOTTY{connected ? "…" : " (is run.py running?)"}</div></div>;
  }
  return (
    <div className={`app ${state.mode === "live" ? "is-live" : ""}`}>
      {media?.background && <Backdrop src={media.background} />}
      <TopBar state={state} connected={connected} tab={tab} setTab={setTab} logo={media?.logo} />
      <Ticker items={state.ticker || []} onPick={(s) => { setFocus(s); if (tab !== "markets") setTab("cockpit"); }} />
      <main>
        {tab === "cockpit" && <Cockpit state={state} pulse={pulse} focus={focus} setFocus={setFocus} />}
        {tab === "insights" && <Insights state={state} setFocus={setFocus} setTab={setTab} />}
        {tab === "radar" && <Radar state={state} setFocus={setFocus} setTab={setTab} />}
        {tab === "markets" && <Markets state={state} focus={focus} setFocus={setFocus} />}
        {tab === "agents" && <Agents nodes={state.nodes || []} avatars={media?.avatars || {}} />}
        {tab === "lab" && <Lab state={state} />}
        {tab === "controls" && <Controls state={state} />}
      </main>
    </div>
  );
}

function Backdrop({ src }) {
  const video = /\.(mp4|webm)$/i.test(src);
  return (
    <div className="backdrop" aria-hidden>
      {video ? <video src={src} autoPlay loop muted playsInline /> : <img src={src} alt="" />}
    </div>
  );
}

function TopBar({ state, connected, tab, setTab, logo }) {
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
      {logo ? <img className="logo-img" src={logo} alt="TradingBotty" /> : <div className="logo">TRADING<span>BOTTY</span></div>}
      <nav>
        {TABS.map((t, i) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)} title={`key ${i + 1}`}>{t}</button>
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

function Ticker({ items, onPick }) {
  if (!items.length) return <div className="ticker" />;
  const row = items.map((q) => (
    <span className="tick" key={q.symbol} onClick={() => onPick(q.symbol)}>
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
