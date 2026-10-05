import { useEffect, useMemo, useRef, useState } from "react";
import Sphere from "./Sphere.jsx";
import { LineChart, PriceChart } from "./charts.jsx";
import { ScoreBar, SignalBars, Stat, Tabs, SIGNAL_LABELS } from "./components.jsx";
import { ago, api, fmt, pctColor, usePoll } from "./useBot.js";

const RANGES = [["60", "1h"], ["240", "4h"], ["1440", "24h"]];

export default function Cockpit({ state, pulse, focus, setFocus }) {
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
  const w = state.wallet && !state.wallet.error ? state.wallet : null;
  const scores = Object.entries(state.scores || {}).sort((a, b) => b[1] - a[1]);
  const energy = Math.min(1, scores.reduce((s, [, v]) => s + Math.abs(v), 0) / Math.max(1, scores.length) * 2);
  const points = champ ? [...curve, [Date.now() / 1000, champ.equity]] : [];
  const symbol = focus && state.signals?.[focus] ? focus : scores[0]?.[0];

  return (
    <div className="cockpit2">
      {state.wallet && <LiveWallet state={state} setFocus={setFocus} />}
      <StatStrip state={state} ret={ret} />

      <section className="panel core">
        <Sphere mood={(w ? w.change_pct : ret) / 5} energy={energy} pulse={pulse} label={w ? (
          <div className="core-label">
            <div className="dim">YOUR ACCOUNT · {state.mode.toUpperCase()}</div>
            <div className="equity">{w.total.toFixed(2)} {w.currency}</div>
            <div className={pctColor(w.change_pct)}>{fmt.pct(w.change_pct)} since start</div>
          </div>
        ) : (
          <div className="core-label">
            <div className="dim">{champ?.name || "–"} · paper test</div>
            <div className="equity">{fmt.usd(champ?.equity)}</div>
            <div className={ret >= 0 ? "up" : "down"}>{fmt.pct(ret)} since start</div>
          </div>
        )} />
        <Professor state={state} />
      </section>

      <section className="panel focus">
        <FocusChart symbol={symbol} state={state} />
      </section>

      <section className="panel equity-panel">
        {w ? <>
          <h3>YOUR ACCOUNT ({w.currency}) <span className="dim">· real money · dashed line = where it started</span></h3>
          <LineChart series={[{ id: "a", color: "var(--magenta)", bold: true, points: [...(w.history || []), [Date.now() / 1000, w.total]] }]}
            baseline={w.start_total} height={170} />
        </> : <>
          <h3>CHAMPION TEST (USD) <span className="dim">· dashed line = start</span></h3>
          <LineChart series={[{ id: "c", color: "var(--cyan)", bold: true, points }]} baseline={champ?.start} height={170} />
        </>}
      </section>

      <section className="panel why">
        <Scores state={state} scores={scores} symbol={symbol} setFocus={setFocus} />
      </section>

      <section className="panel positions">
        <Positions state={state} setFocus={setFocus} />
      </section>

      <section className="panel log">
        <Feed log={state.log || []} />
      </section>

      <section className="panel newsfeed">
        <News state={state} setFocus={setFocus} />
      </section>
    </div>
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
    <div className={`bot-edge ${tone}`} title="Your account now minus what it would be worth if nobody had traded: the cash and coins you had when this started, at today's prices. BTC and other price swings cancel out, so this is only what the bot's trades added or lost. Paying in or withdrawing money moves it too.">
      <div className="edge-label">🤖 BOT'S OWN GAIN / LOSS</div>
      <div className="edge-big">{edge == null ? "–" : <Ticking value={edge} signed />} <span className="unit">{cur}</span></div>
      <div className="stat-sub">
        {w.bot_edge_pct != null ? `${w.bot_edge_pct >= 0 ? "+" : ""}${w.bot_edge_pct.toFixed(2)}% · ` : ""}
        vs doing nothing{w.bot_edge_since ? ` since ${new Date(w.bot_edge_since * 1000).toLocaleString([], { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}` : ""}
      </div>
      {pts.length > 1 && <div className="edge-spark"><LineChart series={[{ id: "e", color: edge >= 0 ? "var(--green)" : "var(--red)", bold: true, points: pts }]} baseline={0} height={54} /></div>}
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
        <h3>{live ? "● LIVE · YOUR ACCOUNT" : "YOUR ACCOUNT · watching, not trading"} <span className="dim">· {w.venue} · real money · updated {ago(w.ts)}</span></h3>
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
              <button key={c.symbol} className="tag coin-own" onClick={() => setFocus(c.symbol)}
                title={`${c.qty} ${c.symbol}${c.bot ? " · bought by the bot" : ""}${c.tradable ? "" : " · not tradable on Fusion"}`}>
                {c.bot ? "🤖 " : ""}{c.symbol} {c.price ? `${c.value.toFixed(2)}` : `${c.qty} (no price)`}
              </button>
            ))}
          </div>
          {live && <BuyNow state={state} cur={cur} />}
          <div className="stat-sub">Limits: max {caps.max_invest} {cur} in bot trades · {caps.max_order}/order · spread ≤ {caps.max_spread_pct}% · {caps.use_my_coins ? "may use all coins" : "only uses cash"}</div>
        </div>
      </div>
      <Blockers state={state} />
    </section>
  );
}

/** Your button: a real buy of the amount you choose, in the best coin right now. */
function BuyNow({ state, cur }) {
  const [amount, setAmount] = useState(10);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = state.force_buy;
  const go = async () => {
    if (!window.confirm(`Real money: buy ${amount} ${cur} of the best coin right now?\n\nThe champion then manages it with its own stop loss and sell rules.`)) return;
    setBusy(true); setMsg("");
    try {
      const r = await api("buy_now", { amount: Number(amount) });
      setMsg(r.ok ? `✓ Bought ${r.amount} ${r.currency} of ${r.symbol}` : r.waiting ? `Waiting: ${r.why}` : `Not bought: ${r.why}`);
    } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="buynow">
      <span className="buynow-label">BUY NOW</span>
      <input type="number" min={1} step={1} value={amount} onChange={(e) => setAmount(e.target.value)} />
      <span className="dim">{cur}</span>
      <button className="primary" disabled={busy || !!pending} onClick={go}>{busy ? "buying…" : "buy the best coin"}</button>
      {pending && <span className="warn-msg">searching until {new Date(pending.until * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}{pending.why ? `: ${pending.why}` : "…"}
        <button className="mini" onClick={() => api("buy_now/cancel", {})}>cancel</button></span>}
      {msg && !pending && <span className="small">{msg}</span>}
    </div>
  );
}

/** Why the champion isn't buying right now, grouped by reason, so you can see if rules block each other. */
function Blockers({ state }) {
  const b = Object.entries(state.blockers || {});
  if (!b.length) return null;
  const total = b.reduce((n, [, s]) => n + s.length, 0);
  return (
    <div className="blockers">
      <span className="dim">WHY {state.champion?.name?.toUpperCase()} ISN'T BUYING:</span>
      {b.map(([why, syms]) => (
        <span key={why} className="blocker" title={syms.join(", ")}>
          <i style={{ width: `${(syms.length / total) * 100}%` }} />{why} <b>{syms.length}</b>
        </span>
      ))}
    </div>
  );
}

function StatStrip({ state, ret }) {
  const c = state.champion || {};
  const s = state.stats || {};
  const r = state.regime || {};
  const cfg = state.champion_config || {};
  const nextProf = state.next_professor ? Math.max(0, Math.round((state.next_professor - Date.now() / 1000) / 60)) : null;
  return (
    <section className="panel stats">
      <Stat label={`Champion: ${c.name || "–"}`} value={<span className={pctColor(ret)}>{fmt.pct(ret)}</span>} sub="its test run, sized like your account"
        title="The strategy whose trades are copied to your account. Every strategy is tested in parallel on an account the size of yours." />
      <Stat label="Realized P&L (test)" value={fmt.usd(s.realized)} tone={pctColor(s.realized)} sub="champion's closed trades" />
      <Stat label="Fees (test)" value={fmt.usd(s.fees)} tone="down" sub={`${s.trades ?? 0} trades total`} />
      <Stat label="Trades 24h" value={s.trades_24h ?? 0} sub={`${s.buys_1h ?? 0}/${cfg.max_buys_per_hour ?? "–"} buys this hour`} />
      <Stat label="Win rate" value={s.win_rate == null ? "–" : `${s.win_rate}%`} sub="of closed trades" />
      <Stat label="Market mood" value={r.mood || "…"} tone={r.mood === "risk-on" ? "up" : r.mood === "risk-off" ? "down" : ""}
        sub={state.stocks_enabled ? `crypto ${r.crypto ?? "–"} · stocks ${r.stocks ?? "–"}` : `crypto ${r.crypto ?? "–"}`} />
      <Stat label="Fear & Greed" value={r.fear_greed ?? "…"} sub={r.fear_greed_label} />
      <Stat label="Risk appetite" value={state.risk_appetite?.toFixed(2)} sub={nextProf == null ? "" : `Professor in ${nextProf}m`}
        title="Set by the Professor: scales every position size (0.5 defensive to 1.5 aggressive)" />
      <Stat label="Real trades" value={state.live_trades?.n ?? 0} tone={state.live_trades?.n ? "up" : "dim"}
        sub={state.live_trades?.last ? `last ${ago(state.live_trades.last)}` : "none yet"}
        title="Orders placed on your real Bitpanda account (buys and sells, including the buy-now button)" />
    </section>
  );
}

function Professor({ state }) {
  const p = state.professor || {};
  return (
    <div className="prof">
      <div className="prof-head">
        <span className="who">The Professor</span>
        <span className="dim">{p.ts ? ago(p.ts) : "first review after warm-up"}</span>
      </div>
      {p.assessment ? <p>{p.assessment}</p> : <p className="dim">Waiting for enough fresh market, news and sentiment data.</p>}
      {p.idea && <p className="idea"><b>Idea:</b> {p.idea}</p>}
      {state.avoid?.length > 0 && <p className="down">Avoiding: {state.avoid.join(", ")}</p>}
    </div>
  );
}

function FocusChart({ symbol, state }) {
  const [range, setRange] = useState(() => localStorage.getItem("tb-range") || "240");
  const [all, setAll] = useState(false);
  useEffect(() => { try { localStorage.setItem("tb-range", range); } catch { /* ignore */ } }, [range]);
  const [data] = usePoll(symbol ? `candles/${symbol}?minutes=${range}` : null, 20000, [state.trades?.length]);
  const t = (state.ticker || []).find((x) => x.symbol === symbol);
  return (
    <>
      <div className="row-head">
        <h3>{symbol || "–"} <span className="dim">{t ? fmt.price(t.price) : ""}</span>{" "}
          {t && <span className={pctColor(t.change)}>{fmt.pct(t.change)} 24h</span>}
          {data && !data.tradable && <span className="badge dim-badge">market closed</span>}
        </h3>
        <div className="row-tools">
          <label className="toggle"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> all strategies</label>
          <Tabs value={range} options={RANGES} onChange={setRange} />
        </div>
      </div>
      {data ? <PriceChart candles={data.candles} trades={data.trades} showAll={all} height={210} /> : <div className="empty" style={{ height: 210 }}>loading…</div>}
    </>
  );
}

function Scores({ state, scores, symbol, setFocus }) {
  const cfg = state.champion_config || {};
  const w = state.weights || {};
  const sig = state.signals?.[symbol] || {};
  const norm = Object.values(w).reduce((s, x) => s + Math.abs(x), 0) || 1;
  const contrib = Object.fromEntries(Object.keys(SIGNAL_LABELS).map((k) => [k, ((w[k] || 0) * (sig[k] || 0)) / norm * 2]));
  const held = new Set((state.champion?.positions || []).map((p) => p.symbol));
  const why = state.why_not?.[symbol];
  const score = state.scores?.[symbol];
  return (
    <>
      <h3>PREDICTOR SCORES <span className="dim">· ▲ buy line {cfg.entry_score} · ▼ sell line {cfg.exit_score}</span></h3>
      <div className="scores2">
        {scores.map(([sym, v]) => (
          <button key={sym} className={`score2 ${sym === symbol ? "sel" : ""}`} onClick={() => setFocus(sym)}>
            <span className="sym">{sym}{held.has(sym) && <i className="held" title="held">●</i>}</span>
            <ScoreBar score={v} entry={cfg.entry_score} exit={cfg.exit_score} />
            <span className={pctColor(v)}>{v.toFixed(2)}</span>
          </button>
        ))}
      </div>
      {symbol && (
        <div className="whybox">
          <div className="row-head">
            <h3>WHY {symbol} SCORES {score?.toFixed(2) ?? "–"}</h3>
            <span className={`verdict ${held.has(symbol) ? "up" : why ? "dim" : "up"}`}>
              {held.has(symbol) ? "holding" : why ? `not buying: ${why}` : score >= cfg.entry_score ? "buy signal" : ""}
            </span>
          </div>
          <SignalBars values={contrib} scale={Math.max(0.15, ...Object.values(contrib).map(Math.abs))} />
          <p className="dim small">Each bar = signal × the champion's weight. They add up to the score. Change the weights in Controls.</p>
        </div>
      )}
    </>
  );
}

function Positions({ state, setFocus }) {
  const champ = state.champion;
  const trades = (state.trades || []).filter((t) => t.champion || t.mode === "live").slice(0, 40);
  return (
    <>
      <h3>OPEN POSITIONS</h3>
      {!champ?.positions.length && <div className="empty small">no open positions: the team is watching</div>}
      <table>
        <tbody>
          {champ?.positions.map((p) => (
            <tr key={p.symbol} onClick={() => setFocus(p.symbol)} className="click">
              <td><b>{p.symbol}</b></td>
              <td>{fmt.usd(p.value)}</td>
              <td className={pctColor(p.pnl_pct)}>{fmt.pct(p.pnl_pct)}</td>
              <td className="dim">{ago(p.opened).replace(" ago", "")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3 style={{ marginTop: 12 }}>TRADES</h3>
      <div className="scroll" style={{ maxHeight: 260 }}>
        <table>
          <tbody>
            {trades.map((t, i) => (
              <tr key={i} className={`click ${t.mode === "live" ? "live-row" : ""}`} onClick={() => setFocus(t.symbol)}>
                <td className="dim">{fmt.time(t.ts)}</td>
                <td className={t.side === "BUY" ? "up" : "down"}>{t.side}</td>
                <td><b>{t.symbol}</b></td>
                <td>{fmt.usd(t.notional)}</td>
                <td className={t.pnl > 0 ? "up" : t.pnl < 0 ? "down" : "dim"}>{t.pnl == null ? "" : fmt.usd(t.pnl)}</td>
                <td className="dim reason" title={t.reason}>{t.mode === "live" ? "LIVE" : t.reason}</td>
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
    || (filter === "ai" && ["News Hunter", "The Professor", "Optimizer"].includes(l.agent))
    || (filter === "warn" && (l.level === "warn" || l.level === "error"))).slice().reverse(), [log, filter]);
  // newest on top: stay pinned to the top unless the reader scrolled down to read older lines
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

function News({ state, setFocus }) {
  const [tab, setTab] = useState("news");
  const news = [...(state.news || [])].sort((a, b) => (b.ts || 0) - (a.ts || 0));
  const hype = state.hype || {};
  const mentions = Object.entries(hype.mentions || {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, 8);
  return (
    <>
      <div className="row-head">
        <h3>{tab === "news" ? "NEWS, RATED" : "SOCIAL BUZZ"} <span className="dim">· newest on top</span></h3>
        <Tabs value={tab} options={[["news", "news"], ["hype", "buzz"]]} onChange={setTab} />
      </div>
      <div className="scroll" style={{ maxHeight: 330 }}>
        {tab === "news" && (news.length ? news.map((n, i) => (
          <div key={i} className="news-item">
            <div className="news-meta">
              <span className={`sent ${n.sentiment > 0.1 ? "up" : n.sentiment < -0.1 ? "down" : "dim"}`}>
                {n.sentiment > 0.1 ? "▲" : n.sentiment < -0.1 ? "▼" : "•"} {(n.impact * 100).toFixed(0)}%
              </span>
              {n.symbols.map((s) => <button key={s} className="tag" onClick={() => setFocus(s)}>{s}</button>)}
              <span className="dim">{n.source} · {ago(n.ts)}{n.ai ? "" : " · keywords"}</span>
            </div>
            <a href={n.link?.startsWith("http") ? n.link : undefined} target="_blank" rel="noreferrer">{n.title}</a>
          </div>
        )) : <div className="empty small">no rated headlines yet</div>)}
        {tab === "hype" && (
          <>
            <p className="dim small">Trending on CoinGecko: {(hype.trending || []).join(", ") || "–"}</p>
            <table>
              <tbody>
                {mentions.map(([s, v]) => (
                  <tr key={s} className="click" onClick={() => setFocus(s)}>
                    <td><b>{s}</b></td><td>{v} mentions</td>
                    <td className={pctColor(hype.scores?.[s])}>{hype.scores?.[s] != null ? `hype ${hype.scores[s] >= 0 ? "+" : ""}${hype.scores[s]}` : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h3 style={{ marginTop: 10 }}>HOT POSTS</h3>
            {(hype.posts || []).map((p, i) => (
              <div key={i} className="news-item">
                <div className="news-meta"><span className="dim">r/{p.sub}</span>{p.symbols.map((s) => <span key={s} className="tag">{s}</span>)}</div>
                <span>{p.title}</span>
              </div>
            ))}
          </>
        )}
      </div>
    </>
  );
}
