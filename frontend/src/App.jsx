import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { api, fmt, useBot, usePoll } from "./useBot.js";
import Cockpit from "./Cockpit.jsx";
const Agents = lazy(() => import("./Agents.jsx"));
const Research = lazy(() => import("./Research.jsx"));
const Controls = lazy(() => import("./Controls.jsx"));
const Pulse = lazy(() => import("./Pulse.jsx"));
import ChatBar from "./ChatBar.jsx";
import TradeCinema, { SoundToggle } from "./TradeCinema.jsx";
import { Sparkline } from "./charts.jsx";
import { PALETTES, applyPalette, currentPalette, flipInk } from "./skins.js";

const TABS = ["cockpit", "pulse", "agents", "research", "controls"];

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
  useEffect(() => {  // keys 1-5 switch tabs
    const onKey = (e) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "t" || e.key === "T") { flipInk(); return; }
      const i = Number(e.key) - 1;
      if (i >= 0 && i < TABS.length) setTab(TABS[i]);
    };
    const onTab = (e) => TABS.includes(e.detail) && setTab(e.detail);  // links between tabs
    window.addEventListener("keydown", onKey);
    window.addEventListener("tb-tab", onTab);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("tb-tab", onTab); };
  }, []);
  const openAgent = (id) => { setAgent(id); setTab("agents"); };
  const mood = moodOf(state?.wallet);
  useEffect(() => { document.documentElement.dataset.mood = mood; }, [mood]);

  if (!state) {
    return <div className="boot"><div className="boot-text">CONNECTING TO TRADINGBOTTY{connected ? "…" : " (is the bot running?)"}</div></div>;
  }
  return (
    <div className={`app ${state.mode === "live" ? "is-live" : ""}`}>
      {media?.background && <Backdrop src={media.background} />}
      <TopBar state={state} connected={connected} tab={tab} setTab={setTab} logo={media?.logo} />
      <Ticker items={state.ticker || []} onPick={(s) => { setFocus(s); setTab("pulse"); }} />
      <main>
        <Suspense fallback={<div className="empty" role="status">Arbeitsbereich wird geladen…</div>}>
        {tab === "cockpit" && <Cockpit state={state} pulse={pulse} focus={focus} setFocus={setFocus} openAgent={openAgent} />}
        {tab === "pulse" && <Pulse state={state} focus={focus} setFocus={setFocus} />}
        {tab === "agents" && <Agents state={state} avatars={media?.avatars || {}} want={agent} />}
        {tab === "research" && <Research state={state} openAgent={openAgent} />}
        {tab === "controls" && <Controls state={state} />}
        </Suspense>
      </main>
      <ChatBar />
      <TradeCinema trades={state.trades} currency={state.wallet?.currency || "CHF"} />
    </div>
  );
}

// The Aurora design in another palette. Remembered in this browser.
function PalettePick() {
  const [pal, setPal] = useState(currentPalette);
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const off = (e) => { if (!box.current?.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", off);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", off); document.removeEventListener("keydown", esc); };
  }, [open]);
  useEffect(() => {  // the T key changes the palette from outside the menu
    const on = () => setPal(currentPalette());
    window.addEventListener("tb-skin", on);
    return () => window.removeEventListener("tb-skin", on);
  }, []);
  const sw = (c) => ({ "--sw-a": c[0], "--sw-b": c[1], "--sw-c": c[2] });
  const cur = PALETTES.find(([k]) => k === pal) || PALETTES[0];
  return (
    <div className="pal" ref={box}>
      <button className="pal-btn" title="Farben" aria-haspopup="true" aria-expanded={open} onClick={() => setOpen(!open)}>
        <i className="pal-dot" style={sw(cur[2])} /><span className="pal-name">{cur[1]}</span>
      </button>
      {open && (
        <div className="pal-menu" role="menu">
          {PALETTES.map(([k, name, c, what]) => (
            <button key={k} role="menuitemradio" aria-checked={k === pal} className={k === pal ? "active" : ""} title={what}
              onClick={() => { setPal(k); applyPalette(k); }}>
              <i className="pal-dot" style={sw(c)} />{name}
            </button>
          ))}
          <small>{cur[3]} · Taste T wechselt hell/dunkel</small>
        </div>
      )}
    </div>
  );
}

// Is the account up or down today? The aurora skin tints its sky with it (data-mood on <html>).
function moodOf(w) {
  if (!w || w.error) return "flat";
  const day = w.change_24h ?? w.bot_edge;
  if (day == null || !w.total) return "flat";
  const pct = (day / w.total) * 100;
  return pct > 0.1 ? "up" : pct < -0.1 ? "down" : "flat";
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
      {logo ? <img className="logo-img" src={logo} alt="TradingBotty" /> : <div className="logo"><small>RALPH'S</small> TRADING<span>BOTTY</span></div>}
      <nav>
        {TABS.map((t, i) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)} title={`key ${i + 1}`}>{t}</button>
        ))}
      </nav>
      <div className="spacer" />
      {state.simulate && <span className="badge warn" title="Random-walk prices for testing">SIMULATED DATA</span>}
      <PalettePick />
      <SoundToggle />
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
