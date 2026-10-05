import { useEffect, useMemo, useRef, useState } from "react";
import Sphere from "./Sphere.jsx";
import { DailyChart, LineChart } from "./charts.jsx";
import { Stat, Tabs } from "./components.jsx";
import { ago, api, fmt, pctColor, usePoll } from "./useBot.js";

const hm = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const dayhm = (ts) => new Date(ts * 1000).toLocaleString([], { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const nextMidnightUTC = () => (Math.floor(Date.now() / 86400000) + 1) * 86400;

// Everything the live core shows, from real data (see Sphere.jsx for the layers)
function coreData(state, w, energy) {
  const tick = state.ticker || [];
  const ch = tick.map((t) => t.change).filter((x) => x != null);
  const market = ch.length ? ch.reduce((a, b) => a + b, 0) / ch.length : 0;
  const breadth = ch.length ? ch.filter((x) => x > 0).length / ch.length : null;
  const held = (w?.coins || []).filter((c) => c.value > 0.5);
  const spikes = held.map((c) => {
    const pct = c.cost ? (c.value / c.cost - 1) * 100 : 0;
    return { symbol: c.symbol, kind: "held", pct, len: Math.min(1.6, 0.35 + Math.abs(pct) / 12), text: `${c.symbol} ${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%` };
  });
  for (const c of (w?.fast_coins || []).filter((c) => c.value > 0.5)) {
    const pct = c.cost ? (c.value / c.cost - 1) * 100 : 0;
    spikes.push({ symbol: `fast:${c.symbol}`, kind: "held", pct, len: Math.min(1.6, 0.35 + Math.abs(pct) / 8), text: `⚡${c.symbol} ${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%` });
  }
  const mine = new Set([...held, ...(w?.fast_coins || [])].map((c) => c.symbol));
  for (const r of (state.trend?.rows || []).filter((r) => !mine.has(r.symbol)).slice(0, 14)) {
    const gap = r.to_breakout_pct ?? 30;
    const close = Math.max(0, 1 - Math.max(0, gap) / 25);
    const hot = ["would buy", "breakout, no slot", "near breakout", "would sell"].includes(r.state);
    spikes.push({ symbol: r.symbol, kind: "watch", state: r.state, len: 0.08 + close * 0.55,
      text: hot ? `${r.symbol} ${r.state === "would buy" ? "BUY TONIGHT" : gap <= 0 ? "at its high" : `${gap.toFixed(1)}% to buy`}` : "" });
  }
  const edge = w ? w.bot_edge_pct ?? w.change_pct : 0;
  const cashShare = w && w.total ? (w.fiat || 0) / w.total : null;
  const fast = state.fast || {};
  const pc = (x) => `${x >= 0 ? "+" : ""}${(x || 0).toFixed(2)}%`;
  const btc = state.trend?.btc || {};
  const hud = {
    left: [["MARKET TODAY", pc(market), market >= 0 ? "up" : "down"], ["COINS UP 24H", breadth == null ? "–" : `${Math.round(breadth * 100)}%`, breadth >= 0.5 ? "up" : "down"],
      ["CASH", cashShare == null ? "–" : `${Math.round(cashShare * 100)}%`, "cyan"], ["BOT'S OWN", pc(edge), edge >= 0 ? "up" : "down"]],
    right: [["BITCOIN FILTER", btc.ok == null ? "–" : btc.ok ? "OPEN" : "CASH", btc.ok ? "up" : "down"], ["DAILY BRAIN", `${held.length} coin${held.length === 1 ? "" : "s"}`, held.length ? "up" : "dim"],
      ["FAST POT", fast.on ? `${(fast.pot || 0).toFixed(0)} ${w?.currency || ""}` : "off", fast.on ? "magenta" : "dim"], ["GUARDIAN", Object.keys(state.guard || {}).length ? `${Object.keys(state.guard).length} blocked` : "clear", Object.keys(state.guard || {}).length ? "down" : "up"]],
  };
  return {
    mood: edge, market, breadth, energy, cashShare, spikes, hud,
    btcOk: state.trend?.btc?.ok ?? state.brain?.btc_ok ?? null, btcGap: state.trend?.btc?.gap_pct ?? 0,
    guard: Object.keys(state.guard || {}).length, lastSide: (state.trades || [])[0]?.side,
    facts: `Right now: bot ${edge >= 0 ? "+" : ""}${(edge || 0).toFixed(2)}% · market ${market >= 0 ? "+" : ""}${market.toFixed(2)}% today · ${breadth == null ? "?" : Math.round(breadth * 100)}% of coins up · Bitcoin filter ${state.trend?.btc?.ok ? "open" : "closed"} · cash ${cashShare == null ? "?" : Math.round(cashShare * 100)}% · ${held.length} bot coins · ${Object.keys(state.guard || {}).length} Guardian blocks`,
  };
}

export default function Cockpit({ state, pulse, focus, setFocus, openAgent }) {
  const w = state.wallet && !state.wallet.error ? state.wallet : null;
  const rows = state.trend?.rows || [];
  const near = rows.filter((r) => ["would buy", "breakout, no slot", "near breakout"].includes(r.state)).length;
  const energy = Math.min(1, 0.15 + near / 6 + Object.keys(state.guard || {}).length / 4);
  const symbol = focus && rows.some((r) => r.symbol === focus) ? focus : rows[0]?.symbol || "BTC";

  return (
    <div className="cockpit3">
      {state.wallet ? <LiveWallet state={state} setFocus={setFocus} /> : <NoWallet />}
      <BrainBar state={state} openAgent={openAgent} />
      <FastPotCard state={state} openAgent={openAgent} />
      <StatStrip state={state} openAgent={openAgent} />

      <section className="panel core">
        <Sphere data={coreData(state, w, energy)} pulse={pulse} label={w ? (
          <div className="core-label">
            <div className="dim">YOUR ACCOUNT · {state.mode === "live" ? "LIVE" : "STANDBY"}</div>
            <div className="equity">{w.total.toFixed(2)} {w.currency}</div>
            <div className={pctColor(w.change_pct)}>{fmt.pct(w.change_pct)} since start</div>
          </div>
        ) : <div className="core-label"><div className="dim">NO ACCOUNT CONNECTED</div></div>} />
        <Professor p={state.professor || {}} on={state.ai} openAgent={openAgent} />
      </section>

      <section className="panel watch">
        <TrendWatch t={state.trend} brain={state.brain} guard={state.guard || {}} symbol={symbol} setFocus={setFocus} openAgent={openAgent} />
      </section>

      <section className="panel guardpanel">
        <Guardian guard={state.guard || {}} shocks={state.shocks || {}} openAgent={openAgent} />
      </section>

      <section className="panel focus">
        <FocusDaily symbol={symbol} state={state} />
      </section>

      <section className="panel equity-panel">
        {w ? <>
          <h3>YOUR ACCOUNT ({w.currency}) <span className="dim">· real money · dashed line = where it started</span></h3>
          <LineChart series={[{ id: "a", color: "var(--magenta)", bold: true, points: [...(w.history || []).map((h) => [h[0], h[1]]), [Date.now() / 1000, w.total]] }]}
            baseline={w.start_total} height={200} />
        </> : <div className="empty">your account chart appears once the Fusion key works</div>}
      </section>

      <section className="panel positions">
        <LiveTrades trades={state.trades || []} cur={w?.currency || "CHF"} setFocus={setFocus} />
      </section>

      <section className="panel log">
        <Feed log={state.log || []} />
      </section>

      <section className="panel newsfeed">
        <News news={state.news || []} setFocus={setFocus} />
      </section>
    </div>
  );
}

function NoWallet() {
  return (
    <section className="panel wallet">
      <h3>BITPANDA FUSION</h3>
      <p className="dim">No Fusion key yet: the bot can't see or trade your account. Add BITPANDA_FUSION_API_KEY to .env and restart.</p>
    </section>
  );
}

/** A number that rolls to its new value and flashes green or red when it changes. */
function Ticking({ value, digits = 2, signed = false }) {
  const [shown, setShown] = useState(value ?? 0);
  const [flash, setFlash] = useState("");
  const prev = useRef(value ?? 0);
  useEffect(() => {
    if (value == null) return;
    const from = prev.current, to = value;
    prev.current = value;
    if (from === to) return;
    setFlash(to > from ? "flash-up" : "flash-down");
    const t0 = performance.now(), dur = 900;
    let raf;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur);
      setShown(from + (to - from) * (1 - Math.pow(1 - k, 3)));
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    const off = setTimeout(() => setFlash(""), 1400);
    return () => { cancelAnimationFrame(raf); clearTimeout(off); };
  }, [value]);
  const txt = `${signed && shown >= 0 ? "+" : ""}${Number(shown).toFixed(digits)}`;
  return <span className={`ticking ${flash}`}>{txt}</span>;
}

function BotEdge({ w, cur }) {
  const edge = w.bot_edge;
  const pts = (w.history || []).filter((h) => h[2] != null).map((h) => [h[0], h[2]]);
  if (edge != null) pts.push([Date.now() / 1000, edge]);
  const tone = edge == null ? "" : edge > 0 ? "up" : edge < 0 ? "down" : "flat";
  return (
    <div className={`bot-edge ${tone}`} title="Your account now minus what it would be worth if nobody had traded: the cash and coins you had when this started, at today's prices, plus what you paid in or took out since. Coin price swings cancel out, so this is only what the bot's trades added or lost.">
      <div className="edge-label">🤖 BOT'S OWN GAIN / LOSS</div>
      <div className="edge-big">{edge == null ? "–" : <Ticking value={edge} signed />} <span className="unit">{cur}</span></div>
      <div className="stat-sub">
        {w.bot_edge_pct != null ? `${w.bot_edge_pct >= 0 ? "+" : ""}${w.bot_edge_pct.toFixed(2)}% · ` : ""}
        vs doing nothing{w.bot_edge_since ? ` since ${new Date(w.bot_edge_since * 1000).toLocaleString([], { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}` : ""}
      </div>
      {pts.length > 1 && <div className="edge-spark"><LineChart series={[{ id: "e", color: edge >= 0 ? "var(--green)" : "var(--red)", bold: true, points: pts }]} baseline={0} height={54} /></div>}
      <Flows w={w} cur={cur} />
    </div>
  );
}

// Deposits and withdrawals are spotted automatically; this is for ones made before that, or to correct one.
function Flows({ w, cur }) {
  const [open, setOpen] = useState(false);
  const [amt, setAmt] = useState("");
  const [msg, setMsg] = useState("");
  const last = (w.flows || []).slice(-1)[0];
  const book = async (sign) => {
    const a = Number(String(amt).replace(",", "."));
    if (!a) return;
    try { await api("account/flow", { amount: sign * Math.abs(a) }); setMsg(`${sign > 0 ? "paid in" : "taken out"} ${Math.abs(a).toFixed(2)} ${cur}: not counted as gain or loss`); setAmt(""); setOpen(false); }
    catch (e) { setMsg(e.message); }
  };
  return (
    <div className="flows small">
      {last && <span className="dim">{last.amount > 0 ? "paid in" : "taken out"} {Math.abs(last.amount).toFixed(2)} {cur} {ago(last.ts)} ({last.how}) · </span>}
      {!open ? <button className="mini linkish" onClick={() => setOpen(true)}>paid in or out?</button> : (
        <span className="flow-form">
          <input type="number" min="0" step="1" placeholder="amount" value={amt} onChange={(e) => setAmt(e.target.value)} />
          <button className="mini primary" onClick={() => book(1)}>paid in</button>
          <button className="mini" onClick={() => book(-1)}>took out</button>
          <button className="mini linkish" onClick={() => setOpen(false)}>cancel</button>
        </span>
      )}
      {msg && <div className="dim">{msg}</div>}
    </div>
  );
}

function LiveWallet({ state, setFocus }) {
  const w = state.wallet;
  const caps = state.live_caps || {};
  const live = state.mode === "live";
  if (w.error) {
    return <section className="panel wallet"><h3>BITPANDA FUSION</h3><div className="err">Can't read your account: {w.error}</div></section>;
  }
  const cur = w.currency;
  const total = w.total;
  const coinsValue = w.coins_value ?? total - w.fiat;
  const part = (x) => `${total ? (x / total) * 100 : 0}%`;
  const sign = (x) => `${x >= 0 ? "+" : ""}${Number(x || 0).toFixed(2)}`;
  return (
    <section className={`panel wallet ${live ? "is-live" : ""}`}>
      <div className="row-head">
        <h3>{live ? "● LIVE · YOUR ACCOUNT" : "YOUR ACCOUNT · standby, nothing trades"} <span className="dim">· {w.venue} · real money · updated {ago(w.ts)}</span></h3>
      </div>
      <div className="wallet-top">
        <div className="wallet-total">
          <div className="big"><Ticking value={total} /> <span className="unit">{cur}</span></div>
          <div className={`wallet-change ${pctColor(w.change)}`}>
            {sign(w.change)} {cur} ({sign(w.change_pct)}%) <span className="dim">since {w.start_ts ? new Date(w.start_ts * 1000).toLocaleDateString() : "start"}</span>
          </div>
          <div className={`stat-sub ${pctColor(w.change_24h)}`}>{sign(w.change_24h)} {cur} last 24h</div>
        </div>
        <BotEdge w={w} cur={cur} />
        <div className="wallet-split">
          <div className="splitbar">
            <div className="seg-cash" style={{ width: part(w.fiat) }} title="cash" />
            <div className="seg-own" style={{ width: part(coinsValue) }} title="coins" />
          </div>
          <div className="split-legend">
            <span><i className="seg-cash" />Cash {w.fiat.toFixed(2)} {cur}</span>
            <span><i className="seg-own" />Coins {coinsValue.toFixed(2)} {cur}</span>
            <span className="dim">worst case you lose what's here, never more: no debt possible</span>
          </div>
          <div className="wallet-coins">
            {(w.all_coins || []).map((c) => (
              <button key={c.symbol} className={`tag ${c.bot ? "coin-bot" : "coin-own"}`} onClick={() => setFocus(c.symbol)}
                title={`${c.qty} ${c.symbol}${c.fast ? " · bought by the fast pot" : c.bot ? " · bought by the bot" : " · yours from before"}${c.tradable ? "" : " · not tradable on Fusion"}`}>
                {c.fast ? "⚡ " : c.bot ? "🤖 " : ""}{c.symbol} {c.price ? `${c.value.toFixed(2)}` : `${c.qty} (no price)`}
                {c.bot && c.pnl != null && <span className={pctColor(c.pnl)}> {sign(c.pnl)}</span>}
              </button>
            ))}
          </div>
          <div className="stat-sub">Limits: max {caps.max_invest} {cur} in coins · {caps.max_order} per order · spread ≤ {caps.max_spread_pct}% · {caps.use_my_coins ? "may use all your coins" : "only uses cash"}</div>
        </div>
      </div>
    </section>
  );
}

/** The Daily Brain: which tested strategy runs the real money, what it holds, what tonight likely brings. */
function BrainBar({ state, openAgent }) {
  const b = state.brain || {};
  const t = state.trend || {};
  const pv = t.preview || {};
  const holds = Object.entries(b.target || {});
  const mins = Math.max(0, Math.round((nextMidnightUTC() - Date.now() / 1000) / 60));
  const live = state.mode === "live";
  return (
    <section className={`panel brainbar ${b.on && live ? "on" : ""}`}>
      <button className="brain-label" onClick={() => openAgent("brain")} title="open the Daily Brain">DAILY BRAIN {b.on ? (live ? "● LIVE" : "· STANDBY") : "· OFF"}</button>
      <div className="bb-main">
        <b>{b.strategy || "no strategy picked"}</b>
        <span>holds {holds.length ? holds.map(([s, x]) => `${s} ${Math.round(x * 100)}%`).join(" · ") : "nothing (cash)"}</span>
        {t.btc && <span className={t.btc.ok ? "up" : "down"}>Bitcoin {t.btc.gap_pct > 0 ? "+" : ""}{t.btc.gap_pct}% vs its {t.btc.days}-day average{t.btc.ok ? "" : ": brain stays in cash"}</span>}
      </div>
      <div className="bb-next">
        <span className="dim">next decision in {Math.floor(mins / 60)}h {mins % 60}m ({hm(nextMidnightUTC())})</span>
        {b.on && (pv.buy?.length || pv.sell?.length
          ? <span>at today's prices: {pv.buy?.length ? <b className="up">buy {pv.buy.join(", ")}</b> : null}{pv.buy?.length && pv.sell?.length ? " · " : ""}{pv.sell?.length ? <b className="down">sell {pv.sell.join(", ")}</b> : null}</span>
          : <span className="dim">at today's prices: no trades tonight</span>)}
        {b.ts && <span className="dim">last decision {dayhm(b.ts)}</span>}
      </div>
      {b.note && <div className="dim small brain-note">{b.note}</div>}
    </section>
  );
}

function StatStrip({ state, openAgent }) {
  const lt = state.live_trades || {};
  const r = state.research || {};
  const p = state.professor || {};
  const g = Object.keys(state.guard || {});
  const rows = state.trend?.rows || [];
  const near = rows.filter((x) => ["would buy", "breakout, no slot", "near breakout"].includes(x.state));
  return (
    <section className="panel stats">
      <Stat label="Real trades" value={lt.n ?? 0} tone={lt.n ? "up" : "dim"} sub={lt.last ? `last ${ago(lt.last)}` : "none yet"}
        title="Orders placed on your real Bitpanda account" />
      <Stat label="Near a breakout" value={near.length} tone={near.length ? "up" : "dim"} sub={near.slice(0, 4).map((x) => x.symbol).join(" ") || "none"}
        title="Coins within 3% of their 20-day high while Bitcoin is in an uptrend (Trend Watch)" />
      <Stat label="Robust strategies" value={r.tested ? `${r.robust}/${r.tested}` : "–"} tone={r.robust ? "up" : "dim"}
        sub={r.ts ? `history test ${ago(r.ts)}` : "test pending"} title="Strategies that pass every check of the history test (Researcher)" />
      <Stat label="Guardian blocks" value={g.length} tone={g.length ? "down" : "dim"} sub={g.join(" ") || "all clear"}
        title="Coins blocked from buying after hack or delisting news, a crash, or a Professor warning" />
      <Stat label="Professor" value={p.ts ? ago(p.ts) : "–"} sub={p.block?.length ? `blocked ${p.block.map((x) => x.symbol).join(", ")}` : p.ts ? "no veto" : "after next decision"}
        title="Daily review by Claude Opus after each brain decision" />
      <Stat label="Phone briefing" value={state.telegram ? "on" : "off"} tone={state.telegram ? "up" : "dim"} sub={state.telegram ? "Telegram, daily" : "set up in Controls"} />
      <div className="stat-links">
        {["trend", "guardian", "researcher"].map((id) => <button key={id} className="mini" onClick={() => openAgent(id)}>{id}</button>)}
      </div>
    </section>
  );
}

function Professor({ p, on, openAgent }) {
  return (
    <div className="prof">
      <div className="prof-head">
        <button className="who linkish" onClick={() => openAgent("professor")}>The Professor</button>
        <span className="dim">{p.ts ? `${ago(p.ts)} · $${(p.cost || 0).toFixed(3)}` : on ? "first review after the next daily decision" : "AI off (no Anthropic key)"}</span>
      </div>
      {p.assessment ? <p>{p.assessment}</p> : <p className="dim">{p.skipped || "Reviews each daily decision with the news, the history test and the patterns. Can veto a buy for 24h, never forces a sell."}</p>}
      {p.watch && <p><b>Watch:</b> {p.watch}</p>}
      {p.idea && <p className="idea"><b>Idea:</b> {p.idea}</p>}
      {p.block?.length > 0 && <p className="down">Vetoed for 24h: {p.block.map((x) => `${x.symbol} (${x.reason})`).join("; ")}</p>}
    </div>
  );
}

const STATE_TONE = { "would buy": "up", "would sell": "down", holding: "held", "breakout, no slot": "warn", "near breakout": "near", waiting: "dim" };

function TrendWatch({ t, brain, guard, symbol, setFocus, openAgent }) {
  const [all, setAll] = useState(false);
  if (!t?.rows) return <><h3>TONIGHT'S WATCHLIST</h3><div className="empty">Trend Watch is loading daily history…</div></>;
  const rows = all ? t.rows : t.rows.filter((r) => r.state !== "waiting").concat(t.rows.filter((r) => r.state === "waiting").slice(0, 6));
  return (
    <>
      <div className="row-head">
        <h3>TONIGHT'S WATCHLIST <span className="dim">· the brain's rules on live prices · {ago(t.ts)}</span></h3>
        <div className="row-tools">
          <label className="toggle"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> all {t.rows.length}</label>
          <button className="mini" onClick={() => openAgent("trend")}>how</button>
        </div>
      </div>
      <p className="dim small">Buy = price closes above the {t.entry_days}-day high while Bitcoin is above its {t.btc?.days}-day average. Sell = close under the {t.exit_days}-day low or the trailing stop. Click a coin for its chart.</p>
      <div className="scroll" style={{ maxHeight: 380 }}>
        <table className="watch-table">
          <thead><tr><th>coin</th><th>state</th><th title="how far the price must rise to break the 20-day high">to buy line</th><th title="how far the price may fall before a sell">to sell line</th><th>30d</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol} className={`click ${r.symbol === symbol ? "sel" : ""}`} onClick={() => setFocus(r.symbol)}>
                <td><b>{r.symbol}</b>{r.held && <i className="held" title="the bot holds it">●</i>}{guard[r.symbol] && <span className="down" title="Guardian block"> ⛔</span>}</td>
                <td><span className={`st-chip ${STATE_TONE[r.state] || ""}`}>{r.state}</span></td>
                <td><Dist pct={r.to_breakout_pct} up /></td>
                <td><Dist pct={r.to_exit_pct} /></td>
                <td className={pctColor(r.strength_30d)}>{r.strength_30d == null ? "–" : `${r.strength_30d > 0 ? "+" : ""}${r.strength_30d}%`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {brain?.on === false && <p className="warn-msg small">The Daily Brain is off: this list is information only.</p>}
    </>
  );
}

/** distance bar: how close the price is to a line (buy line above, sell line below). */
function Dist({ pct, up }) {
  if (pct == null) return <span className="dim">–</span>;
  const closeness = Math.max(0, 1 - Math.abs(pct) / 15);
  const crossed = up ? pct <= 0 : pct >= 0;
  return (
    <span className="dist" title={`${pct > 0 ? "+" : ""}${pct}%`}>
      <i style={{ width: `${crossed ? 100 : closeness * 100}%`, background: up ? "var(--green)" : "var(--red)" }} />
      <em>{crossed ? (up ? "above" : "under") : `${pct > 0 ? "+" : ""}${pct}%`}</em>
    </span>
  );
}

function Guardian({ guard, shocks, openAgent }) {
  const g = Object.entries(guard);
  const s = Object.entries(shocks);
  return (
    <>
      <div className="row-head">
        <h3>GUARDIAN <span className="dim">· hacks, delistings, crashes</span></h3>
        <button className="mini" onClick={() => openAgent("guardian")}>details</button>
      </div>
      {!g.length && !s.length && <div className="all-clear">✓ all clear<div className="dim small">no hack, delisting, crash or Professor veto on any coin</div></div>}
      {g.map(([sym, x]) => (
        <div key={sym} className="guard-item">
          <div><b>{sym}</b> <span className="down">no buys until {dayhm(x.until)}</span>{x.sold && <span className="down"> · SOLD</span>}</div>
          <div className="dim small">{x.reason} · witnesses: {(x.sources || []).join(", ") || "–"}</div>
          {(x.titles || []).slice(-2).map((tt, i) => <div key={i} className="small">“{tt}”</div>)}
        </div>
      ))}
      {s.length > 0 && <h4>CRASH ALERTS (FUSION SCOUT)</h4>}
      {s.map(([sym, x]) => (
        <div key={sym} className="guard-item">
          <b>{sym}</b> <span className="down">{x.change}% in 24h while Bitcoin moved {x.btc}%</span> <span className="dim">· {ago(x.ts)}</span>
        </div>
      ))}
    </>
  );
}

function FocusDaily({ symbol, state }) {
  const [days, setDays] = useState(() => { try { return localStorage.getItem("tb-days") || "120"; } catch { return "120"; } });
  useEffect(() => { try { localStorage.setItem("tb-days", days); } catch { /* ignore */ } }, [days]);
  const [d] = usePoll(symbol ? `daily/${symbol}?days=${days}` : null, 120000, [state.live_trades?.n]);
  const lv = d?.levels;
  const t = (state.ticker || []).find((x) => x.symbol === symbol);
  return (
    <>
      <div className="row-head">
        <h3>{symbol} · DAILY <span className="dim">{t ? fmt.price(t.price) : ""}</span>{" "}
          {t && <span className={pctColor(t.change)}>{fmt.pct(t.change)} 24h</span>}
          {lv && <span className={`st-chip ${STATE_TONE[lv.state] || ""}`} style={{ marginLeft: 8 }}>{lv.state}</span>}
        </h3>
        <Tabs value={days} options={[["60", "2m"], ["120", "4m"], ["365", "1y"], ["1000", "3y"]]} onChange={setDays} />
      </div>
      <DailyChart d={d && d.symbol === symbol ? d : null} entry={state.trend?.entry_days || 20} exit={state.trend?.exit_days || 10} height={260} />
      <p className="dim small">
        <span className="up">- - buy line</span> = {state.trend?.entry_days || 20}-day high · <span className="down">- - sell line</span> = {state.trend?.exit_days || 10}-day low · arrows = the bot's real trades · last candle = today so far
        {lv?.stop ? ` · trailing stop ${fmt.price(lv.stop)}` : ""}
      </p>
    </>
  );
}

function LiveTrades({ trades, cur, setFocus }) {
  return (
    <>
      <h3>REAL TRADES <span className="dim">· your Bitpanda account</span></h3>
      {!trades.length && <div className="empty small">no real trades yet</div>}
      <div className="scroll" style={{ maxHeight: 340 }}>
        <table>
          <tbody>
            {trades.map((t, i) => (
              <tr key={i} className="click live-row" onClick={() => setFocus(t.symbol)} title={t.reason}>
                <td className="dim">{new Date(t.ts * 1000).toLocaleString([], { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</td>
                <td className={t.side === "BUY" ? "up" : "down"}>{t.side}</td>
                <td><b>{t.symbol}</b></td>
                <td>{t.notional?.toFixed(2)} {cur}</td>
                <td className="dim reason">{t.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

const FEED_FILTERS = [["all", "all"], ["trade", "trades"], ["ai", "AI"], ["warn", "problems"]];

function Feed({ log }) {
  const ref = useRef(null);
  const [filter, setFilter] = useState("all");
  const shown = useMemo(() => log.filter((l) => filter === "all"
    || (filter === "trade" && (l.level === "trade" || l.level === "live"))
    || (filter === "ai" && ["News Hunter", "The Professor"].includes(l.agent))
    || (filter === "warn" && (l.level === "warn" || l.level === "error"))).slice().reverse(), [log, filter]);
  useEffect(() => {
    const el = ref.current;
    if (el && el.scrollTop < 120) el.scrollTop = 0;
  }, [shown.length]);
  useEffect(() => { if (ref.current) ref.current.scrollTop = 0; }, [filter]);
  return (
    <>
      <div className="row-head">
        <h3>AGENT FEED <span className="dim">· newest on top</span></h3>
        <Tabs value={filter} options={FEED_FILTERS} onChange={setFilter} />
      </div>
      <div className="feed" ref={ref}>
        <div className="cursor">▋</div>
        {shown.map((l, i) => (
          <div key={i} className={`line ${l.level}`}>
            <span className="dim">{fmt.time(l.ts)}</span> <span className="who">{l.agent}</span> {l.message}
          </div>
        ))}
      </div>
    </>
  );
}

const EVENT_LABEL = { hack: "HACK", delisting: "DELISTING", listing: "listing", regulation: "regulation", partnership: "partnership", upgrade: "upgrade", macro: "macro" };

function News({ news, setFocus }) {
  const sorted = [...news].sort((a, b) => (b.ts || 0) - (a.ts || 0));
  return (
    <>
      <h3>NEWS, RATED <span className="dim">· by the News Hunter · hacks and delistings go to the Guardian</span></h3>
      <div className="scroll" style={{ maxHeight: 340 }}>
        {sorted.length ? sorted.map((n, i) => (
          <div key={i} className={`news-item ${["hack", "delisting"].includes(n.event) ? "alarm" : ""}`}>
            <div className="news-meta">
              <span className={`sent ${n.sentiment > 0.1 ? "up" : n.sentiment < -0.1 ? "down" : "dim"}`}>
                {n.sentiment > 0.1 ? "▲" : n.sentiment < -0.1 ? "▼" : "•"} {((n.impact || 0) * 100).toFixed(0)}%
              </span>
              {n.event && n.event !== "none" && <span className={`tag ${["hack", "delisting"].includes(n.event) ? "down" : ""}`}>{EVENT_LABEL[n.event] || n.event}</span>}
              {(n.symbols || []).map((s) => <button key={s} className="tag" onClick={() => setFocus(s)}>{s}</button>)}
              <span className="dim">{n.source} · {ago(n.ts)}{n.ai ? "" : " · keywords"}</span>
            </div>
            <a href={n.link?.startsWith("http") ? n.link : undefined} target="_blank" rel="noreferrer">{n.title}</a>
          </div>
        )) : <div className="empty small">no rated headlines yet</div>}
      </div>
    </>
  );
}

function FastPotCard({ state, openAgent }) {
  const f = state.fast || {};
  const w = state.wallet || {};
  const cur = w.currency || "CHF";
  const coins = Object.fromEntries((w.fast_coins || []).map((c) => [c.symbol, c]));
  const pos = Object.entries(f.pos || {});
  const mins = f.next ? Math.max(0, Math.round((f.next - Date.now() / 1000) / 60)) : null;
  const live = state.mode === "live";
  const goLab = () => { try { localStorage.setItem("tb-lab", "fast"); } catch { /* private window */ } window.dispatchEvent(new CustomEvent("tb-tab", { detail: "research" })); };
  return (
    <section className={`panel fastbar ${f.on && live ? "on" : ""}`}>
      <span className="fast-badge">⚡ FAST POT</span>
      {f.on ? (
        <>
          <span><b>{(f.pot || 0).toFixed(2)} {cur}</b> <span className="dim">{f.mode === "chf" ? "your amount + its gains" : `${f.pct}% of the account`}</span></span>
          <span className={f.realized >= 0 ? "up" : "down"}>{f.realized >= 0 ? "+" : ""}{(f.realized || 0).toFixed(2)} {cur} booked · {f.trades} trades{f.trades ? `, ${f.wins} won` : ""}</span>
          <span className="fast-pos">{pos.length ? pos.map(([s, p]) => {
            const c = coins[s];
            return <span key={s} className={`tag ${c?.pnl >= 0 ? "up" : "down"}`}>{s} {c ? `${c.pnl >= 0 ? "+" : ""}${c.pnl?.toFixed(2)}` : ""}</span>;
          }) : <span className="dim">no coin right now: waiting for a pump on {f.slots} slot{f.slots === 1 ? "" : "s"}</span>}</span>
          <span className="dim">{live ? `next look in ${mins} min` : "standby: LIVE is off"}</span>
          {!f.robust && <span className="play">play money: the rule doesn't pass every check</span>}
        </>
      ) : <span className="dim">off · a small, separate pot of real money for one fast rule, every 4 hours, next to the Daily Brain</span>}
      <span className="fast-note dim small">{f.note ? `last: ${f.note}` : ""}</span>
      <span className="row-tools">
        <button className="mini" onClick={() => openAgent("fast")}>agent</button>
        <button className={f.on ? "mini" : "mini primary"} onClick={goLab}>{f.on ? "settings" : "set it up"}</button>
      </span>
    </section>
  );
}
