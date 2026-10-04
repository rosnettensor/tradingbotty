import { useEffect, useRef, useState } from "react";
import Sphere from "./Sphere.jsx";
import { LineChart } from "./charts.jsx";
import { api, fmt } from "./useBot.js";

export default function Cockpit({ state, pulse }) {
  const champ = state.champion;
  const [curve, setCurve] = useState([]);
  useEffect(() => {
    let alive = true;
    const load = () => api("experiments").then((e) => alive && champ && setCurve(e.curves[champ.id] || [])).catch(() => {});
    load();
    const t = setInterval(load, 60000);
    return () => { alive = false; clearInterval(t); };
  }, [champ?.id]);

  const ret = champ ? (champ.equity / champ.start - 1) * 100 : 0;
  const scores = Object.entries(state.scores || {}).sort((a, b) => b[1] - a[1]);
  const energy = Math.min(1, scores.reduce((s, [, v]) => s + Math.abs(v), 0) / Math.max(1, scores.length) * 2);
  const points = champ ? [...curve, [Date.now() / 1000, champ.equity]] : [];
  const regime = state.regime || {};

  return (
    <div className="cockpit">
      <section className="panel core">
        <Sphere mood={ret / 5} energy={energy} pulse={pulse} label={
          <div className="core-label">
            <div className="dim">{champ?.name || "–"} · {state.mode.toUpperCase()}</div>
            <div className="equity">{fmt.usd(champ?.equity)}</div>
            <div className={ret >= 0 ? "up" : "down"}>{fmt.pct(ret)} since start</div>
          </div>
        } />
        <div className="regime">
          <span>MOOD <b className={regime.mood === "risk-on" ? "up" : regime.mood === "risk-off" ? "down" : ""}>{regime.mood || "…"}</b></span>
          <span>FEAR&GREED <b>{regime.fear_greed ?? "…"}</b> {regime.fear_greed_label}</span>
          <span>RISK APPETITE <b>{state.risk_appetite?.toFixed(2)}</b></span>
          <span>CASH <b>{fmt.usd(champ?.cash)}</b></span>
        </div>
      </section>

      <section className="panel chart">
        <h3>CHAMPION EQUITY (USD)</h3>
        <LineChart series={[{ id: "c", color: "var(--cyan)", bold: true, points }]} baseline={champ?.start} />
      </section>

      <section className="panel positions">
        <h3>OPEN POSITIONS</h3>
        {!champ?.positions.length && <div className="empty">no open positions: the team is watching</div>}
        <table>
          <tbody>
            {champ?.positions.map((p) => (
              <tr key={p.symbol}>
                <td><b>{p.symbol}</b></td>
                <td>{fmt.usd(p.value)}</td>
                <td className={p.pnl_pct >= 0 ? "up" : "down"}>{fmt.pct(p.pnl_pct)}</td>
                <td className="dim">{Math.round((Date.now() / 1000 - p.opened) / 60)}m</td>
              </tr>
            ))}
          </tbody>
        </table>
        <h3>PREDICTOR SCORES</h3>
        <div className="scores">
          {scores.slice(0, 10).map(([sym, v]) => (
            <div className="score" key={sym}>
              <span>{sym}</span>
              <div className="track"><div className={v >= 0 ? "pos" : "neg"} style={{ width: `${Math.min(50, Math.abs(v) * 50)}%`, [v >= 0 ? "left" : "right"]: "50%" }} /></div>
              <span className={v >= 0 ? "up" : "down"}>{v.toFixed(2)}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="panel log">
        <h3>AGENT FEED</h3>
        <Feed log={state.log || []} />
      </section>

      <section className="panel trades">
        <h3>TRADES</h3>
        <table>
          <tbody>
            {(state.trades || []).filter((t) => t.champion || t.mode === "live").slice(0, 30).map((t, i) => (
              <tr key={i} className={t.mode === "live" ? "live-row" : ""}>
                <td className="dim">{fmt.time(t.ts)}</td>
                <td className={t.side === "BUY" ? "up" : "down"}>{t.side}</td>
                <td><b>{t.symbol}</b></td>
                <td>{fmt.usd(t.notional)}</td>
                <td className={t.pnl > 0 ? "up" : t.pnl < 0 ? "down" : "dim"}>{t.pnl == null ? "" : fmt.usd(t.pnl)}</td>
                <td className="dim reason">{t.mode === "live" ? "LIVE" : t.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function Feed({ log }) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 80) el.scrollTop = el.scrollHeight;
  }, [log.length]);
  return (
    <div className="feed" ref={ref}>
      {log.map((l, i) => (
        <div key={i} className={`line ${l.level}`}>
          <span className="dim">{fmt.time(l.ts)}</span> <span className="who">{l.agent}</span> {l.message}
        </div>
      ))}
      <div className="cursor">▋</div>
    </div>
  );
}
