import { useEffect, useMemo, useRef, useState } from "react";
import { DailyChart } from "./charts.jsx";
import { Tabs } from "./components.jsx";
import { fmt, pctColor, usePoll } from "./useBot.js";

/* PULSE: the whole market the bot watches, drawn several ways. Every picture is real data from the daily candles
   (plus Fusion's live price); each one also shows one of the ideas the bot trades on. */

const PERIODS = [["1d", "24h"], ["7d", "7T"], ["30d", "30T"], ["90d", "90T"], ["1y", "1J"]];
const MOOD = { bull: "var(--green)", bear: "var(--red)", sideways: "var(--dim)", wild: "var(--amber)" };
const MOOD_DE = { bull: "Bull", bear: "Bear", sideways: "Seitwärts", wild: "Wild" };

function useWidth() {
  const box = useRef(null);
  const [W, setW] = useState(600);
  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(200, e.contentRect.width)));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);
  return [box, W];
}

// a change in percent as a tile color: green up, red down, stronger the bigger the move (scale = a "big" move)
const heat = (x, scale) => {
  if (x == null) return "var(--faint)";
  const k = Math.min(1, Math.abs(x) / scale);
  return `color-mix(in srgb, ${x >= 0 ? "var(--green)" : "var(--red)"} ${Math.round(8 + k * 52)}%, transparent)`;
};
const SCALE = { "1d": 6, "7d": 15, "30d": 30, "90d": 60, "1y": 150 };

export default function Pulse({ state, focus, setFocus }) {
  const [d] = usePoll("pulse", 60000);
  const [per, setPer] = useState(() => { try { return localStorage.getItem("tb-pulse-per") || "7d"; } catch { return "7d"; } });
  useEffect(() => { try { localStorage.setItem("tb-pulse-per", per); } catch { /* private mode */ } }, [per]);
  const rows = d?.coins || [];
  const sel = rows.find((r) => r.symbol === focus) || rows.find((r) => r.symbol === "BTC") || rows[0];
  const tags = useMemo(() => {
    const t = {};
    for (const s of d?.held?.brain || []) t[s] = [...(t[s] || []), (d.owners || {})[s] && (d.owners || {})[s] === state.brain?.probe ? "probe" : "brain"];
    for (const s of d?.held?.fast || []) t[s] = [...(t[s] || []), "fast"];
    for (const s of d?.guard || []) t[s] = [...(t[s] || []), "guard"];
    return t;
  }, [d, state.brain?.probe]);
  if (!d) return <div className="pulse"><section className="panel"><div className="empty">lade den Markt… (beim ersten Mal lädt der Bot die Tageskerzen, das dauert kurz)</div></section></div>;
  const s = d.summary;
  return (
    <div className="pulse">
      <section className="panel pulse-head">
        <div className="pulse-title">
          <h3>PULSE <span className="dim">· {s.coins} Coins, die der Bot beobachtet · Tageskerzen bis {new Date(d.day * 1000).toLocaleDateString("de-CH")} + Live-Kurs</span></h3>
          <Tabs value={per} options={PERIODS} onChange={setPer} />
        </div>
        <div className="pulse-stats">
          <Gauge label="heute im Plus" n={s.up_1d} of={s.coins} />
          <Gauge label="über 50-Tage-Schnitt" n={s.above50} of={s.coins} />
          <Gauge label="nahe am Ausbruch" n={s.near_high} of={s.coins} hint="weniger als 3% unter dem 20-Tage-Hoch" />
          <div className="pulse-stat"><b>{s.avg_corr.toFixed(2)}</b><span>Gleichlauf mit Bitcoin</span><i className="dim">0 = eigene Wege, 1 = alles folgt BTC</i></div>
        </div>
      </section>

      <section className="panel pulse-map">
        <h3>MARKT-KARTE <span className="dim">· Farbe = Bewegung {PERIODS.find(([k]) => k === per)?.[1]} · tippen für Details</span></h3>
        <HeatGrid rows={rows} per={per} sel={sel?.symbol} onPick={setFocus} tags={tags} />
        <div className="pulse-legend dim small"><span>🧠 Daily Brain</span><span>🧪 Probe</span><span>⚡ Fast Pot</span><span>⛔ Guardian sperrt</span></div>
      </section>

      <section className="panel pulse-detail">{sel && <CoinDetail r={sel} state={state} tags={tags[sel.symbol] || []} />}</section>

      <section className="panel pulse-radar">
        <h3>AUSBRUCH-RADAR <span className="dim">· so sieht der Daily Brain den Markt</span></h3>
        <Radar rows={rows} sel={sel?.symbol} onPick={setFocus} />
        <p className="dim small">Mitte = 20-Tage-Hoch. Ein Coin im leuchtenden Kreis schliesst darüber: Kaufsignal, solange Bitcoin über seinem 50-Tage-Schnitt liegt. Je weiter aussen, desto weiter weg. Punktgrösse = Schwankung, Farbe = 7 Tage.</p>
      </section>

      <section className="panel pulse-scatter">
        <h3>RISIKO GEGEN ERTRAG <span className="dim">· letzte 30 Tage</span></h3>
        <Scatter rows={rows} sel={sel?.symbol} onPick={setFocus} />
        <p className="dim small">Rechts = wilder, oben = mehr Gewinn. Oben links ist der Traum: stark gestiegen bei ruhigem Kurs. Der Pattern Hunter fand: sehr wilde Coins haben im Schnitt schlechtere Wochen.</p>
      </section>

      <section className="panel pulse-race">
        <h3>DAS RENNEN <span className="dim">· 90 Tage, alle bei 100 gestartet</span></h3>
        <Race rows={rows} sel={sel?.symbol} onPick={setFocus} />
        <p className="dim small">Breakout-Strategien leben davon, dass die Führenden weiter führen. Hervorgehoben: Bitcoin, die 3 besten und die 3 schwächsten.</p>
      </section>

      <section className="panel pulse-breadth">
        <h3>MARKTBREITE <span className="dim">· 1 Jahr · die Grundlage des Regime Radars</span></h3>
        <Breadth data={d.breadth} />
        <p className="dim small">Fläche = Anteil der Coins über ihrem 50-Tage-Schnitt. Linie = Anteil auf einem 20-Tage-Hoch (Ausbrüche). Weiss = Bitcoin. Das Band unten zeigt die Marktlage jedes Tages.</p>
      </section>

      <section className="panel pulse-corr">
        <h3>GLEICHLAUF <span className="dim">· Korrelation der Tagesbewegungen, {d.matrix.days} Tage</span></h3>
        <Matrix m={d.matrix} sel={sel?.symbol} onPick={setFocus} />
        <p className="dim small">Hell = bewegen sich gemeinsam. Drei Coins, die alle gleich laufen, sind in Wahrheit eine Wette. Ähnliche Coins stehen nebeneinander.</p>
      </section>
    </div>
  );
}

function Gauge({ label, n, of, hint }) {
  const f = of ? n / of : 0;
  return (
    <div className="pulse-stat" title={hint || ""}>
      <b>{n}<small>/{of}</small></b><span>{label}</span>
      <i className="pulse-bar"><em style={{ width: `${f * 100}%` }} /></i>
    </div>
  );
}

function Badges({ tags }) {
  const icon = { brain: "🧠", probe: "🧪", fast: "⚡", guard: "⛔" };
  return tags.length ? <span className="pulse-badges">{tags.map((t) => <i key={t} title={t}>{icon[t]}</i>)}</span> : null;
}

function HeatGrid({ rows, per, sel, onPick, tags }) {
  const sorted = [...rows].sort((a, b) => (b.moves[per] ?? -1e9) - (a.moves[per] ?? -1e9));
  return (
    <div className="heat-grid">
      {sorted.map((r) => {
        const x = r.moves[per];
        return (
          <button key={r.symbol} className={`heat ${r.symbol === sel ? "sel" : ""}`} style={{ background: heat(x, SCALE[per]) }}
            onClick={() => onPick(r.symbol)}>
            <span className="heat-top"><b>{r.symbol}</b><Badges tags={tags[r.symbol] || []} /></span>
            <span className={`heat-chg ${pctColor(x)}`}>{x == null ? "–" : `${x >= 0 ? "+" : ""}${x.toFixed(x > -10 && x < 10 ? 1 : 0)}%`}</span>
            <span className="heat-px">{fmt.price(r.price)}{r.live && <i className="live-dot" title="Live-Kurs von Fusion" />}</span>
            <MiniLine values={r.spark} />
          </button>
        );
      })}
    </div>
  );
}

function MiniLine({ values }) {
  if (!values || values.length < 2) return null;
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 100},${30 - ((v - min) / span) * 28 - 1}`).join(" ");
  return (
    <svg className="heat-line" viewBox="0 0 100 30" preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke="currentColor" strokeWidth="1.4" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function CoinDetail({ r, state, tags }) {
  const [d] = usePoll(`daily/${r.symbol}?days=120`, 120000, [r.symbol]);
  const f = (x, digits = 1) => (x == null ? "–" : `${x >= 0 ? "+" : ""}${x.toFixed(digits)}%`);
  return (
    <>
      <div className="row-head">
        <h3>{r.symbol} <span className="hi-num">{fmt.price(r.price)}</span> <span className={pctColor(r.moves["1d"])}>{f(r.moves["1d"], 2)} 24h</span> <Badges tags={tags} /></h3>
        <span className="dim small">{r.live ? "Live-Kurs Fusion" : "letzter Tagesschluss"}</span>
      </div>
      <div className="coin-facts">
        {PERIODS.map(([k, label]) => <div key={k}><span>{label}</span><b className={pctColor(r.moves[k])}>{f(r.moves[k])}</b></div>)}
        <div><span>bis Kauflinie</span><b className={r.to_high >= 0 ? "up" : ""}>{r.to_high >= 0 ? "darüber" : f(r.to_high)}</b></div>
        <div><span>bis Verkaufslinie</span><b className={r.to_low <= 0 ? "down" : ""}>{r.to_low <= 0 ? "darunter" : f(-r.to_low)}</b></div>
        <div><span>Schwankung/Jahr</span><b>{r.vol == null ? "–" : `${r.vol.toFixed(0)}%`}</b></div>
        <div><span>vom Allzeithoch</span><b className="down">{f(r.from_ath, 0)}</b></div>
        <div><span>Gleichlauf BTC</span><b>{r.btc_corr == null ? "–" : r.btc_corr.toFixed(2)}</b></div>
        <div><span>50-Tage-Schnitt</span><b className={r.above50 ? "up" : "down"}>{f(r.vs50)}</b></div>
      </div>
      <DailyChart d={d && d.symbol === r.symbol ? d : null} entry={state.trend?.entry_days || 20} exit={state.trend?.exit_days || 10} height={220} />
    </>
  );
}

function Radar({ rows, sel, onPick }) {
  const [box, W] = useWidth();
  const S = Math.min(W, 460), c = S / 2, R = c - 26, inner = R * 0.16;
  const MAX = 30;   // percent below the 20-day high at the outer ring
  const list = rows.filter((r) => r.to_high != null);
  const rad = (x) => (x >= 0 ? inner * (1 - Math.min(1, x / 10)) : inner + (R - inner) * Math.min(1, -x / MAX));
  const maxVol = Math.max(40, ...list.map((r) => r.vol || 0));
  return (
    <div ref={box} className="radar-box">
      <svg width={S} height={S} className="radar">
        <defs>
          <radialGradient id="rg-core"><stop offset="0%" stopColor="var(--green)" stopOpacity="0.5" /><stop offset="100%" stopColor="var(--green)" stopOpacity="0" /></radialGradient>
          <linearGradient id="rg-sweep" x1="0" x2="1"><stop offset="0%" stopColor="var(--cyan)" stopOpacity="0" /><stop offset="100%" stopColor="var(--cyan)" stopOpacity="0.28" /></linearGradient>
        </defs>
        {[5, 10, 20, 30].map((p) => (
          <g key={p}>
            <circle cx={c} cy={c} r={rad(-p)} className="radar-ring" />
            <text x={c + 4} y={c - rad(-p) + 12} className="axis">−{p}%</text>
          </g>
        ))}
        <circle cx={c} cy={c} r={inner} fill="url(#rg-core)" className="radar-core" />
        <circle cx={c} cy={c} r={inner} className="radar-line" />
        <g className="radar-sweep" style={{ transformOrigin: `${c}px ${c}px` }}>
          <path d={`M${c},${c} L${c + R},${c} A${R},${R} 0 0,0 ${c + R * Math.cos(0.5)},${c - R * Math.sin(0.5)} Z`} fill="url(#rg-sweep)" />
        </g>
        {list.map((r, i) => {
          const a = (i / list.length) * Math.PI * 2 - Math.PI / 2;
          const d = rad(r.to_high);
          const x = c + Math.cos(a) * d, y = c + Math.sin(a) * d;
          const lx = c + Math.cos(a) * (R + 14), ly = c + Math.sin(a) * (R + 14);
          const size = 3 + 7 * Math.sqrt((r.vol || 20) / maxVol);
          const m = r.moves["7d"] || 0;
          return (
            <g key={r.symbol} className={`radar-dot ${r.symbol === sel ? "sel" : ""} ${r.to_high >= 0 ? "hot" : ""}`} onClick={() => onPick(r.symbol)}>
              <line x1={c + Math.cos(a) * (R + 2)} y1={c + Math.sin(a) * (R + 2)} x2={x} y2={y} className="radar-spoke" />
              <circle cx={x} cy={y} r={size} style={{ fill: m >= 0 ? "var(--green)" : "var(--red)", fillOpacity: 0.35 + Math.min(0.6, Math.abs(m) / 25) }} />
              <text x={lx} y={ly + 4} textAnchor={Math.abs(Math.cos(a)) < 0.2 ? "middle" : Math.cos(a) > 0 ? "start" : "end"} className="radar-label">{r.symbol}</text>
              <title>{`${r.symbol}: ${r.to_high >= 0 ? "über dem 20-Tage-Hoch" : `${r.to_high.toFixed(1)}% bis zum 20-Tage-Hoch`}`}</title>
            </g>
          );
        })}
        <text x={c} y={c + 4} textAnchor="middle" className="radar-center">KAUF</text>
      </svg>
    </div>
  );
}

function Scatter({ rows, sel, onPick }) {
  const [box, W] = useWidth();
  const H = 300, pl = 44, pr = 14, pt = 12, pb = 28;
  const pts = rows.filter((r) => r.vol != null && r.moves["30d"] != null);
  if (!pts.length) return <div ref={box} className="empty">keine Daten</div>;
  const xs = pts.map((r) => r.vol), ys = pts.map((r) => r.moves["30d"]);
  const x0 = Math.max(0, Math.min(...xs) * 0.85), x1 = Math.max(...xs) * 1.08;
  const yl = Math.max(10, ...ys.map(Math.abs)) * 1.12;
  const sx = (x) => pl + ((x - x0) / (x1 - x0)) * (W - pl - pr);
  const sy = (y) => pt + (1 - (y + yl) / (2 * yl)) * (H - pt - pb);
  const med = [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
  return (
    <div ref={box}>
      <svg width={W} height={H} className="scatter">
        <rect x={pl} y={pt} width={sx(med) - pl} height={sy(0) - pt} className="sc-dream" />
        <line x1={pl} x2={W - pr} y1={sy(0)} y2={sy(0)} className="baseline" />
        <line x1={sx(med)} x2={sx(med)} y1={pt} y2={H - pb} className="grid" />
        {[-yl * 0.66, -yl * 0.33, yl * 0.33, yl * 0.66].map((t) => <text key={t} x={pl - 6} y={sy(t) + 4} textAnchor="end" className="axis">{t > 0 ? "+" : ""}{t.toFixed(0)}%</text>)}
        <text x={pl} y={H - 8} className="axis">ruhig {x0.toFixed(0)}%</text>
        <text x={W - pr} y={H - 8} textAnchor="end" className="axis">wild {x1.toFixed(0)}% Schwankung/Jahr</text>
        {pts.map((r) => (
          <g key={r.symbol} className={`sc-dot ${r.symbol === sel ? "sel" : ""} ${r.symbol === "BTC" ? "btc" : ""}`} onClick={() => onPick(r.symbol)}>
            <circle cx={sx(r.vol)} cy={sy(r.moves["30d"])} r={r.symbol === "BTC" ? 7 : 5} style={{ fill: r.moves["30d"] >= 0 ? "var(--green)" : "var(--red)" }} />
            <text x={sx(r.vol) + 8} y={sy(r.moves["30d"]) + 4} className="sc-label">{r.symbol}</text>
            <title>{`${r.symbol}: ${r.moves["30d"].toFixed(1)}% in 30 Tagen, ${r.vol.toFixed(0)}% Schwankung`}</title>
          </g>
        ))}
      </svg>
    </div>
  );
}

function Race({ rows, sel, onPick }) {
  const [box, W] = useWidth();
  const [hover, setHover] = useState(null);
  const H = 300, pl = 40, pr = 56, pt = 10, pb = 20;
  const lines = rows.filter((r) => r.spark?.length > 10);
  if (!lines.length) return <div ref={box} className="empty">keine Daten</div>;
  const end = (r) => r.spark[r.spark.length - 1];
  const ranked = [...lines].sort((a, b) => end(b) - end(a));
  const lit = new Set(["BTC", ...ranked.slice(0, 3).map((r) => r.symbol), ...ranked.slice(-3).map((r) => r.symbol), sel, hover].filter(Boolean));
  const all = lines.flatMap((r) => r.spark);
  const y0 = Math.min(...all) * 0.97, y1 = Math.max(...all) * 1.03;
  const n = Math.max(...lines.map((r) => r.spark.length));
  const sx = (i, len) => pl + ((i + (n - len)) / (n - 1)) * (W - pl - pr);
  const sy = (v) => pt + (1 - (Math.log(v) - Math.log(y0)) / (Math.log(y1) - Math.log(y0))) * (H - pt - pb);
  const ticks = [0.5, 0.75, 1, 1.5, 2, 3].filter((t) => t > y0 && t < y1);
  return (
    <div ref={box}>
      <svg width={W} height={H} className="race">
        {ticks.map((t) => (
          <g key={t}><line x1={pl} x2={W - pr} y1={sy(t)} y2={sy(t)} className={t === 1 ? "baseline" : "grid"} />
            <text x={pl - 6} y={sy(t) + 4} textAnchor="end" className="axis">{Math.round(t * 100)}</text></g>
        ))}
        {[...lines].sort((a, b) => lit.has(a.symbol) - lit.has(b.symbol)).map((r) => {
          const on = lit.has(r.symbol);
          const up = end(r) >= 1;
          return (
            <g key={r.symbol} className={`race-line ${on ? "on" : ""} ${r.symbol === "BTC" ? "btc" : ""}`}
              onMouseEnter={() => setHover(r.symbol)} onMouseLeave={() => setHover(null)} onClick={() => onPick(r.symbol)}>
              <polyline fill="none" points={r.spark.map((v, i) => `${sx(i, r.spark.length).toFixed(1)},${sy(v).toFixed(1)}`).join(" ")}
                style={{ stroke: r.symbol === "BTC" ? "var(--hi)" : up ? "var(--green)" : "var(--red)" }} />
              {on && <text x={W - pr + 6} y={sy(end(r)) + 4} className="race-label" style={{ fill: r.symbol === "BTC" ? "var(--hi)" : up ? "var(--green)" : "var(--red)" }}>{r.symbol} {Math.round(end(r) * 100)}</text>}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function Breadth({ data }) {
  const [box, W] = useWidth();
  const [hover, setHover] = useState(null);
  const H = 260, pl = 36, pr = 12, pt = 10, pb = 34;
  if (!data?.length) return <div ref={box} className="empty">keine Daten</div>;
  const n = data.length;
  const sx = (i) => pl + (i / (n - 1)) * (W - pl - pr);
  const sy = (p) => pt + (1 - p / 100) * (H - pt - pb);
  const bt = data.map((d) => d.btc).filter(Boolean);
  const b0 = Math.min(...bt), b1 = Math.max(...bt);
  const sb = (v) => pt + (1 - (v - b0) / (b1 - b0 || 1)) * (H - pt - pb);
  const area = `${sx(0)},${sy(0)} ` + data.map((d, i) => `${sx(i).toFixed(1)},${sy(d.above50).toFixed(1)}`).join(" ") + ` ${sx(n - 1)},${sy(0)}`;
  const bw = (W - pl - pr) / n;
  const h = hover != null ? data[hover] : data[n - 1];
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    setHover(Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left - pl) / (W - pl - pr)) * (n - 1)))));
  };
  return (
    <div ref={box}>
      <div className="breadth-read small">
        <b>{new Date(h.day * 1000).toLocaleDateString("de-CH")}</b>
        <span><i className="sw" style={{ background: "var(--cyan)" }} />{h.above50}% über 50T</span>
        <span><i className="sw" style={{ background: "var(--green)" }} />{h.highs}% auf 20T-Hoch</span>
        {h.mood && <span style={{ color: MOOD[h.mood] }}>● {MOOD_DE[h.mood]}</span>}
        <span className="dim">BTC {Math.round(h.btc).toLocaleString("de-CH")}</span>
      </div>
      <svg width={W} height={H} className="breadth" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <defs><linearGradient id="br-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="var(--cyan)" stopOpacity="0.45" /><stop offset="100%" stopColor="var(--cyan)" stopOpacity="0.02" /></linearGradient></defs>
        {[25, 50, 75].map((p) => <g key={p}><line x1={pl} x2={W - pr} y1={sy(p)} y2={sy(p)} className={p === 50 ? "baseline" : "grid"} />
          <text x={pl - 6} y={sy(p) + 4} textAnchor="end" className="axis">{p}%</text></g>)}
        <polygon points={area} fill="url(#br-fill)" />
        <polyline points={data.map((d, i) => `${sx(i).toFixed(1)},${sy(d.above50).toFixed(1)}`).join(" ")} fill="none" stroke="var(--cyan)" strokeWidth="1.6" />
        <polyline points={data.map((d, i) => `${sx(i).toFixed(1)},${sy(d.highs).toFixed(1)}`).join(" ")} fill="none" stroke="var(--green)" strokeWidth="1" opacity="0.8" />
        <polyline points={data.map((d, i) => (d.btc ? `${sx(i).toFixed(1)},${sb(d.btc).toFixed(1)}` : null)).filter(Boolean).join(" ")} fill="none" stroke="var(--hi)" strokeWidth="1.3" strokeDasharray="1 0" opacity="0.85" />
        {data.map((d, i) => d.mood && <rect key={i} x={sx(i) - bw / 2} y={H - pb + 6} width={bw + 0.6} height={8} style={{ fill: MOOD[d.mood] }} opacity={0.85} />)}
        <text x={pl} y={H - 4} className="axis">{new Date(data[0].day * 1000).toLocaleDateString("de-CH", { month: "short", year: "2-digit" })}</text>
        <text x={W - pr} y={H - 4} textAnchor="end" className="axis">heute</text>
        {hover != null && <line x1={sx(hover)} x2={sx(hover)} y1={pt} y2={H - pb + 14} className="cross" />}
      </svg>
      <div className="pulse-legend dim small">
        {Object.entries(MOOD_DE).map(([k, v]) => <span key={k}><i className="sw" style={{ background: MOOD[k] }} />{v}</span>)}
      </div>
    </div>
  );
}

function Matrix({ m, sel, onPick }) {
  const [box, W] = useWidth();
  const [hover, setHover] = useState(null);
  const n = m.symbols.length;
  if (!n) return <div ref={box} className="empty">keine Daten</div>;
  const lab = 42, size = Math.min(W - lab - 4, 400), cell = size / n;
  const col = (v) => (v == null ? "var(--faint)" : `color-mix(in srgb, var(--cyan) ${Math.round(Math.max(0, v) * 92)}%, transparent)`);
  const hv = hover && m.values[hover[0]][hover[1]];
  return (
    <div ref={box} className="matrix-box">
      <svg width={lab + size} height={lab + size} className="matrix" onMouseLeave={() => setHover(null)}>
        {m.symbols.map((s, i) => (
          <g key={s}>
            <text x={lab - 5} y={lab + (i + 0.5) * cell + 3} textAnchor="end" className={`mx-label ${s === sel ? "sel" : ""}`} onClick={() => onPick(s)}>{s}</text>
            <text transform={`translate(${lab + (i + 0.5) * cell + 3},${lab - 5}) rotate(-60)`} className={`mx-label ${s === sel ? "sel" : ""}`} onClick={() => onPick(s)}>{s}</text>
          </g>
        ))}
        {m.values.map((row, i) => row.map((v, j) => (
          <rect key={`${i}-${j}`} x={lab + j * cell + 0.5} y={lab + i * cell + 0.5} width={cell - 1} height={cell - 1} rx={Math.min(3, cell / 6)}
            style={{ fill: col(v) }} className={hover && (hover[0] === i || hover[1] === j) ? "mx-hl" : ""}
            onMouseEnter={() => setHover([i, j])} onClick={() => onPick(m.symbols[j])} />
        )))}
      </svg>
      <div className="small matrix-read">{hover
        ? <><b>{m.symbols[hover[0]]}</b> und <b>{m.symbols[hover[1]]}</b>: {hv == null ? "–" : hv.toFixed(2)} {hv >= 0.8 ? "· fast im Gleichschritt" : hv >= 0.5 ? "· oft gemeinsam" : "· eher eigene Wege"}</>
        : <span className="dim">Maus über ein Feld: wie stark zwei Coins zusammen laufen</span>}</div>
    </div>
  );
}
