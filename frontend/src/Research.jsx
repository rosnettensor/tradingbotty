import { useEffect, useState } from "react";
import { LineChart } from "./charts.jsx";
import { Tabs } from "./components.jsx";
import { ago, api, pctColor, usePoll } from "./useBot.js";

const COLORS = ["#00f0ff", "#ff2bd6", "#39ff88", "#ffb020", "#8a5cff", "#ff6b3d", "#4da3ff", "#e8ff3d"];
const PERIODS = [["full", "all history"], ["last_2y", "2 years"], ["last_1y", "1 year"], ["last_6m", "6 months"], ["first_half", "1st half"], ["second_half", "2nd half"]];
const dayFmt = (ts) => new Date(ts * 1000).toLocaleDateString([], { month: "short", year: "2-digit" });

const LABS = [["daily", "DAILY BRAIN LAB · the real money"], ["fast", "FAST TRADER LAB · speculative, 4-hour"]];

export default function Research({ state, openAgent }) {
  const [res, setRes] = useState(null);
  const [pat, reloadPat] = usePoll("patterns", 0);
  const [lab, setLab] = useState(() => { try { return localStorage.getItem("tb-lab") || "daily"; } catch { return "daily"; } });
  const pickLab = (k) => { setLab(k); try { localStorage.setItem("tb-lab", k); } catch { /* private window */ } };
  useEffect(() => { api("research").then((r) => r && r.rows && setRes(r)).catch(() => {}); }, [state.research?.ts]);
  return (
    <div className="research-tab">
      <div className="lab-switch"><Tabs value={lab} options={LABS} onChange={pickLab} /></div>
      {lab === "fast" ? <FastLab state={state} openAgent={openAgent} /> : (
        <>
          <HistoryTest state={state} res={res} setRes={(r) => { setRes(r); reloadPat(); }} openAgent={openAgent} />
          {res && <RealityChecks res={res} live={state.brain?.strategy} />}
          <PatternHunt p={pat} openAgent={openAgent} />
          <FreeData node={(state.nodes || []).find((n) => n.id === "collector")} openAgent={openAgent} />
        </>
      )}
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
    points: r.curve.map((v, k) => [t0 + k * (res.curve_step_days ?? res.curve_step ?? 3) * 86400, (v - 1) * 100]) }));
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
              <thead><tr><th>strategy</th><th>return</th><th>per year</th><th>worst drop</th><th>sharpe</th><th>trades</th><th title="fees and spread paid per year, as a share of the account">fees/yr</th><th title="per year if every trade cost twice as much: wider spreads, worse fills">at 2x fees</th><th>in coins</th><th title="calendar years in which it beat holding Bitcoin, risk-adjusted">years won</th><th title="deflated Sharpe: chance the result is skill, not luck">skill</th><th>robust</th><th /></tr></thead>
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
                      <td className={pctColor(r.fees2x?.cagr_pct)}>{r.fees2x?.cagr_pct == null ? "–" : `${r.fees2x.cagr_pct}%`}</td>
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

function PatternHunt({ p, openAgent, fast = false }) {
  const rows = p?.rows || [];
  const real = rows.filter((r) => r.verdict === "pattern");
  const unit = fast ? "day" : "week";
  return (
    <section className="panel patterns-panel">
      <div className="row-head">
        {fast
          ? <h3>FAST SIGNALS <span className="dim">· which signal says something about the next 24 hours? Classic, volume, Bitcoin lead-lag and a few new ideas</span></h3>
          : <h3>PATTERN HUNT <span className="dim">· does any free signal predict next week's price? · by the <button className="mini linkish" onClick={() => openAgent("patterns")}>Pattern Hunter</button></span></h3>}
        {p?.ts && <span className="dim small">{p.from} to {p.to} · {ago(p.ts)}</span>}
      </div>
      {fast ? <p className="dim small">Same honest test as the Pattern Hunt, on 4-hour candles: only what is known at a bar's close, the outcome is the next 24 hours, days don't overlap, corrected for testing {p?.tested ?? "many"} signals at once, same direction in both halves and in most years, with two random controls. Per-coin signals compare coins with each other on the same day; market signals (Bitcoin lead-lag, breadth, weekend) are judged against the average altcoin's next day. A real pattern here is a candidate rule for a fast strategy, not a trade by itself.</p> : (
      <p className="dim small">Every idea is measured the same honest way: only data known on the day, the outcome is the return over the next {p?.horizon_days ?? 7} days, weeks don't overlap, and a signal only counts if it survives the correction for testing {p?.tested ?? "many"} ideas at once, points the same way in both halves of history and in most years. Two random numbers run along as controls: if they ever "win", the test is broken. That's how a pizza index or flight counts would be judged too: without a reason why they should move prices, a match is almost always chance.</p>)}
      {p?.simulated && <p className="err-msg">Simulated data: not real findings.</p>}
      {!rows.length ? <div className="empty">{fast ? "Runs with the fast lab." : "Runs with the history test (tonight, or \"run again\" above)."}</div> : (
        <>
          <p className={real.length ? "ok-msg" : "dim"}>{real.length ? `${real.length} real pattern(s): ${real.map((r) => r.name).join(", ")}.` : "No signal passes every check right now. That's the normal, honest result: most ideas are noise. Hints are watched but not traded."}</p>
          <div className="scroll">
            <table className="board">
              <thead><tr><th>signal</th><th>verdict</th><th>direction</th><th title={`rank correlation with the next ${unit}'s return`}>corr</th><th title="chance of a result this strong by luck, after correcting for the number of signals">p corrected</th><th>halves</th><th>years same way</th><th title={`top fifth minus bottom fifth, return per ${unit}`}>spread/{fast ? "day" : "wk"}</th><th>{unit}s</th><th>source</th></tr></thead>
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

function RealityChecks({ res, live, title = "REALITY CHECKS" }) {
  const btc = res.rows.find((r) => r.name === "Hold Bitcoin");
  const mine = res.rows.find((r) => r.name === live);
  const wf = res.walk_forward;
  const luck = res.coin_luck || [];
  const pc = (x) => (x == null ? "–" : `${x > 0 ? "+" : ""}${x}%`);
  return (
    <section className="panel reality">
      <h3>{title} <span className="dim">· would it have worked if we had used it back then?</span></h3>
      <div className="reality-grid">
        {mine && btc && (
          <div>
            <h4>LIVE STRATEGY, YEAR BY YEAR</h4>
            <p className="dim small">{mine.name} vs holding Bitcoin, return in each calendar year.</p>
            <table className="board">
              <thead><tr><th>year</th><th>live strategy</th><th>Bitcoin</th><th>worst drop</th></tr></thead>
              <tbody>
                {Object.keys(mine.years).map((y) => (
                  <tr key={y}>
                    <td>{y}</td>
                    <td className={pctColor(mine.years[y].return_pct)}><b>{pc(mine.years[y].return_pct)}</b></td>
                    <td className={pctColor(btc.years[y]?.return_pct)}>{pc(btc.years[y]?.return_pct)}</td>
                    <td className="down">{mine.years[y].max_dd_pct}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {wf && (
          <div>
            <h4>CHASING THE LEADER (WALK-FORWARD)</h4>
            <p className="dim small">At the start of each year, switch to whatever looked best on all the years before, using only the past. If this beats sticking with one strategy, chasing the top of the list pays; if not, the list's leader is partly luck.</p>
            <table className="board">
              <thead><tr><th>year</th><th>picked from the past</th><th>its year</th>{wf.live && <th>live strategy</th>}<th>Bitcoin</th></tr></thead>
              <tbody>
                {wf.years.map((y) => (
                  <tr key={y.year}>
                    <td>{y.year}</td>
                    <td className="small" style={{ whiteSpace: "normal" }}>{y.pick}</td>
                    <td className={pctColor(y.pick_ret)}>{pc(y.pick_ret)}</td>
                    {wf.live && <td className={pctColor(y.live_ret)}>{pc(y.live_ret)}</td>}
                    <td className={pctColor(y.btc_ret)}>{pc(y.btc_ret)}</td>
                  </tr>
                ))}
                <tr className="champ">
                  <td><b>per year</b></td><td className="small">{wf.switches} switches</td>
                  <td className={pctColor(wf.pick_cagr)}><b>{pc(wf.pick_cagr)}</b></td>
                  {wf.live && <td className={pctColor(wf.live_cagr)}><b>{pc(wf.live_cagr)}</b></td>}
                  <td className={pctColor(wf.btc_cagr)}><b>{pc(wf.btc_cagr)}</b></td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
        {luck.length > 0 && (
          <div>
            <h4>COIN LUCK</h4>
            <p className="dim small">{luck.some((l) => !res.rows.find((r) => r.name === l.name)?.robust) ? "Nothing passed every check, so this shows the best-looking ones. " : ""}Each strategy re-run {luck[0].rounds} times on a random two-thirds of the coins. A real edge keeps working whichever coins are missing; one that only won thanks to a few lucky coins collapses here.</p>
            <table className="board">
              <thead><tr><th>strategy</th><th>all coins</th><th>median</th><th>worst</th><th>worst drop</th><th>positive</th></tr></thead>
              <tbody>
                {luck.map((l) => {
                  const full = res.rows.find((r) => r.name === l.name)?.full?.cagr_pct;
                  return (
                    <tr key={l.name} className={l.name === live ? "live-strat" : ""}>
                      <td className="small" style={{ whiteSpace: "normal" }}><b>{l.name}</b></td>
                      <td>{pc(full)}</td>
                      <td className={pctColor(l.median_cagr)}><b>{pc(l.median_cagr)}</b></td>
                      <td className={pctColor(l.worst_cagr)}>{pc(l.worst_cagr)}</td>
                      <td className="down">{l.worst_dd}%</td>
                      <td>{l.beat_zero}/{l.rounds}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="dim small">Numbers are per year. Remember: the coins are today's survivors, so all of these are rosier than the future will be.</p>
          </div>
        )}
      </div>
    </section>
  );
}


function FastLab({ state, openAgent }) {
  const [res, reload] = usePoll("fastlab", 0);
  const [stat] = usePoll("fastlab/status", 3000);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const running = !!stat?.running;
  useEffect(() => { if (stat?.finished) reload(); }, [stat?.finished]);
  const [period, setPeriod] = useState("full");
  const [group, setGroup] = useState("all");
  const [pick, setPick] = useState([]);
  const run = async () => {
    setBusy(true); setMsg("");
    try { const r = await api("fastlab/run", {}); if (!r.busy) reload(); }
    catch (e) { setMsg(e.message); } finally { setBusy(false); }
  };
  const has = res && res.rows;
  const all = has ? res.rows : [];
  const btc = all.find((r) => r.group === "Benchmark");
  const slow = all.find((r) => r.group.startsWith("Slow"));
  const fast = all.filter((r) => r.group.startsWith("Fast"));
  const robust = fast.filter((r) => r.robust);
  const best = [...fast].sort((a, b) => (b.full?.sharpe ?? -9) - (a.full?.sharpe ?? -9))[0];
  const groups = ["all", ...new Set(fast.map((r) => r.group))];
  const rows = all.filter((r) => group === "all" || r.group === group || !r.group.startsWith("Fast"))
    .sort((a, b) => (b.robust - a.robust) || ((b[period]?.sharpe ?? -9) - (a[period]?.sharpe ?? -9)));
  const shown = has ? [btc, slow, ...(pick.length ? fast.filter((r) => pick.includes(r.name)) : [best])].filter(Boolean) : [];
  const t0 = has ? Date.parse(res.from) / 1000 : 0;
  const series = shown.map((r, i) => ({ id: r.name, name: r.name, color: COLORS[i % COLORS.length], bold: r === best,
    points: r.curve.map((v, k) => [t0 + k * (res.curve_step_days ?? 1) * 86400, (v - 1) * 100]) }));
  const toggle = (name) => setPick((p) => (p.includes(name) ? p.filter((x) => x !== name) : [...p, name]));
  const pc = (x) => (x == null ? "–" : `${x > 0 ? "+" : ""}${x}%`);
  const pot = state.fast || {};
  const usePot = async (r) => {
    try { await api("fast", { strategy: r.name }); setMsg(`"${r.name}" now drives the fast pot.`); } catch (e) { setMsg(e.message); }
  };
  return (
    <>
      <FastPot state={state} lab={res} openAgent={openAgent} />
      <section className="panel lab research fastlab">
        <div className="row-head">
          <h3>FAST TRADER LAB <span className="dim">· a quick, speculative trader for its own pot, tested before it gets real money · by the <button className="mini linkish" onClick={() => openAgent("researcher")}>Researcher</button></span></h3>
          <div className="row-tools">
            {has && <Tabs value={period} options={PERIODS} onChange={setPeriod} />}
            <button className="primary" onClick={run} disabled={busy || running}>{busy || running ? "running…" : has ? "run again" : "run the fast lab"}</button>
          </div>
        </div>
        <p className="dim small">4-hour candles of the {res?.coins?.length ?? 40} most traded coins that Fusion also lists, up to 3 years back. Every 4 hours each rule decides on the close and trades at the next open, paying {res?.cost_per_side_pct ?? 0.5}% per buy or sell (Fusion's fee plus a wider spread for smaller coins). Breakouts with profit-taking, pump riding, dip buying, with and without a volume check, against holding Bitcoin and against the daily brain's slow rules on the same coins. Same robustness bar as the daily lab. <b>Research only:</b> nothing here touches your money yet.</p>
        {msg && <p className={/drives the fast pot/.test(msg) ? "ok-msg" : "err-msg"}>{msg}</p>}
        {running && (
          <div className="lab-progress">
            <span className="pulse-dot" /> <b>Running:</b> {stat.step}{stat.total ? ` (${stat.done} of ${stat.total} coins)` : ""} · started {ago(stat.started)}
            {stat.total > 0 && <div className="bar"><span style={{ width: `${Math.round((stat.done / stat.total) * 100)}%` }} /></div>}
            <div className="dim small">The results appear here by themselves when it is done.</div>
          </div>
        )}
        {!running && stat?.error && <p className="err-msg">The last run failed: {stat.error}</p>}
        {res?.simulated && <p className="err-msg">Simulated prices (simulate mode): these numbers are not real.</p>}
        {!has ? <div className="empty">Not run yet. It runs nightly after the history test, or press "run the fast lab" (the first run downloads about 40 coins × 3 years of 4-hour candles, a few minutes).</div> : (
          <>
            <div className={robust.length ? "ok-msg" : "verdict-box"}>
              {robust.length
                ? <>{robust.length} fast rule(s) pass every check. Best: <b>{robust[0].name}</b>, {pc(robust[0].full.cagr_pct)}/yr, worst drop {robust[0].full.max_dd_pct}%, {robust[0].fees_pct}% of the pot in fees per year.</>
                : <>No fast rule passes every check yet. Best-looking: <b>{best?.name}</b> {pc(best?.full?.cagr_pct)}/yr (worst drop {best?.full?.max_dd_pct}%, fees {best?.fees_pct}%/yr, skill {best?.skill_prob == null ? "–" : Math.round(best.skill_prob * 100)}%).</>}
              {" "}For comparison: holding Bitcoin {pc(btc?.full?.cagr_pct)}/yr, the daily brain's rules on these coins {pc(slow?.full?.cagr_pct)}/yr.
            </div>
            <p className="dim small">{res.from} to {res.to} · {Math.round(res.days / (res.bars_per_day || 6))} days · {res.coins.length} coins: {res.coins.join(", ")} · tested {ago(res.ts)}</p>
            <div className="legend small">{series.map((x) => <span key={x.id} style={{ color: x.color, marginRight: 16 }}>■ {x.name}</span>)}</div>
            <LineChart series={series} height={220} unit="%" baseline={0} xfmt={dayFmt} />
            <div className="row-head" style={{ marginTop: 8 }}>
              <span className="dim small">{robust.length} robust of {fast.length} fast rules · click rows to compare</span>
              <Tabs value={group} options={groups.map((g) => [g, g === "all" ? "all" : g.replace("Fast: ", "")])} onChange={setGroup} />
            </div>
            <div className="scroll">
              <table className="board">
                <thead><tr><th>rule</th><th>return</th><th>per year</th><th>worst drop</th><th>sharpe</th><th>trades</th><th>fees/yr</th><th>at 2x fees</th><th>in coins</th><th>years won</th><th>skill</th><th>robust</th><th /></tr></thead>
                <tbody>
                  {rows.map((r) => {
                    const st = r[period] || {};
                    const fastRow = r.group.startsWith("Fast");
                    const inPot = pot.strategy === r.name;
                    return (
                      <tr key={r.name} className={`${!fastRow ? "champ" : ""} ${pick.includes(r.name) ? "picked" : ""}`} onClick={() => fastRow && toggle(r.name)} title={r.explain}>
                        <td><b>{r.name}</b><div className="dim small">{r.group}</div></td>
                        <td className={pctColor(st.return_pct)}>{pc(st.return_pct)}</td>
                        <td>{st.cagr_pct == null ? "–" : `${st.cagr_pct}%`}</td>
                        <td className="down">{st.max_dd_pct}%</td>
                        <td>{st.sharpe}</td>
                        <td>{r.trades}</td>
                        <td>{r.fees_pct}%</td>
                        <td className={pctColor(r.fees2x?.cagr_pct)}>{r.fees2x?.cagr_pct == null ? "–" : `${r.fees2x.cagr_pct}%`}</td>
                        <td>{r.invested_pct}%</td>
                        <td>{r.group === "Benchmark" ? "" : `${r.years_won ?? "–"}/${r.years_total ?? "–"}`}</td>
                        <td>{r.skill_prob == null ? "–" : `${Math.round(r.skill_prob * 100)}%`}</td>
                        <td>{r.group === "Benchmark" ? "" : r.robust ? <span className="up">✓</span> : <span className="dim">–</span>}</td>
                        <td>{fastRow && (inPot ? <span className="live-badge">⚡ POT</span>
                          : <button className="mini" onClick={(e) => { e.stopPropagation(); usePot(r); }}>use in fast pot</button>)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
      {has && <RealityChecks res={res} live={null} title="FAST LAB REALITY CHECKS" />}
      {has && <PatternHunt p={res.patterns} openAgent={openAgent} fast />}
      {has && res.correlation && <BtcLinks c={res.correlation} />}
    </>
  );
}

function BtcLinks({ c }) {
  const pts = (c.series || []).map((x) => [Date.parse(x.date) / 1000, x.avg_corr * 100]);
  const disp = (c.series || []).map((x) => [Date.parse(x.date) / 1000, x.dispersion_pct]);
  const coins = c.coins || [];
  const lone = coins.filter((x) => x.change != null && x.change <= -0.2);
  return (
    <section className="panel">
      <h3>BITCOIN LINKS OVER TIME <span className="dim">· how tightly the altcoins follow Bitcoin, and who is breaking away</span></h3>
      <p className="dim small">Correlation 100 = moves exactly with Bitcoin, 0 = its own way. When everything follows Bitcoin, picking coins hardly matters and Bitcoin's direction decides. When links loosen and coins move differently (dispersion), a fast coin-picker has more to work with. Coins whose link dropped a lot in the last 30 days have their own story right now, good or bad.</p>
      <div className="reality-grid">
        <div>
          <h4>AVERAGE ALTCOIN LINK TO BITCOIN, PER MONTH</h4>
          <div className="legend small"><span style={{ color: COLORS[0], marginRight: 16 }}>■ correlation ×100</span><span style={{ color: COLORS[3] }}>■ dispersion, % per day</span></div>
          <LineChart series={[{ id: "c", name: "correlation", color: COLORS[0], bold: true, points: pts }, { id: "d", name: "dispersion", color: COLORS[3], points: disp }]} height={200} baseline={0} xfmt={dayFmt} />
          {lone.length > 0 && <p className="small">Breaking away now: {lone.map((x) => <b key={x.coin} style={{ marginRight: 8 }}>{x.coin} ({x.corr_all} → {x.corr_30d})</b>)}</p>}
        </div>
        <div>
          <h4>EACH COIN, LAST 30 DAYS VS THE WHOLE PERIOD</h4>
          <div className="scroll" style={{ maxHeight: 300 }}>
            <table className="board">
              <thead><tr><th>coin</th><th title="correlation with Bitcoin's 4-hour moves, last 30 days">link 30d</th><th>link all</th><th>change</th><th title="how much it swings when Bitcoin moves 1%">beta 30d</th><th>30d move</th><th title="trading volume of the last day vs the week before">volume now</th></tr></thead>
              <tbody>
                {coins.map((x) => (
                  <tr key={x.coin}>
                    <td><b>{x.coin}</b></td>
                    <td>{x.corr_30d}</td>
                    <td className="dim">{x.corr_all ?? "–"}</td>
                    <td className={x.change <= -0.2 ? "warn" : x.change >= 0.2 ? "up" : "dim"}>{x.change == null ? "–" : `${x.change > 0 ? "+" : ""}${x.change}`}</td>
                    <td>{x.beta_30d}</td>
                    <td className={pctColor(x.move_30d_pct)}>{x.move_30d_pct == null ? "–" : `${x.move_30d_pct > 0 ? "+" : ""}${x.move_30d_pct}%`}</td>
                    <td className={x.vol_surge >= 2 ? "up" : "dim"}>{x.vol_surge == null ? "–" : `${x.vol_surge}x`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}

function FastPot({ state, lab, openAgent }) {
  const f = state.fast || {};
  const w = state.wallet || {};
  const cur = w.currency || "CHF";
  const [mode, setMode] = useState(f.mode || "chf");
  const [chf, setChf] = useState(f.chf ?? 40);
  const [pct, setPct] = useState(f.pct ?? 13);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (f.mode) { setMode(f.mode); setChf(f.chf); setPct(f.pct); } }, [f.mode, f.chf, f.pct]);
  const total = w.total || 0;
  const pot = mode === "chf" ? Math.max(0, Number(chf) + (f.realized || 0)) : (total * pct) / 100;
  const slots = Math.floor(pot / (f.min_slot || 40));
  const row = (lab?.rows || []).find((r) => r.name === f.strategy);
  const save = async (body) => {
    setMsg("");
    try { await api("fast", body); } catch (e) { setMsg(e.message); }
  };
  const toggle = async () => {
    if (!f.on) {
      const warn = row && !row.robust ? "\n\nThis rule does NOT pass every check: treat it as play money." : "";
      if (!window.confirm(`Give the fast pot ${pot.toFixed(2)} ${cur} of REAL money?\n\nRule: ${f.strategy}\nIt decides every 4 hours and may buy wild coins. It never sells your coins or the Daily Brain's, and the brain leaves this money alone. Worst case: the pot goes to zero, never more.${warn}`)) return;
    }
    setBusy(true);
    await save({ on: !f.on, mode, chf: Number(chf), pct: Number(pct) });
    setBusy(false);
  };
  const close = async () => {
    if (!window.confirm("Sell every coin of the fast pot now, at market price?")) return;
    setBusy(true);
    try { const r = await api("fast/close", {}); setMsg(r.done?.join("; ") || "nothing to sell"); } catch (e) { setMsg(e.message); }
    setBusy(false);
  };
  const pos = Object.entries(f.pos || {});
  const coins = Object.fromEntries((w.fast_coins || []).map((c) => [c.symbol, c]));
  return (
    <section className={`panel fastpot ${f.on ? "on" : ""}`}>
      <div className="row-head">
        <h3>⚡ FAST POT · REAL MONEY <span className="dim">· one fast rule, its own money and coins, next to the Daily Brain · <button className="mini linkish" onClick={() => openAgent("fast")}>Fast Trader</button></span></h3>
        <div className="row-tools">
          {pos.length > 0 && <button onClick={close} disabled={busy}>sell the pot's coins now</button>}
          <button className={f.on ? "danger" : "primary"} onClick={toggle} disabled={busy || (!f.on && slots < 1)}>{f.on ? "switch off" : "switch on with real money"}</button>
        </div>
      </div>
      <div className="fastpot-grid">
        <div>
          <div className="dim small">RULE</div>
          <div><b>{f.strategy}</b> {row ? (row.robust ? <span className="up">✓ passes every check</span> : <span className="play">play money: fails a check</span>) : ""}</div>
          {row && <div className="dim small">in the test: {row.full?.cagr_pct}%/yr, worst drop {row.full?.max_dd_pct}%, {row.trades} trades, fees {row.fees_pct}%/yr · pick another with "use in fast pot" below</div>}
        </div>
        <div>
          <div className="dim small">POT SIZE</div>
          <Tabs value={mode} options={[["chf", `fixed ${cur}`], ["pct", "% of account"]]} onChange={(m) => { setMode(m); save({ mode: m }); }} />
          {mode === "chf" ? (
            <div className="pot-input"><input type="range" min="0" max="500" step="5" value={chf} onChange={(e) => setChf(e.target.value)} onMouseUp={() => save({ chf: Number(chf) })} onTouchEnd={() => save({ chf: Number(chf) })} />
              <input type="number" min="0" step="5" value={chf} onChange={(e) => setChf(e.target.value)} onBlur={() => save({ chf: Number(chf) })} /> {cur}</div>
          ) : (
            <div className="pot-input"><input type="range" min="0" max="100" step="1" value={pct} onChange={(e) => setPct(e.target.value)} onMouseUp={() => save({ pct: Number(pct) })} onTouchEnd={() => save({ pct: Number(pct) })} />
              <input type="number" min="0" max="100" step="1" value={pct} onChange={(e) => setPct(e.target.value)} onBlur={() => save({ pct: Number(pct) })} /> %</div>
          )}
          <div className={slots < 1 ? "err-msg" : "dim small"}>= {pot.toFixed(2)} {cur}{mode === "chf" && f.realized ? ` (incl. ${f.realized >= 0 ? "+" : ""}${f.realized.toFixed(2)} won or lost so far)` : ""} · {slots < 1 ? `too small: each coin needs at least ${f.min_slot || 40} ${cur} (Fusion's 25 minimum plus room to sell after a drop)` : `${Math.min(slots, 2)} coin${Math.min(slots, 2) === 1 ? "" : "s"} at a time`}</div>
        </div>
        <div>
          <div className="dim small">STATUS</div>
          <div><b className={f.on ? "up" : "dim"}>{f.on ? (state.mode === "live" ? "ON · trading" : "ON · but LIVE is off") : "OFF"}</b> · booked <span className={f.realized >= 0 ? "up" : "down"}>{f.realized >= 0 ? "+" : ""}{(f.realized || 0).toFixed(2)} {cur}</span> · {f.trades || 0} trades{f.trades ? ` (${f.wins} won)` : ""}</div>
          <div className="dim small">{f.on && f.next ? `next look ${new Date(f.next * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} (every 4 hours, after each candle)` : "decides 2 minutes after each 4-hour candle closes"}</div>
          {pos.map(([s, p]) => <div key={s} className="small">⚡ <b>{s}</b> in at {p.entry} for {p.cost?.toFixed(2)} {cur}{coins[s] ? <> · now {coins[s].value?.toFixed(2)} (<span className={coins[s].pnl >= 0 ? "up" : "down"}>{coins[s].pnl >= 0 ? "+" : ""}{coins[s].pnl?.toFixed(2)}</span>)</> : ""}</div>)}
        </div>
      </div>
      {msg && <p className="err-msg">{msg}</p>}
      {f.steps?.length > 0 && <ol className="steps small">{f.steps.map((x, i) => <li key={i}>{x}</li>)}</ol>}
    </section>
  );
}
