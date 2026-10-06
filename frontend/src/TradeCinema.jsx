import { useEffect, useRef, useState } from "react";

// Trade cinema: a short full-screen moment when the bot makes a NEW real trade.
// Trades that were already there when the page loaded never replay. Tap, click or Esc closes it.
// Sound is a tiny synthesized whoosh + chime (no audio files), off until you switch it on (🔇/🔊 in the top bar).

const SHOW_MS = 3500;
const SOUND_KEY = "tb-sound";

// ------------------------------------------------------------------ sound
let ctx = null;
function audio() {
  if (ctx) return ctx;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  try { ctx = new AC(); } catch { ctx = null; }
  return ctx;
}

function soundOn() {
  try { return localStorage.getItem(SOUND_KEY) === "on"; } catch { return false; }
}

function play(side) {
  const ac = audio();
  if (!ac) return;
  if (ac.state === "suspended") ac.resume().catch(() => {});
  const t0 = ac.currentTime + 0.02;
  const out = ac.createGain();
  out.gain.value = 0.5;
  out.connect(ac.destination);
  // whoosh: half a second of noise swept through a band-pass filter
  const len = Math.floor(ac.sampleRate * 0.7);
  const buf = ac.createBuffer(1, len, ac.sampleRate);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < len; i++) ch[i] = Math.random() * 2 - 1;
  const noise = ac.createBufferSource();
  noise.buffer = buf;
  const bp = ac.createBiquadFilter();
  bp.type = "bandpass"; bp.Q.value = 1.4;
  bp.frequency.setValueAtTime(320, t0);
  bp.frequency.exponentialRampToValueAtTime(2600, t0 + 0.55);
  const ng = ac.createGain();
  ng.gain.setValueAtTime(0.0001, t0);
  ng.gain.exponentialRampToValueAtTime(0.35, t0 + 0.18);
  ng.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.65);
  noise.connect(bp).connect(ng).connect(out);
  noise.start(t0); noise.stop(t0 + 0.7);
  // chime: two soft bells, rising for a buy, falling for a sell
  const notes = side === "BUY" ? [659.25, 987.77] : [987.77, 659.25];
  notes.forEach((f, i) => {
    const s = t0 + 0.32 + i * 0.13;
    const o = ac.createOscillator();
    o.type = "sine"; o.frequency.value = f;
    const o2 = ac.createOscillator();  // a quiet octave on top makes it ring
    o2.type = "sine"; o2.frequency.value = f * 2;
    const g = ac.createGain();
    g.gain.setValueAtTime(0.0001, s);
    g.gain.exponentialRampToValueAtTime(0.22, s + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, s + 1.1);
    const g2 = ac.createGain();
    g2.gain.value = 0.18;
    o.connect(g); o2.connect(g2).connect(g); g.connect(out);
    o.start(s); o2.start(s); o.stop(s + 1.15); o2.stop(s + 1.15);
  });
}

/** 🔇/🔊 for the top bar. Switching on happens in a tap, which also unlocks audio on iPhones. */
export function SoundToggle() {
  const [on, setOn] = useState(soundOn);
  useEffect(() => {
    const sync = () => setOn(soundOn());
    // browsers only allow sound after a tap: wake the audio on the first one if sound was left on
    const unlock = () => { if (soundOn()) audio()?.resume?.().catch(() => {}); };
    window.addEventListener("tb-sound", sync);
    window.addEventListener("pointerdown", unlock, { once: true });
    return () => { window.removeEventListener("tb-sound", sync); window.removeEventListener("pointerdown", unlock); };
  }, []);
  const flip = () => {
    const next = !on;
    try { localStorage.setItem(SOUND_KEY, next ? "on" : "off"); } catch { /* private mode */ }
    if (next) { const ac = audio(); ac?.resume?.().catch(() => {}); }
    setOn(next);
    window.dispatchEvent(new Event("tb-sound"));
  };
  return (
    <button className={`sound-toggle ${on ? "on" : ""}`} onClick={flip} aria-pressed={on}
      title={on ? "Trade sound on: click to mute" : "Trade sound off: click to hear a chime on every real trade"}>
      {on ? "🔊" : "🔇"}
    </button>
  );
}

/** Show the animation with a made-up trade (Controls), clearly marked as a demo. */
export function previewTrade(side = "BUY", symbol = "BTC") {
  window.dispatchEvent(new CustomEvent("tb-trade-demo", {
    detail: { demo: true, side, symbol, notional: side === "BUY" ? 250 : 268.4, pnl: side === "SELL" ? 17.62 : undefined,
      book: "brain", reason: "demo: nothing was traded", ts: Date.now() / 1000 },
  }));
}

// the gain or loss of a sale after fees, if the bot sent one (the trade feed has no such field today)
const pnlOf = (t) => [t.pnl, t.realized, t.pnl_after_fees, t.gain].find((x) => typeof x === "number" && Number.isFinite(x));

// ------------------------------------------------------------------ overlay
export default function TradeCinema({ trades, currency = "CHF" }) {
  const seen = useRef(null);       // newest trade time already on screen when the page loaded
  const [queue, setQueue] = useState([]);
  const [leaving, setLeaving] = useState(false);
  const cur = queue[0];

  useEffect(() => {
    const list = trades || [];
    const newest = list.reduce((m, t) => Math.max(m, Number(t.ts) || 0), 0);
    if (seen.current === null) { seen.current = newest; return; }
    const fresh = list.filter((t) => Number(t.ts) > seen.current);
    if (!fresh.length) return;
    seen.current = newest;
    setQueue((q) => [...q, ...fresh.sort((a, b) => a.ts - b.ts)].slice(-4));
  }, [trades]);

  useEffect(() => {
    const onDemo = (e) => setQueue((q) => [...q, e.detail].slice(-4));
    window.addEventListener("tb-trade-demo", onDemo);
    return () => window.removeEventListener("tb-trade-demo", onDemo);
  }, []);

  const close = () => setLeaving(true);

  useEffect(() => {
    if (!cur) return undefined;
    setLeaving(false);
    if (soundOn()) { try { play(cur.side); } catch { /* no audio */ } }
    const t = setTimeout(() => setLeaving(true), SHOW_MS - 400);
    const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); setLeaving(true); } };
    window.addEventListener("keydown", onKey);
    return () => { clearTimeout(t); window.removeEventListener("keydown", onKey); };
  }, [cur]);

  useEffect(() => {
    if (!leaving) return undefined;
    const t = setTimeout(() => { setLeaving(false); setQueue((q) => q.slice(1)); }, 380);
    return () => clearTimeout(t);
  }, [leaving]);

  if (!cur) return null;
  const buy = cur.side === "BUY";
  const pnl = buy ? undefined : pnlOf(cur);
  const amount = Number(cur.notional || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (
    <div className={`tc ${buy ? "buy" : "sell"} ${leaving ? "out" : ""}`} onClick={close} role="alert"
      aria-label={`${cur.demo ? "Demo: " : "Real trade: "}${cur.side} ${cur.symbol} ${amount} ${currency}`} key={`${cur.ts}-${cur.symbol}`}>
      <div className="tc-flash" />
      <div className="tc-rings" aria-hidden>
        <i /><i /><i /><i />
      </div>
      <div className="tc-card">
        <div className="tc-kicker">{cur.demo ? "DEMO · nothing traded" : cur.book === "fast" ? "⚡ FAST POT · REAL TRADE" : "DAILY BRAIN · REAL TRADE"}</div>
        <div className="tc-side">{buy ? "BUY" : "SELL"}</div>
        <div className="tc-sym">{cur.symbol}</div>
        <div className="tc-amt">{amount} <small>{currency}</small></div>
        {pnl !== undefined && (
          <div className={`tc-pnl ${pnl >= 0 ? "up" : "down"}`}>
            {pnl >= 0 ? "+" : "−"}{Math.abs(pnl).toFixed(2)} {currency} <small>{pnl >= 0 ? "gain" : "loss"} after fees</small>
          </div>
        )}
        {cur.reason && !cur.demo && <div className="tc-why">{cur.reason}</div>}
        <div className="tc-hint">tap or Esc to close</div>
      </div>
    </div>
  );
}
