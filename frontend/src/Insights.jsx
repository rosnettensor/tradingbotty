import { useMemo, useState } from "react";
import { SIGNAL_LABELS } from "./components.jsx";
import { fmt, pctColor, usePoll } from "./useBot.js";

// Pearson correlation of 1-minute log returns
function corr(a, b) {
  const n = Math.min(a.length, b.length);
  if (n < 10) return null;
  const x = a.slice(-n), y = b.slice(-n);
  const mx = x.reduce((s, v) => s + v, 0) / n, my = y.reduce((s, v) => s + v, 0) / n;
  let sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < n; i++) {
    const dx = x[i] - mx, dy = y[i] - my;
    sxy += dx * dy; sxx += dx * dx; syy += dy * dy;
  }
  return sxx && syy ? sxy / Math.sqrt(sxx * syy) : null;
}
const rets = (s) => s.slice(1).map((v, i) => (s[i] > 0 && v > 0 ? Math.log(v / s[i]) : 0));
const heat = (c) => c == null ? "rgba(255,255,255,0.03)"
  : c >= 0 ? `rgba(0,240,255,${0.08 + 0.8 * c})` : `rgba(255,43,214,${0.08 + 0.8 * -c})`;

export default function Insights({ state, setFocus, setTab }) {
  const [pick, setPick] = useState(null);
  const open = (s) => { setFocus(s); setTab("cockpit"); };
  return (
    <div className="insights">
      <section className="panel ins-corr"><CorrMatrix state={state} onPick={setPick} pick={pick} /></section>
      <section className="panel ins-detail"><CoinDetail state={state} symbol={pick} onOpen={open} /></section>
      <section className="panel ins-map"><StrategyMap state={state} /></section>
      <section className="panel ins-mix"><SignalMix state={state} onPick={setPick} pick={pick} /></section>
    </div>
  );
}

function CorrMatrix({ state, onPick, pick }) {
  const [hover, setHover] = useState(null);
  const coins = useMemo(() => (state.ticker || []).filter((t) => t.kind === "crypto" && (t.spark || []).length > 20)
    .sort((a, b) => Math.abs(b.change) - Math.abs(a.change)).slice(0, 16), [state.ticker]);
  const m = useMemo(() => {
    const r = coins.map((c) => rets(c.spark));
    return r.map((a) => r.map((b) => corr(a, b)));
  }, [coins]);
  const n = coins.length, cell = 26, pad = 52;
  const avg = n > 1 ? m.flat().filter((c, i) => c != null && i % (n + 1) !== 0).reduce((s, c, _, arr) => s + c / arr.length, 0) : 0;
  return (
    <>
      <h3>WHO MOVES WITH WHOM <span className="dim">· correlation of the last hour's minute moves · cyan = together, pink = opposite</span></h3>
      {n < 2 ? <div className="empty">collecting prices…</div> : (
        <div className="corr-wrap">
          <svg viewBox={`0 0 ${pad + n * cell} ${pad + n * cell}`} className="corr">
            {coins.map((c, i) => (
              <g key={c.symbol}>
                <text x={pad - 4} y={pad + i * cell + cell / 2 + 3} textAnchor="end" className={pick === c.symbol ? "sel" : ""}>{c.symbol}</text>
                <text x={pad + i * cell + cell / 2} y={pad - 6} textAnchor="start" transform={`rotate(-50 ${pad + i * cell + cell / 2} ${pad - 6})`}
                  className={pick === c.symbol ? "sel" : ""}>{c.symbol}</text>
              </g>
            ))}
            {m.map((row, i) => row.map((c, j) => (
              <rect key={`${i}-${j}`} x={pad + j * cell + 1} y={pad + i * cell + 1} width={cell - 2} height={cell - 2} rx="3"
                fill={i === j ? "rgba(255,255,255,0.08)" : heat(c)} className="corr-cell"
                style={{ animationDelay: `${(i + j) * 18}ms` }}
                onMouseEnter={() => setHover({ a: coins[i], b: coins[j], c })} onMouseLeave={() => setHover(null)}
                onClick={() => onPick(coins[i].symbol)} />
            )))}
          </svg>
          <div className="corr-side">
            {hover ? <>
              <div className="big-k">{hover.a.symbol} × {hover.b.symbol}</div>
              <div className="corr-val" style={{ color: hover.c >= 0 ? "var(--cyan)" : "var(--magenta)" }}>{hover.c == null ? "–" : hover.c.toFixed(2)}</div>
              <p className="dim small">{hover.c == null ? "not enough data" : hover.c > 0.6 ? "They move almost as one: holding both is barely more diversified than holding one."
                : hover.c > 0.2 ? "They tend to move the same way." : hover.c > -0.2 ? "Mostly independent: good for spreading risk."
                : "They tend to move in opposite directions."}</p>
            </> : <>
              <div className="big-k">Market herd</div>
              <div className="corr-val" style={{ color: "var(--cyan)" }}>{avg.toFixed(2)}</div>
              <p className="dim small">Average correlation. High = everything moves together (one big crypto mood), so owning more coins
                spreads less risk. Hover a square to compare two coins, click to see one coin's details.</p>
            </>}
          </div>
        </div>
      )}
    </>
  );
}

function CoinDetail({ state, symbol, onOpen }) {
  const sym = symbol || Object.entries(state.scores || {}).sort((a, b) => b[1] - a[1])[0]?.[0];
  const t = (state.ticker || []).find((x) => x.symbol === sym);
  if (!sym || !t) return <><h3>COIN DETAIL</h3><div className="empty">click a coin on the left or below</div></>;
  const sig = state.signals?.[sym] || {};
  const w = state.weights || {};
  const score = state.scores?.[sym];
  const why = state.why_not?.[sym];
  const held = state.champion?.positions?.find?.((p) => p.symbol === sym);
  const hot = state.radar?.hot?.find((r) => r.symbol === sym);
  const s = t.spark || [];
  const lo = Math.min(...s), hi = Math.max(...s);
  const path = s.map((v, i) => `${i ? "L" : "M"}${(i / (s.length - 1)) * 300} ${60 - ((v - lo) / (hi - lo || 1)) * 56}`).join(" ");
  return (
    <>
      <h3>COIN DETAIL <span className="dim">· what the bot thinks of it right now</span></h3>
      <div className="cd-head">
        <div><div className="big-k">{sym}</div><div className="dim small">{fmt.price(t.price)} USD</div></div>
        <div className={`cd-chg ${pctColor(t.change)}`}>{fmt.pct(t.change)}<div className="dim small">24h</div></div>
        <div className="cd-score" style={{ color: score >= (state.champion_config?.entry_score ?? 0.35) ? "var(--green)" : "var(--text)" }}>
          {score == null ? "–" : score.toFixed(2)}<div className="dim small">score (buy ≥ {state.champion_config?.entry_score ?? "?"})</div>
        </div>
      </div>
      <svg viewBox="0 0 300 62" className="cd-spark"><path d={path} fill="none" stroke={t.change >= 0 ? "var(--green)" : "var(--red)"} strokeWidth="1.6" /></svg>
      <div className="cd-why">
        <span className="tag">{held ? "🤖 held by the champion" : why ? `not buying: ${why}` : "buy-ready"}</span>
        {hot && <span className="tag">radar heat {hot.heat}</span>}
        {(state.avoid || []).includes(sym) && <span className="tag st-out">Professor says avoid</span>}
      </div>
      <div className="cd-sigs">
        {Object.keys(SIGNAL_LABELS).map((k) => {
          const c = (sig[k] || 0) * (w[k] || 0);
          return (
            <div key={k} className="cd-sig" title={`signal ${(sig[k] || 0).toFixed(2)} × weight ${(w[k] || 0).toFixed(2)}`}>
              <span>{SIGNAL_LABELS[k]}</span>
              <div className="cd-bar"><div className={c >= 0 ? "pos" : "neg"} style={{ width: `${Math.min(50, Math.abs(c) * 50)}%`, [c >= 0 ? "left" : "right"]: "50%" }} /></div>
              <b className={c > 0 ? "up" : c < 0 ? "down" : "dim"}>{c >= 0 ? "+" : ""}{c.toFixed(2)}</b>
            </div>
          );
        })}
      </div>
      <button className="primary" onClick={() => onOpen(sym)}>open chart in cockpit</button>
    </>
  );
}

function StrategyMap({ state }) {
  const [data] = usePoll("experiments", 30000);
  const [hover, setHover] = useState(null);
  const board = data?.leaderboard || [];
  if (!board.length) return <><h3>STRATEGY MAP</h3><div className="empty">loading…</div></>;
  const W = 760, H = 300, P = 40;
  const xs = board.map((b) => b.max_drawdown_pct), ys = board.map((b) => b.return_pct);
  const xmax = Math.max(1, ...xs) * 1.15, span = Math.max(1, Math.max(0, ...ys) - Math.min(0, ...ys)), ymin = Math.min(0, ...ys) - span * 0.1, ymax = Math.max(0, ...ys) + span * 0.12;
  const X = (v) => P + (v / xmax) * (W - P - 10), Y = (v) => H - P - ((v - ymin) / (ymax - ymin)) * (H - P - 10);
  return (
    <>
      <h3>STRATEGY MAP <span className="dim">· up = more profit · left = smoother ride · size = trades · ★ = champion (trades your money)</span></h3>
      <svg viewBox={`0 0 ${W} ${H}`} className="smap">
        <rect x={P} y={10} width={W - P - 10} height={Y(0) - 10} fill="rgba(57,255,136,0.04)" />
        <line x1={P} x2={W - 10} y1={Y(0)} y2={Y(0)} stroke="var(--line)" strokeDasharray="3 4" />
        <text x={W - 12} y={Y(0) - 4} textAnchor="end" className="ax">break-even</text>
        <text x={P} y={H - 8} className="ax">0% drawdown</text>
        <text x={W - 12} y={H - 8} textAnchor="end" className="ax">{xmax.toFixed(1)}% worst dip →</text>
        <text x={12} y={18} className="ax">{ymax.toFixed(1)}%</text>
        <text x={12} y={H - P} className="ax">{ymin.toFixed(1)}%</text>
        {board.map((b, i) => {
          const r = 5 + Math.min(12, Math.sqrt(b.trades || 0) * 2.2);
          const col = b.champion ? "var(--amber)" : b.benchmark ? "var(--dim)" : b.return_pct >= 0 ? "var(--green)" : "var(--red)";
          return (
            <g key={b.id} transform={`translate(${X(b.max_drawdown_pct)} ${Y(b.return_pct)})`} className="sdot"
              style={{ animationDelay: `${i * 60}ms` }} onMouseEnter={() => setHover(b)} onMouseLeave={() => setHover(null)}>
              <circle r={r} fill={col} fillOpacity="0.25" stroke={col} strokeWidth={b.champion ? 2.5 : 1.2} />
              {b.champion && <circle r={r + 5} fill="none" stroke={col} className="sdot-ring" />}
              {(b.champion || b.benchmark || hover?.id === b.id) && <text y={-r - 4} textAnchor="middle">{b.champion ? "★ " : ""}{b.name}</text>}
            </g>
          );
        })}
      </svg>
      <div className="smap-info">
        {hover ? <><b>{hover.name}</b> {fmt.pct(hover.return_pct)} · worst dip {hover.max_drawdown_pct.toFixed(2)}% · {hover.trades} trades
          · last {hover.window_h ?? 48}h fitness {hover.recent_fitness?.toFixed(2) ?? "–"} · {hover.age_h}h old</>
          : <span className="dim">Top-left is the dream: profit without scary dips. Hover a dot.</span>}
      </div>
    </>
  );
}

function SignalMix({ state, onPick, pick }) {
  const w = state.weights || {};
  const keys = Object.keys(SIGNAL_LABELS);
  const rows = Object.entries(state.scores || {}).sort((a, b) => b[1] - a[1]).slice(0, 14);
  return (
    <>
      <h3>WHERE EACH SCORE COMES FROM <span className="dim">· champion's view · each cell = signal × weight · click a row</span></h3>
      <table className="mix">
        <thead><tr><th>coin</th>{keys.map((k) => <th key={k}>{SIGNAL_LABELS[k]}<div className="dim">×{(w[k] ?? 0).toFixed(1)}</div></th>)}<th>score</th></tr></thead>
        <tbody>
          {rows.map(([sym, score]) => (
            <tr key={sym} className={`click ${pick === sym ? "sel" : ""}`} onClick={() => onPick(sym)}>
              <td><b>{sym}</b></td>
              {keys.map((k) => {
                const c = (state.signals?.[sym]?.[k] || 0) * (w[k] || 0);
                const a = Math.min(1, Math.abs(c));
                return <td key={k} className="mix-cell" style={{ background: c >= 0 ? `rgba(57,255,136,${a * 0.7})` : `rgba(255,59,92,${a * 0.7})` }}
                  title={`${SIGNAL_LABELS[k]}: ${c >= 0 ? "+" : ""}${c.toFixed(2)}`}>{Math.abs(c) >= 0.05 ? c.toFixed(1) : ""}</td>;
              })}
              <td className={score >= (state.champion_config?.entry_score ?? 0.35) ? "up" : ""}><b>{score.toFixed(2)}</b></td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
