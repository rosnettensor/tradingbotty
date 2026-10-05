import { useEffect, useState } from "react";
import { LineChart } from "./charts.jsx";
import { Tabs } from "./components.jsx";
import { ago, api, pctColor, usePoll } from "./useBot.js";

const COLORS = ["#00f0ff", "#ff2bd6", "#39ff88", "#ffb020", "#8a5cff", "#ff6b3d", "#4da3ff", "#e8ff3d"];
const PERIODS = [["full", "all history"], ["last_2y", "2 years"], ["last_1y", "1 year"], ["last_6m", "6 months"], ["first_half", "1st half"], ["second_half", "2nd half"]];
const dayFmt = (ts) => new Date(ts * 1000).toLocaleDateString([], { month: "short", year: "2-digit" });

export default function Research({ state, openAgent }) {
  const [res, setRes] = useState(null);
  const [pat, reloadPat] = usePoll("patterns", 0);
  useEffect(() => { api("research").then((r) => r && r.rows && setRes(r)).catch(() => {}); }, [state.research?.ts]);
  return (
    <div className="research-tab">
      <HistoryTest state={state} res={res} setRes={(r) => { setRes(r); reloadPat(); }} openAgent={openAgent} />
      <PatternHunt p={pat} openAgent={openAgent} />
      <FreeData node={(state.nodes || []).find((n) => n.id === "collector")} openAgent={openAgent} />
    </div>
  );
}

function HistoryTest({ state, res, setRes, openAgent }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [period, setPeriod] = useState("full");
  const [pick, setPick] = useState([]);
  const [group, setGroup] = useState("all");
  const run = async () => {
    setBusy(true); setMsg("");
    try { const r = await api("research/run", {}); if (r.busy) setMsg("already running…"); else setRes(r); }
    catch (e) { setMsg(e.message); } finally { setBusy(false); }
  };
  const groups = res ? ["all", ...new Set(res.rows.map((r) => r.group))] : ["all"];
  const rows = res ? [...res.rows].filter((r) => group === "all" || r.group === group || r.group === "Benchmark")
    .sort((a, b) => (b.robust - a.robust) || ((b[period]?.sharpe ?? -9) - (a[period]?.sharpe ?? -9))) : [];
  const btc = rows.find((r) => r.group === "Benchmark" && r.name.includes("Bitcoin"));
  const brain = state.brain || {};
  const live = rows.find((r) => r.name === brain.strategy);
  const shown = res ? [btc, live, ...rows.filter((r) => r !== btc && r !== live && (pick.length ? pick.includes(r.name) : r.group !== "Benchmark")).slice(0, pick.length || 2)].filter(Boolean) : [];
  const t0 = res ? Date.parse(res.from) / 1000 : 0;
  const series = shown.map((r, i) => ({ id: r.name, name: r.name, color: COLORS[i % COLORS.length], bold: r === live,
    points: r.curve.map((v, k) => [t0 + k * (res.curve_step || 3) * 86400, (v - 1) * 100]) }));
  const useLive = async (r) => {
    const warn = r.robust ? "" : "\n\nCareful: this one does NOT pass every robustness check, so its results may be luck.";
    if (!window.confirm(`Let "${r.name}" manage the bot's real coins?\n\nIt decides once a day on daily candles, exactly as in this test. Coins it doesn't want are sold, the ones it wants are bought up to its share of your limit (Controls).${warn}`)) return;
    try { await api("brain", { on: true, strategy: r.name }); setMsg(`"${r.name}" now manages your real money.`); } catch (e) { setMsg(e.message); }
  };
  const stopLive = async () => {
    if (!window.confirm("Stop the Daily Brain?\n\nThen nothing trades the real money. The bot's coins stay as they are.")) return;
    try { await api("brain", { on: false }); setMsg("Daily brain off: nothing trades the real money until you switch it on again."); } catch (e) { setMsg(e.message); }
  };
  const toggle = (name) => setPick((p) => (p.includes(name) ? p.filter((x) => x !== name) : [...p, name]));
  return (
    <section className="panel lab research">
      <div className="row-head">
        <h3>HISTORY TEST <span className="dim">· every strategy on all daily history, real fees and spread, vs simply holding Bitcoin · re-run nightly by the <button className="mini linkish" onClick={() => openAgent("researcher")}>Researcher</button></span></h3>
        <div className="row-tools">
          {res && <Tabs value={period} options={PERIODS} onChange={setPeriod} />}
          {brain.on && <button onClick={stopLive}>stop daily brain</button>}
          <button className="primary" onClick={run} disabled={busy}>{busy ? "loading history + free data…" : res ? "run again" : "run the history test"}</button>
        </div>
      </div>
      <p className="dim small">Decides on each day's close, trades at the next day's open, pays {res?.cost_per_side_pct ?? 0.4}% per buy or sell. <b>Robust ✓</b> = beats holding Bitcoin (risk-adjusted) in both halves AND in most calendar years, AND the skill check (deflated Sharpe, corrected for testing {res?.strategies_tested ?? "many"} strategies at once) says it's at least 80% likely not luck. The Daily Brain only trusts robust ones: if the live strategy fails three nights in a row, it switches to the best robust one. Coins are today's survivors, so the past looks a bit rosier than it was. Click rows to compare curves.</p>
      {msg && <p className={/now manages|brain off/.test(msg) ? "ok-msg" : "err-msg"}>{msg}</p>}
      {res?.simulated && <p className="err-msg">Simulated prices (simulate mode): these numbers are not real.</p>}
      {!res && <div className="empty">No history test yet. It runs tonight by itself, or press "run the history test" (takes a minute: it downloads all daily candles and the free data).</div>}
      {res && (
        <>
          <p className="dim small">{res.from} to {res.to} · {res.days} days · {res.coins.length} coins: {res.coins.join(", ")} · tested {ago(res.ts)}</p>
          <div className="legend small">{series.map((x) => <span key={x.id} style={{ color: x.color, marginRight: 16 }}>■ {x.name}{x.bold ? " (live)" : ""}</span>)}</div>
          <LineChart series={series} height={240} unit="%" baseline={0} xfmt={dayFmt} />
          <div className="row-head" style={{ marginTop: 8 }}>
            <span className="dim small">{rows.filter((r) => r.robust).length} robust of {res.rows.length}</span>
            <Tabs value={group} options={groups.map((g) => [g, g === "all" ? "all groups" : g])} onChange={setGroup} />
          </div>
          <div className="scroll">
            <table className="board">
              <thead><tr><th>strategy</th><th>return</th><th>per year</th><th>worst drop</th><th>sharpe</th><th>trades</th><th title="fees and spread paid per year, as a share of the account">fees/yr</th><th>in coins</th><th title="calendar years in which it beat holding Bitcoin, risk-adjusted">years won</th><th title="deflated Sharpe: chance the result is skill, not luck">skill</th><th>robust</th><th /></tr></thead>
              <tbody>
                {rows.map((r) => {
                  const st = r[period] || {};
                  const isLive = brain.strategy === r.name;
                  return (
                    <tr key={r.name} className={`${r === btc ? "champ" : ""} ${isLive ? "live-strat" : ""} ${pick.includes(r.name) ? "picked" : ""}`} onClick={() => toggle(r.name)} title={r.explain}>
                      <td><b>{r.name}</b>{isLive && <span className="live-badge">{brain.on ? "LIVE" : "picked"}</span>}<div className="dim small">{r.group}</div></td>
                      <td className={pctColor(st.return_pct)}>{st.return_pct == null ? "–" : `${st.return_pct > 0 ? "+" : ""}${st.return_pct}%`}</td>
                      <td>{st.cagr_pct == null ? "–" : `${st.cagr_pct}%`}</td>
                      <td className="down">{st.max_dd_pct}%</td>
                      <td>{st.sharpe}</td>
                      <td>{r.trades}</td>
                      <td>{r.fees_pct}%</td>
                      <td>{r.invested_pct}%</td>
                      <td>{r.group === "Benchmark" ? "" : `${r.years_won ?? "–"}/${r.years_total ?? "–"}`}</td>
                      <td>{r.skill_prob == null ? "–" : `${Math.round(r.skill_prob * 100)}%`}</td>
                      <td>{r.group === "Benchmark" ? "" : r.robust ? <span className="up">✓</span> : <span className="dim">–</span>}</td>
                      <td>{r.group !== "Benchmark" && !(brain.on && isLive) && <button className={r.robust ? "mini primary" : "mini"} onClick={(e) => { e.stopPropagation(); useLive(r); }}>trade this live</button>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

const VERDICT = { pattern: ["up", "REAL PATTERN"], hint: ["warn", "hint"], chance: ["dim", "chance"], "no data": ["dim", "no data yet"] };

function PatternHunt({ p, openAgent }) {
  const rows = p?.rows || [];
  const real = rows.filter((r) => r.verdict === "pattern");
  return (
    <section className="panel patterns-panel">
      <div className="row-head">
        <h3>PATTERN HUNT <span className="dim">· does any free signal predict next week's price? · by the <button className="mini linkish" onClick={() => openAgent("patterns")}>Pattern Hunter</button></span></h3>
        {p?.ts && <span className="dim small">{p.from} to {p.to} · {ago(p.ts)}</span>}
      </div>
      <p className="dim small">Every idea is measured the same honest way: only data known on the day, the outcome is the return over the next {p?.horizon_days ?? 7} days, weeks don't overlap, and a signal only counts if it survives the correction for testing {p?.tested ?? "many"} ideas at once, points the same way in both halves of history and in most years. Two random numbers run along as controls: if they ever "win", the test is broken. That's how a pizza index or flight counts would be judged too: without a reason why they should move prices, a match is almost always chance.</p>
      {p?.simulated && <p className="err-msg">Simulated data: not real findings.</p>}
      {!rows.length ? <div className="empty">Runs with the history test (tonight, or "run again" above).</div> : (
        <>
          <p className={real.length ? "ok-msg" : "dim"}>{real.length ? `${real.length} real pattern(s): ${real.map((r) => r.name).join(", ")}.` : "No signal passes every check right now. That's the normal, honest result: most ideas are noise. Hints are watched but not traded."}</p>
          <div className="scroll">
            <table className="board">
              <thead><tr><th>signal</th><th>verdict</th><th>direction</th><th title="rank correlation with next week's return">corr</th><th title="chance of a result this strong by luck, after correcting for the number of signals">p corrected</th><th>halves</th><th>years same way</th><th title="top fifth minus bottom fifth, return per week">spread/wk</th><th>weeks</th><th>source</th></tr></thead>
              <tbody>
                {rows.map((r) => {
                  const [tone, label] = VERDICT[r.verdict] || ["dim", r.verdict];
                  return (
                    <tr key={`${r.name}-${r.kind}`} title={r.idea}>
                      <td><b>{r.name}</b><div className="dim small">{r.idea}</div></td>
                      <td><span className={`st-chip ${tone}`}>{label}</span></td>
                      <td className="small">{r.direction || "–"}</td>
                      <td className={pctColor(r.corr)}>{r.corr ?? "–"}</td>
                      <td>{r.p_corrected ?? "–"}</td>
                      <td className="small">{r.first_half == null ? "–" : `${r.first_half} / ${r.second_half}`}</td>
                      <td>{r.years_total ? `${r.years_same}/${r.years_total}` : "–"}</td>
                      <td className={pctColor(r.spread_pct)}>{r.spread_pct == null ? "–" : `${r.spread_pct > 0 ? "+" : ""}${r.spread_pct}%`}</td>
                      <td>{r.weeks}</td>
                      <td className="dim small">{r.source}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

function FreeData({ node, openAgent }) {
  const t = node?.detail?.table;
  return (
    <section className="panel">
      <div className="row-head">
        <h3>FREE DATA <span className="dim">· years of daily history, no keys · collected by the <button className="mini linkish" onClick={() => openAgent("collector")}>Data Collector</button></span></h3>
        <span className="dim small">{node?.summary}</span>
      </div>
      <p className="dim small">Fear & Greed (alternative.me), perpetual futures funding per coin (Binance), Wikipedia page views (Wikimedia), total stablecoin supply (DefiLlama) and Bitcoin's hash rate (blockchain.com). These feed the Pattern Hunter and three alternative-data strategies in the history test. They only steer real money through a strategy that passes the test.</p>
      {t?.rows?.length ? (
        <div className="scroll" style={{ maxHeight: 320 }}>
          <table className="board">
            <thead><tr>{t.cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>{t.rows.map((r, i) => <tr key={i}>{r.map((v, k) => <td key={k}>{v ?? "–"}</td>)}</tr>)}</tbody>
          </table>
        </div>
      ) : <div className="empty small">Downloads on the first history test (needs internet on your Mac).</div>}
    </section>
  );
}
