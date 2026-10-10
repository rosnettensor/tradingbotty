import { useEffect, useRef, useState } from "react";
import { useMotion } from "./motion.js";
import "./signalRibbon.css";

const clean = (value, limit = 180) => String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
const stamp = ts => new Date(ts * 1000).toLocaleString("de-CH", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const recent = (ts, now, age) => Number.isFinite(ts) && ts <= now + 10 && now - ts <= age;
const signals = { "would buy": "Kaufsignal in Prüfung", "would sell": "Verkaufssignal in Prüfung", "near breakout": "Nahe am Ausbruch", "breakout, no slot": "Ausbruch · kein freier Platz" };

// Uses the existing read-only feed. A preview is never presented as an executed order.
function feedItems(state, mission, now) {
  const items = [{ id: "system", tag: "SYSTEM", text: mission.title, meta: mission.fresh ? "Serverstatus" : "Status unbestätigt", tone: mission.fresh ? "neutral" : "warning", section: "operation" }];
  if (state.simulate) items.unshift({ id: "simulation", tag: "TESTDATEN", text: "Simulierte Marktdaten aktiv", tone: "warning", section: "operation" });
  if (mission.fresh) {
    Object.entries(state.guard || {}).filter(([, g]) => g.until > now).slice(0, 3).forEach(([symbol, g]) => items.push({ id: `guard-${symbol}`, tag: "GUARDIAN", text: `${symbol} · Käufe gesperrt`, meta: clean(g.reason, 60), tone: "warning", symbol, section: "signals" }));
    if (recent(state.trend?.ts, now, 900)) (state.trend.rows || []).filter(r => signals[r.state]).slice(0, 4).forEach(r => items.push({ id: `signal-${r.symbol}`, tag: "BEOBACHTET", text: `${r.symbol} · ${signals[r.state]}`, meta: `Vorschau · ${stamp(state.trend.ts)}`, tone: "signal", symbol: r.symbol, section: "signals" }));
  }
  (state.trades || []).filter(t => t.mode === "live" && ["BUY", "SELL"].includes(t.side) && recent(t.ts, now, 86400)).slice(0, 3).forEach((t, i) => items.push({ id: `trade-${t.ts}-${i}`, tag: "AUSGEFÜHRT", text: `${t.side === "BUY" ? "Kauf" : "Verkauf"} ${clean(t.symbol, 16)}${Number.isFinite(t.notional) ? ` · ${t.notional.toFixed(2)} ${state.wallet?.currency || ""}` : ""}`, meta: stamp(t.ts), tone: "trade", symbol: t.symbol, section: "positions" }));
  [...(state.news || [])].filter(n => n.title && recent(n.ts, now, 86400)).sort((a, b) => b.ts - a.ts).slice(0, 5).forEach((n, i) => items.push({ id: `news-${n.ts}-${i}`, tag: "NEWS", text: clean(n.title), meta: `${clean(n.source, 35) || "Newsfeed"} · erfasst ${stamp(n.ts)}`, tone: "news", section: "signals" }));
  if (items.length === 1) items.push({ id: "quiet", tag: "FEED", text: "Keine neuen bestätigten Trades oder aktuellen Meldungen", meta: "Letzte 24 Stunden", tone: "neutral", section: "signals" });
  return items;
}

export default function SignalRibbon({ state, mission, now, onOpen }) {
  const { reduced } = useMotion();
  const [paused, setPaused] = useState(() => { try { return localStorage.getItem("tb-ribbon-paused") === "1"; } catch { return false; } });
  const [expanded, setExpanded] = useState(false);
  const [hidden, setHidden] = useState(document.hidden);
  const [duration, setDuration] = useState(80);
  const group = useRef(null), viewport = useRef(null), toggle = useRef(null), root = useRef(null);
  const items = feedItems(state, mission, now);
  const stationary = paused || reduced || expanded;
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setDuration(Math.max(25, entry.contentRect.width / 42)));
    if (group.current) observer.observe(group.current);
    const visibility = () => setHidden(document.hidden);
    document.addEventListener("visibilitychange", visibility);
    return () => { observer.disconnect(); document.removeEventListener("visibilitychange", visibility); };
  }, []);
  useEffect(() => {
    if (stationary && viewport.current) viewport.current.scrollLeft = 0;
  }, [stationary]);
  useEffect(() => {
    if (!expanded) return;
    const close = e => { if (e.key === "Escape") { e.stopPropagation(); setExpanded(false); toggle.current?.focus(); } };
    const outside = e => { if (!root.current?.contains(e.target)) setExpanded(false); };
    document.addEventListener("keydown", close); document.addEventListener("pointerdown", outside);
    return () => { document.removeEventListener("keydown", close); document.removeEventListener("pointerdown", outside); };
  }, [expanded]);
  const pick = item => { setExpanded(false); onOpen(item.section, item.symbol); };
  const pause = () => setPaused(p => { try { localStorage.setItem("tb-ribbon-paused", p ? "0" : "1"); } catch { /* private mode */ } return !p; });
  const content = item => <><span className={`ribbon-tag ${item.tone}`}>{item.tag}</span><span className="ribbon-copy">{item.text}</span><small>{item.meta}</small><span className="ribbon-arrow" aria-hidden>↗</span></>;
  return <aside ref={root} className={`signal-ribbon ${stationary ? "is-still" : ""} ${hidden ? "is-hidden" : ""}`} aria-label="Signal- und Nachrichtenband">
    {expanded && <section className="ribbon-panel" id="ribbon-details" aria-label="Aktuelle Meldungen">
      <header><div><span>BOTTY / SIGNAL FEED</span><h3>Was gerade relevant ist</h3></div><button onClick={() => { setExpanded(false); toggle.current?.focus(); }} aria-label="Meldungen schließen">×</button></header>
      <div className="ribbon-list">{items.map(item => <button key={item.id} onClick={() => pick(item)}>{content(item)}</button>)}</div>
      <footer>Beobachtete Signale sind noch keine Orders. News zeigen den Erfassungszeitpunkt.</footer>
    </section>}
    <button ref={toggle} className={`ribbon-label ${mission.fresh ? "" : "disconnected"}`} onClick={() => setExpanded(v => !v)} aria-expanded={expanded} aria-controls="ribbon-details" aria-label={expanded ? "Meldungsliste schließen" : "Meldungsliste öffnen"}>
      <i aria-hidden /><span>{state.simulate ? "TEST-FEED" : mission.fresh ? "SIGNAL FEED" : "VERBINDUNG?"}</span><b aria-hidden>{expanded ? "−" : "+"}</b>
    </button>
    <div className="ribbon-viewport" ref={viewport}>
      <div className="ribbon-track" style={{ "--ribbon-duration": `${duration}s` }}>
        <div className="ribbon-group" ref={group}>{items.map(item => <button className="ribbon-item" key={item.id} onClick={() => pick(item)} title={`${item.text} · ${item.meta || "Details öffnen"}`}>{content(item)}</button>)}</div>
        <div className="ribbon-group ribbon-duplicate" aria-hidden="true" inert>{items.map(item => <span className="ribbon-item" key={item.id}>{content(item)}</span>)}</div>
      </div>
    </div>
    <button className="ribbon-pause" onClick={pause} disabled={reduced} aria-pressed={paused || reduced} aria-label={reduced ? "Bewegung durch Ruhemodus ausgeschaltet" : paused ? "Signalband starten" : "Signalband pausieren"} title={reduced ? "Ruhemodus aktiv" : paused ? "Starten" : "Pausieren"}>
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden>{paused || reduced ? <path d="M5 3 13 8 5 13Z" fill="currentColor" /> : <path d="M5 3v10M11 3v10" stroke="currentColor" strokeWidth="2" />}</svg>
    </button>
  </aside>;
}
