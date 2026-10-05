import { useEffect, useState } from "react";
import { api, fmt, useBot, usePoll } from "./useBot.js";
import Cockpit from "./Cockpit.jsx";
import Agents from "./Agents.jsx";
import Research from "./Research.jsx";
import Controls from "./Controls.jsx";
import { Sparkline } from "./charts.jsx";

const TABS = ["cockpit", "agents", "research", "controls"];

export default function App() {
  const { state, connected, pulse } = useBot();
  const [media] = usePoll("media", 0);
  const [tab, setTab] = useState(() => {
    try { const t = localStorage.getItem("tb-tab"); return TABS.includes(t) ? t : "cockpit"; } catch { return "cockpit"; }
  });
  const [focus, setFocus] = useState(() => {
    try { return localStorage.getItem("tb-focus") || null; } catch { return null; }
  });
  const [agent, setAgent] = useState(null);  // jump to one agent's inspector from anywhere
  useEffect(() => { try { localStorage.setItem("tb-tab", tab); } catch { /* private mode */ } }, [tab]);
  useEffect(() => { try { if (focus) localStorage.setItem("tb-focus", focus); } catch { /* private mode */ } }, [focus]);
  useEffect(() => {  // keys 1-4 switch tabs
    const onKey = (e) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
      const i = Number(e.key) - 1;
      if (i >= 0 && i < TABS.length) setTab(TABS[i]);
    };
    const onTab = (e) => TABS.includes(e.detail) && setTab(e.detail);  // links between tabs
    window.addEventListener("keydown", onKey);
    window.addEventListener("tb-tab", onTab);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("tb-tab", onTab); };
  }, []);
  const openAgent = (id) => { setAgent(id); setTab("agents"); };

  if (!state) {
    return <div className="boot"><div className="boot-text">CONNECTING TO TRADINGBOTTY{connected ? "…" : " (is the bot running?)"}</div></div>;
  }
  return (
    <div className={`app ${state.mode === "live" ? "is-live" : ""}`}>
      {media?.background && <Backdrop src={media.background} />}
      <TopBar state={state} connected={connected} tab={tab} setTab={setTab} logo={media?.logo} />
      <Ticker items={state.ticker || []} onPick={(s) => { setFocus(s); setTab("cockpit"); }} />
      <main>
        {tab === "cockpit" && <Cockpit state={state} pulse={pulse} focus={focus} setFocus={setFocus} openAgent={openAgent} />}
        {tab === "agents" && <Agents state={state} avatars={media?.avatars || {}} want={agent} />}
        {tab === "research" && <Research state={state} openAgent={openAgent} />}
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
  const live = state.mode === "live";
  const toggleMode = async () => {
    setBusy(true);
    try {
      if (live) {
        if (!window.confirm("Switch to STANDBY?\n\nNothing trades while on standby. The bot's coins stay where they are.")) return;
        await api("mode", { mode: "paper" });
      } else {
        const typed = window.prompt(
          "LIVE uses REAL MONEY on Bitpanda Fusion.\n\nThe Daily Brain buys and sells the coins its tested strategy picks, inside your limits (Controls).\nSpot only: it can lose money but can never put you in debt.\n\nType REAL MONEY to continue:");
        if (typed !== "REAL MONEY") return;
        const r = await api("mode", { mode: "live", confirm: typed });
        if (!r.ok) window.alert(r.error);
      }
    } catch (e) {
      window.alert(e.message);
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
      <div className="budget" title="AI spend: last 24h vs daily allowance, and total vs your hard cap (grows with 10% of the bot's gains)">
        <span>AI {state.ai ? "" : "(off) "}{fmt.usd(b.spent_today)}/{fmt.usd(b.cap_today)} today</span>
        <div className="bar"><div style={{ width: `${Math.min(100, (b.spent_total / (b.cap_total || 1)) * 100)}%` }} /></div>
        <span className="dim">{fmt.usd(b.spent_total)} of {fmt.usd(b.cap_total)} total</span>
      </div>
      <button className={`mode ${live ? "live" : "paper"}`} onClick={toggleMode} disabled={busy}
        title={live ? "Real money is trading. Click for standby." : "Standby: nothing trades. Click to go LIVE."}>
        {live ? "● LIVE" : "STANDBY"}
      </button>
      <button className={`kill ${state.kill_switch ? "on" : ""}`} onClick={kill} title="Stops all decisions and buys immediately">
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
