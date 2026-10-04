import { Fragment, useEffect, useState } from "react";
import { LineChart } from "./charts.jsx";
import { api, fmt } from "./useBot.js";

const COLORS = ["#00f0ff", "#ff2bd6", "#39ff88", "#ffb020", "#8a5cff", "#ff6b3d", "#4da3ff", "#e8ff3d"];

export default function Experiments({ state }) {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(null);
  const load = () => api("experiments").then(setData).catch(() => {});
  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);
  if (!data) return <div className="empty">loading experiments…</div>;

  const board = data.leaderboard;
  const color = (id) => COLORS[board.findIndex((b) => b.id === id) % COLORS.length];
  // compare in % return so variants that started later still line up
  const series = board.map((b) => {
    const pts = [...(data.curves[b.id] || []), [Date.now() / 1000, b.equity]];
    const start = pts[0]?.[1] || 100;
    return { id: b.id, name: b.name, color: color(b.id), bold: b.champion, points: pts.map(([t, v]) => [t, (v / start - 1) * 100]) };
  });
  const champ = board.find((b) => b.champion);

  const promote = async (id) => { await api(`promote/${id}`, {}); load(); };
  const clone = async (id) => { await api(`variants/${id}/clone`, {}); load(); };
  const auto = async () => { await api("auto_promote", { on: !state.auto_promote }); };

  return (
    <div className="experiments">
      <section className="panel">
        <div className="row-head">
          <h3>RETURN BY STRATEGY VARIANT (%)</h3>
          <label className="toggle">
            <input type="checkbox" checked={!!state.auto_promote} onChange={auto} /> let the Optimizer auto-promote winners
          </label>
        </div>
        <LineChart series={series} unit="%" baseline={0} height={260} />
        <div className="legend">
          {series.map((s) => <span key={s.id}><i style={{ background: s.color }} />{s.name}{s.bold ? " ★" : ""}</span>)}
        </div>
      </section>

      <section className="panel">
        <h3>LEADERBOARD <span className="dim">fitness = return − ½ × max drawdown · every variant trades on paper with the same live data</span></h3>
        <table className="board">
          <thead>
            <tr><th /><th>variant</th><th>equity</th><th>return</th><th>max dd</th><th>fitness</th><th>trades</th><th>win rate</th><th>fees</th><th>age</th><th /></tr>
          </thead>
          <tbody>
            {board.map((b) => (
              <Fragment key={b.id}>
                <tr className={b.champion ? "champ" : ""} onClick={() => setOpen(open === b.id ? null : b.id)}>
                  <td><i className="swatch" style={{ background: color(b.id) }} /></td>
                  <td><b>{b.name}</b>{b.champion && <span className="crown"> ★ champion</span>}</td>
                  <td>{fmt.usd(b.equity)}</td>
                  <td className={b.return_pct >= 0 ? "up" : "down"}>{fmt.pct(b.return_pct)}</td>
                  <td className="down">{b.max_drawdown_pct.toFixed(2)}%</td>
                  <td>{b.fitness.toFixed(2)}</td>
                  <td>{b.trades}</td>
                  <td>{b.win_rate == null ? "–" : `${b.win_rate}%`}</td>
                  <td>{fmt.usd(b.fees)}</td>
                  <td className="dim">{b.age_h}h</td>
                  <td className="actions" onClick={(e) => e.stopPropagation()}>
                    {!b.champion && <button onClick={() => promote(b.id)}>promote</button>}
                    <button onClick={() => clone(b.id)}>mutate</button>
                  </td>
                </tr>
                {open === b.id && (
                  <tr className="cfg-row">
                    <td colSpan={11}><ConfigDiff config={b.config} base={champ?.config} /></td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        {data.retired.length > 0 && <p className="dim">Retired: {data.retired.map((r) => r.name).join(", ")}</p>}
      </section>
    </div>
  );
}

function ConfigDiff({ config, base }) {
  return (
    <div className="cfg">
      {Object.entries(config).map(([k, v]) => {
        const changed = base && base[k] !== v;
        return (
          <span key={k} className={changed ? "changed" : ""} title={changed ? `champion: ${base[k]}` : ""}>
            {k.replace(/_/g, " ")} <b>{String(v)}</b>
          </span>
        );
      })}
    </div>
  );
}
