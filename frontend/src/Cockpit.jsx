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
  const scores = Object.entries(state.scores || {}).sort((a, b) => b[1] - a[1]);
  const energy = Math.min(1, scores.reduce((s, [, v]) => s + Math.abs(v), 0) / Math.max(1, scores.length) * 2);
  const points = champ ? [...curve, [Date.now() / 1000, champ.equity]] : [];
  const symbol = focus && state.signals?.[focus] ? focus : scores[0]?.[0];

  return (
    <div className="cockpit2">
      <StatStrip state={state} ret={ret} />
      {state.wallet && <LiveWallet state={state} setFocus={setFocus} />}

      <section className="panel core">
        <Sphere mood={ret / 5} energy={energy} pulse={pulse} label={
          <div className="core-label">
            <div className="dim">{champ?.name || "–"} · {state.mode.toUpperCase()}</div>
            <div className="equity">{fmt.usd(champ?.equity)}</div>
            <div className={ret >= 0 ? "up" : "down"}>{fmt.pct(ret)} since start</div>
          </div>
        } />
        <Professor state={state} />
      </section>

      <section className="panel focus">
        <FocusChart symbol={symbol} state={state} />
      </section>

      <section className="panel equity-panel">
        <h3>CHAMPION EQUITY (USD) <span className="dim">· dashed line = starting stake</span></h3>
        <LineChart series={[{ id: "c", color: "var(--cyan)", bold: true, points }]} baseline={champ?.start} height={170} />
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

function LiveWallet({ state, setFocus }) {
  const w = state.wallet;
  const caps = state.live_caps || {};
  const live = state.mode === "live";
  if (w.error) {
    return <section className="panel wallet"><h3>BITPANDA FUSION</h3><div className="err">Can't read your account: {w.error}</div></section>;
  }
  const cur = w.currency;
  const total = w.total || w.fiat + w.bot_value + w.yours_value;
  const part = (x) => `${total ? (x / total) * 100 : 0}%`;
  const used = Math.min(100, ((w.bot_cost || 0) / (caps.max_invest || 1)) * 100);
  return (
    <section className={`panel wallet ${live ? "is-live" : ""}`}>
      <div className="row-head">
        <h3>{live ? "● LIVE MONEY" : "REAL ACCOUNT"} <span className="dim">· {w.venue} · {live ? "the champion's trades are copied here" : "watching only, switch to LIVE to trade"} · {ago(w.ts)}</span></h3>
      </div>
      <div className="wallet-top">
        <div className="wallet-total">
          <div className="stat-label">Account total</div>
          <div className="big">{total.toFixed(2)} <span>{cur}</span></div>
          <div className="stat-sub">worst case you lose this, never more: no debt possible</div>
        </div>
        <div className="wallet-split">
          <div className="splitbar">
            <div className="seg-cash" style={{ width: part(w.fiat) }} title="cash" />
            <div className="seg-bot" style={{ width: part(w.bot_value) }} title="bot's coins" />
            <div className="seg-own" style={{ width: part(w.yours_value) }} title="your coins" />
          </div>
          <div className="split-legend">
            <span><i className="seg-cash" />Cash {w.fiat.toFixed(2)}</span>
            <span><i className="seg-bot" />Bot's coins {w.bot_value.toFixed(2)} <b className={pctColor(w.bot_pnl)}>{w.bot_pnl >= 0 ? "+" : ""}{w.bot_pnl.toFixed(2)}</b></span>
            <span><i className="seg-own" />Your coins {w.yours_value.toFixed(2)}</span>
          </div>
          <div className="stat-sub">
            Live cap {w.bot_cost.toFixed(2)} of {caps.max_invest} {cur} used · max {caps.max_order}/order · spread ≤ {caps.max_spread_pct}% ·{" "}
            {caps.use_my_coins ? <b className="up">bot may use your coins</b> : <span>your coins stay untouched</span>}
          </div>
          <div className="capbar"><div style={{ width: `${used}%` }} /></div>
        </div>
      </div>
      <div className="wallet-coins">
        {w.coins.map((c) => (
          <button key={`b${c.symbol}`} className="tag coin-bot" onClick={() => setFocus(c.symbol)} title="bought by the bot">
            🤖 {c.symbol} {c.value.toFixed(2)} <span className={pctColor(c.pnl)}>{c.pnl == null ? "" : `${c.pnl >= 0 ? "+" : ""}${c.pnl.toFixed(2)}`}</span>
          </button>
        ))}
        {(w.own_coins || []).map((c) => (
          <span key={`o${c.symbol}`} className="tag coin-own" title={c.tradable ? "your coin" : "your coin, not tradable on Fusion in this currency"}>
            {c.symbol} {c.price ? `${c.value.toFixed(2)} ${cur}` : `${c.qty} (no price)`}
          </span>
        ))}
      </div>
    </section>
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
      <Stat label="Equity" value={fmt.usd(c.equity)} sub={<span className={pctColor(ret)}>{fmt.pct(ret)}</span>} />
      <Stat label="Cash" value={fmt.usd(c.cash)} sub={`${fmt.usd(s.invested)} invested`} />
      <Stat label="Realized P&L" value={fmt.usd(s.realized)} tone={pctColor(s.realized)} sub="closed trades" />
      <Stat label="Fees paid" value={fmt.usd(s.fees)} tone="down" sub={`${s.trades ?? 0} trades total`} />
      <Stat label="Trades 24h" value={s.trades_24h ?? 0} sub={`${s.buys_1h ?? 0}/${cfg.max_buys_per_hour ?? "–"} buys this hour`} />
      <Stat label="Win rate" value={s.win_rate == null ? "–" : `${s.win_rate}%`} sub="of closed trades" />
      <Stat label="Market mood" value={r.mood || "…"} tone={r.mood === "risk-on" ? "up" : r.mood === "risk-off" ? "down" : ""}
        sub={`crypto ${r.crypto ?? "–"} · stocks ${r.stocks ?? "–"}`} />
      <Stat label="Fear & Greed" value={r.fear_greed ?? "…"} sub={r.fear_greed_label} />
      <Stat label="Risk appetite" value={state.risk_appetite?.toFixed(2)} sub={nextProf == null ? "" : `Professor in ${nextProf}m`}
        title="Set by the Professor: scales every position size (0.5 defensive to 1.5 aggressive)" />
      <Stat label="Stock markets" value={Object.entries(state.markets_open || {}).filter(([k, v]) => v && k !== "Crypto").map(([k]) => k).join(", ") || "all closed"}
        tone={Object.entries(state.markets_open || {}).some(([k, v]) => v && k !== "Crypto") ? "up" : "dim"}
        sub="crypto trades 24/7" title={Object.entries(state.markets_open || {}).map(([k, v]) => `${k}: ${v ? "open" : "closed"}`).join("\n")} />
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
