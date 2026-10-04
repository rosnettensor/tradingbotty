import { useMemo, useState } from "react";
import { Stat, Tabs } from "./components.jsx";
import { ago, fmt, pctColor, usePoll } from "./useBot.js";

const FILTERS = [["pass", "passing"], ["watch", "watching"], ["all", "all"], ["out", "filtered out"]];

const money = (x) => (x >= 1e9 ? `$${(x / 1e9).toFixed(1)}B` : x >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : `$${(x / 1e3).toFixed(0)}K`);

function hash(s) {
  let h = 0;
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return h;
}

export default function Radar({ state, setFocus, setTab }) {
  const [data] = usePoll("radar", 20000);
  const [filter, setFilter] = useState("pass");
  const [hover, setHover] = useState(null);
  const rows = data?.rows || [];
  const watching = new Set(data?.watching || []);
  const core = new Set(data?.core || []);
  const held = new Set(data?.held || []);
  const live = new Set(data?.live || []);
  const passing = rows.filter((r) => r.heat != null);
  const shown = useMemo(() => rows.filter((r) => filter === "all" || (filter === "pass" && r.heat != null)
    || (filter === "watch" && (watching.has(r.symbol) || core.has(r.symbol))) || (filter === "out" && r.heat == null)),
  [rows, filter, data?.watching]);
  const open = (s) => {
    setFocus(s);
    if (state.ticker?.some((t) => t.symbol === s)) setTab("markets");
  };
  const status = (r) => live.has(r.symbol) ? ["LIVE", "live"] : held.has(r.symbol) ? ["held", "held"]
    : core.has(r.symbol) ? ["your list", "mine"] : watching.has(r.symbol) ? ["watching", "watch"] : r.why ? [r.why, "out"] : ["", ""];

  if (!data) return <section className="panel"><div className="empty">loading radar…</div></section>;
  return (
    <div className="radar-view">
      <section className="panel stats radar-stats">
        <Stat label="Coins scanned" value={rows.length || "…"} sub={data.ts ? `scan ${ago(data.ts)}` : "first scan running"} />
        <Stat label="On Fusion" value={data.fusion ?? "?"} sub={data.fusion ? "tradable for real" : "add your Fusion key"} />
        <Stat label="Pass the filters" value={passing.length} sub="liquid, tight spread, not pumped" />
        <Stat label="Watched" value={core.size + watching.size} sub={`${core.size} yours + ${watching.size} hot`} tone="up" />
        <Stat label="Hottest" value={passing[0]?.symbol || "–"} sub={passing[0] ? `heat ${passing[0].heat} · ${fmt.pct(passing[0].change)}` : ""} />
        <Stat label="Held by strategies" value={held.size} sub={live.size ? `${live.size} with real money` : "paper"} />
      </section>
      {data.error && <div className="err">Scan failed: {data.error}</div>}

      <section className="panel radar-scope">
        <h3>RADAR <span className="dim">· center = hottest · size = trading volume · color = 24h move</span></h3>
        <Scope rows={passing.slice(0, 120)} watching={watching} core={core} held={held} live={live}
          onHover={setHover} onPick={open} />
        <div className="scope-info">
          {hover ? <>
            <b>{hover.symbol}</b> <span className={pctColor(hover.change)}>{fmt.pct(hover.change)}</span>
            <span className="dim"> · heat {hover.heat} · {money(hover.volume_usd)} traded · spread {hover.spread_pct.toFixed(2)}%</span>
          </> : <span className="dim">hover a dot · click to open its chart</span>}
        </div>
      </section>

      <section className="panel radar-list">
        <div className="row-head">
          <h3>HOT LIST <span className="dim">· the top ones join the watchlist</span></h3>
        </div>
        <div className="scroll" style={{ maxHeight: 470 }}>
          <table>
            <thead><tr><th>#</th><th>coin</th><th>heat</th><th>24h</th><th>volume</th><th>spread</th><th /></tr></thead>
            <tbody>
              {passing.slice(0, 40).map((r, i) => {
                const [label, cls] = status(r);
                return (
                  <tr key={r.symbol} className="click" onClick={() => open(r.symbol)}>
                    <td className="dim">{i + 1}</td>
                    <td><b>{r.symbol}</b></td>
                    <td><div className="heatbar"><div className={r.heat >= 0 ? "pos" : "neg"} style={{ width: `${Math.min(100, Math.abs(r.heat) * 100)}%` }} /></div></td>
                    <td className={pctColor(r.change)}>{fmt.pct(r.change)}</td>
                    <td className="dim">{money(r.volume_usd)}</td>
                    <td className="dim">{r.spread_pct.toFixed(2)}%</td>
                    <td><span className={`chip-s st-${cls}`}>{label}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel radar-map">
        <div className="row-head">
          <h3>EVERY COIN <span className="dim">· {shown.length} shown</span></h3>
          <Tabs value={filter} options={FILTERS} onChange={setFilter} />
        </div>
        <div className="heatmap">
          {shown.map((r) => {
            const [label, cls] = status(r);
            const a = Math.min(1, Math.abs(r.change) / 12);
            const bg = r.change >= 0 ? `rgba(57,255,136,${0.08 + a * 0.55})` : `rgba(255,59,92,${0.08 + a * 0.55})`;
            const size = Math.max(1, Math.min(3, Math.round(Math.log10(Math.max(r.volume_usd, 1e4)) - 5)));
            return (
              <button key={r.symbol} className={`hm st-${cls} s${size}`} style={{ background: bg }} onClick={() => open(r.symbol)}
                title={`${r.symbol}: ${fmt.pct(r.change)} · ${money(r.volume_usd)} · spread ${r.spread_pct.toFixed(2)}%${label ? ` · ${label}` : ""}`}>
                <b>{r.symbol}</b><span>{fmt.pct(r.change)}</span>
              </button>
            );
          })}
        </div>
        <p className="dim small">How it picks: momentum over 24h, closeness to the 24h high, trading volume, and a bonus for CoinGecko
          trending and Reddit buzz. Filters and the number of hot coins are sliders in Controls → Market Radar.</p>
      </section>
    </div>
  );
}

function Scope({ rows, watching, core, held, live, onHover, onPick }) {
  const R = 190;
  const maxHeat = Math.max(0.01, ...rows.map((r) => r.heat));
  const minHeat = Math.min(0, ...rows.map((r) => r.heat));
  return (
    <svg className="scope" viewBox={`${-R - 10} ${-R - 10} ${2 * R + 20} ${2 * R + 20}`}>
      <defs>
        <radialGradient id="scope-bg"><stop offset="0%" stopColor="rgba(0,240,255,0.14)" /><stop offset="100%" stopColor="rgba(0,240,255,0)" /></radialGradient>
        <linearGradient id="sweep" x1="0" y1="0" x2="1" y2="0"><stop offset="0%" stopColor="rgba(0,240,255,0)" /><stop offset="100%" stopColor="rgba(0,240,255,0.35)" /></linearGradient>
      </defs>
      <circle r={R} fill="url(#scope-bg)" stroke="var(--line)" />
      {[0.25, 0.5, 0.75].map((f) => <circle key={f} r={R * f} fill="none" stroke="var(--line)" strokeDasharray="2 4" />)}
      <line x1={-R} x2={R} y1="0" y2="0" stroke="var(--line)" /><line y1={-R} y2={R} x1="0" x2="0" stroke="var(--line)" />
      <g className="sweep"><path d={`M0 0 L${R} 0 A${R} ${R} 0 0 0 ${R * Math.cos(-0.5)} ${R * Math.sin(-0.5)} Z`} fill="url(#sweep)" /></g>
      {rows.map((r) => {
        const t = (r.heat - minHeat) / (maxHeat - minHeat || 1);
        const dist = (1 - t) * (R - 14) + 8;
        const ang = (hash(r.symbol) % 360) * Math.PI / 180;
        const size = Math.max(2.5, Math.min(9, Math.log10(Math.max(r.volume_usd, 1e4)) - 3.5) * 1.6);
        const color = r.change >= 0 ? "var(--green)" : "var(--red)";
        const ring = live.has(r.symbol) ? "var(--magenta)" : held.has(r.symbol) ? "var(--amber)"
          : watching.has(r.symbol) || core.has(r.symbol) ? "var(--cyan)" : null;
        const x = Math.cos(ang) * dist, y = Math.sin(ang) * dist;
        return (
          <g key={r.symbol} transform={`translate(${x.toFixed(1)} ${y.toFixed(1)})`} className="blip"
            onMouseEnter={() => onHover(r)} onMouseLeave={() => onHover(null)} onClick={() => onPick(r.symbol)}>
            {ring && <circle r={size + 3.5} fill="none" stroke={ring} strokeWidth="1.5" className="blip-ring" />}
            <circle r={size} fill={color} opacity={0.35 + 0.65 * t} />
            {(ring || t > 0.8) && <text y={-size - 5} textAnchor="middle">{r.symbol}</text>}
          </g>
        );
      })}
    </svg>
  );
}
