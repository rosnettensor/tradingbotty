import { Fragment, useEffect, useState } from "react";
import { LineChart } from "./charts.jsx";
import { Tabs } from "./components.jsx";
import { api, fmt, pctColor } from "./useBot.js";

const COLORS = ["#00f0ff", "#ff2bd6", "#39ff88", "#ffb020", "#8a5cff", "#ff6b3d", "#4da3ff", "#e8ff3d"];

export default function Lab({ state }) {
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
  const retire = async (id) => { if (confirm("Retire this strategy? Its paper account stops trading.")) { await api(`variants/${id}/retire`, {}); load(); } };
  const auto = async () => { await api("auto_promote", { on: !state.auto_promote }); };

  return (
    <div className="experiments">
      <BacktestLab board={board} reload={load} />
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
        <h3>LIVE LEADERBOARD <span className="dim">fitness = return − ½ × max drawdown · the champion is picked on the same recent window for everyone · every strategy trades on paper with the same live data</span></h3>
        <table className="board">
          <thead>
            <tr><th /><th>variant</th><th>equity</th><th>return</th><th>max dd</th><th>fitness</th><th title="What decides the champion: fitness over the same recent window for everyone">last {board[0]?.window_h ?? 48}h</th><th>trades</th><th>win rate</th><th>fees</th><th>age</th><th /></tr>
          </thead>
          <tbody>
            {board.map((b) => (
              <Fragment key={b.id}>
                <tr className={b.champion ? "champ" : ""} onClick={() => setOpen(open === b.id ? null : b.id)}>
                  <td><i className="swatch" style={{ background: color(b.id) }} /></td>
                  <td><b>{b.name}</b>{b.champion && <span className="crown"> ★ champion</span>}{b.benchmark && <span className="bench"> yardstick</span>}</td>
                  <td>{fmt.usd(b.equity)}</td>
                  <td className={b.return_pct >= 0 ? "up" : "down"}>{fmt.pct(b.return_pct)}</td>
                  <td className="down">{b.max_drawdown_pct.toFixed(2)}%</td>
                  <td>{b.fitness.toFixed(2)}</td>
                  <td className={(b.recent_fitness ?? 0) >= 0 ? "up" : "down"}><b>{b.recent_fitness?.toFixed(2) ?? "–"}</b></td>
                  <td>{b.trades}</td>
                  <td>{b.win_rate == null ? "–" : `${b.win_rate}%`}</td>
                  <td>{fmt.usd(b.fees)}</td>
                  <td className="dim">{b.age_h}h</td>
                  <td className="actions" onClick={(e) => e.stopPropagation()}>
                    {!b.champion && <button onClick={() => promote(b.id)}>promote</button>}
                    {!b.benchmark && <button onClick={() => clone(b.id)}>mutate</button>}
                    {!b.champion && !b.benchmark && <button onClick={() => retire(b.id)}>retire</button>}
                  </td>
                </tr>
                {open === b.id && (
                  <tr className="cfg-row">
                    <td colSpan={12}><ConfigDiff config={b.config} base={champ?.config} /></td>
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

const WINDOWS = [["6", "6h"], ["24", "24h"], ["72", "3d"], ["168", "7d"]];

function BacktestLab({ board, reload }) {
  const [vid, setVid] = useState("");
  const [hours, setHours] = useState("24");
  const [n, setN] = useState(40);
  const [res, setRes] = useState(null);
  const [tune, setTune] = useState(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const v = board.find((b) => b.id === vid) || board.find((b) => b.champion) || board[0];
  const go = async (label, fn) => {
    setBusy(label); setMsg("");
    try { await fn(); } catch (e) { setMsg(e.message); } finally { setBusy(""); }
  };
  const backtest = () => go("bt", async () => { setTune(null); setRes(await api("backtest", { variant_id: v.id, hours: Number(hours) })); });
  const autotune = () => go("tune", async () => { setRes(null); setTune(await api("autotune", { variant_id: v.id, hours: Number(hours), n })); });
  const adopt = (r) => go("adopt", async () => {
    const out = await api(`variants/${v.id}/config`, { changes: r.config, as_new: true, name: `${v.name.split(" #")[0]} tuned` });
    setMsg(`Created ${out.name}. It now trades live data on its own paper account.`); reload();
  });
  if (!v) return null;
  return (
    <section className="panel lab">
      <div className="row-head">
        <h3>BACKTEST LAB <span className="dim">· replays recorded prices with the real rules and fees</span></h3>
        <div className="row-tools">
          <select value={v.id} onChange={(e) => setVid(e.target.value)}>
            {board.map((b) => <option key={b.id} value={b.id}>{b.champion ? "★ " : ""}{b.name}</option>)}
          </select>
          <Tabs value={hours} options={WINDOWS} onChange={setHours} />
          <button onClick={backtest} disabled={!!busy}>{busy === "bt" ? "replaying…" : "backtest"}</button>
          <label className="toggle">try <input type="number" min={5} max={120} value={n} onChange={(e) => setN(Number(e.target.value))} style={{ width: 52 }} /> variations</label>
          <button className="primary" onClick={autotune} disabled={!!busy}>{busy === "tune" ? "searching…" : "auto-tune"}</button>
        </div>
      </div>
      <p className="dim small">Hype and news have no history yet, so backtests count them as neutral. The bot records every minute (7 days kept), so longer windows fill up as it runs. Auto-tune mutates the strategy many times and replays each one: a quick way to find better settings, but small samples can fool you.</p>
      {msg && <p className="ok-msg">{msg}</p>}
      {res && <BacktestResult r={res} />}
      {tune && (
        <div className="scroll">
          <table className="board">
            <thead><tr><th>#</th><th>candidate</th><th>return</th><th>max dd</th><th>fitness</th><th>trades</th><th>win rate</th><th>fees</th><th>what changed</th><th /></tr></thead>
            <tbody>
              {tune.results.map((r, i) => (
                <tr key={i} className={r.label === "current settings" ? "champ" : ""}>
                  <td className="dim">{r.rank}</td>
                  <td><b>{r.label}</b></td>
                  <td className={pctColor(r.return_pct)}>{fmt.pct(r.return_pct)}</td>
                  <td className="down">{r.max_drawdown_pct}%</td>
                  <td>{r.fitness}</td>
                  <td>{r.trades}</td>
                  <td>{r.win_rate == null ? "–" : `${r.win_rate}%`}</td>
                  <td>{fmt.usd(r.fees)}</td>
                  <td className="cfg-cell"><Changes a={v.config} b={r.config} /></td>
                  <td>{r.label !== "current settings" && <button onClick={() => adopt(r)} disabled={!!busy}>try it (paper)</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="dim small">Tested {tune.tested} settings over the last {tune.results[0]?.hours}h. Strategies that never traded are ranked last.</p>
        </div>
      )}
    </section>
  );
}

function Changes({ a, b }) {
  const diff = Object.keys(b).filter((k) => a[k] !== b[k]);
  if (!diff.length) return <span className="dim">–</span>;
  return <span className="cfg">{diff.map((k) => <span key={k} className="changed">{k.replace(/_/g, " ")} <b>{String(b[k])}</b></span>)}</span>;
}

export function BacktestResult({ r, compact }) {
  const start = r.curve[0]?.[1] || 100;
  const series = [{ id: "bt", color: "var(--magenta)", bold: true, points: r.curve.map(([t, v]) => [t, (v / start - 1) * 100]) }];
  return (
    <div className="bt-result">
      <div className="bt-stats">
        <span>return <b className={pctColor(r.return_pct)}>{fmt.pct(r.return_pct)}</b></span>
        <span>max drawdown <b className="down">{r.max_drawdown_pct}%</b></span>
        <span>fitness <b>{r.fitness}</b></span>
        <span>trades <b>{r.trades}</b></span>
        <span>win rate <b>{r.win_rate == null ? "–" : `${r.win_rate}%`}</b></span>
        <span>fees <b className="down">{fmt.usd(r.fees)}</b></span>
        <span>just holding BTC <b className={pctColor(r.btc_hold_pct)}>{fmt.pct(r.btc_hold_pct)}</b></span>
        <span className="dim">over {r.hours}h</span>
      </div>
      {r.note && <p className="warn-msg">{r.note}</p>}
      <LineChart series={series} unit="%" baseline={0} height={compact ? 140 : 200} />
      {!compact && r.trade_list?.length > 0 && (
        <div className="scroll" style={{ maxHeight: 220 }}>
          <table>
            <tbody>
              {[...r.trade_list].reverse().map((t, i) => (
                <tr key={i}>
                  <td className="dim">{new Date(t.ts * 1000).toLocaleString([], { day: "2-digit", hour: "2-digit", minute: "2-digit" })}</td>
                  <td className={t.side === "BUY" ? "up" : "down"}>{t.side}</td>
                  <td><b>{t.symbol}</b></td>
                  <td>{fmt.usd(t.notional)}</td>
                  <td className={pctColor(t.pnl)}>{t.pnl == null ? "" : fmt.usd(t.pnl)}</td>
                  <td className="dim">{t.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
