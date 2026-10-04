import { useMemo, useState } from "react";
import { PriceChart, Sparkline } from "./charts.jsx";
import { ScoreBar, SignalBars, Tabs } from "./components.jsx";
import { fmt, pctColor, usePoll } from "./useBot.js";

const SORTS = [["score", "score"], ["change", "24h move"], ["hype", "buzz"], ["news", "news"]];
const KINDS = [["all", "all"], ["crypto", "crypto"], ["stock", "stocks"]];
const RANGES = [["60", "1h"], ["240", "4h"], ["1440", "24h"], ["4320", "3d"], ["10080", "7d"]];

export default function Markets({ state, focus, setFocus }) {
  const [kind, setKind] = useState("all");
  const [sort, setSort] = useState("score");
  const cfg = state.champion_config || {};
  const held = new Set((state.champion?.positions || []).map((p) => p.symbol));
  const tiles = useMemo(() => {
    const rows = (state.ticker || []).filter((t) => kind === "all" || t.kind === kind).map((t) => ({
      ...t, score: state.scores?.[t.symbol], sig: state.signals?.[t.symbol] || {},
      mentions: state.hype?.mentions?.[t.symbol] || 0, held: held.has(t.symbol), why: state.why_not?.[t.symbol],
    }));
    const key = { score: (r) => r.score ?? -9, change: (r) => Math.abs(r.change), hype: (r) => r.sig.hype ?? 0, news: (r) => Math.abs(r.sig.news ?? 0) }[sort];
    return rows.sort((a, b) => key(b) - key(a));
  }, [state.ticker, state.scores, state.signals, kind, sort]);
  const sel = tiles.find((t) => t.symbol === focus) || tiles[0];

  return (
    <div className="markets">
      <section className="panel">
        <div className="row-head">
          <h3>MARKETS <span className="dim">· {tiles.length} symbols · scores from the champion ({state.champion?.name})</span></h3>
          <div className="row-tools">
            <Tabs value={kind} options={KINDS} onChange={setKind} />
            <Tabs value={sort} options={SORTS} onChange={setSort} />
          </div>
        </div>
        <div className="tiles">
          {tiles.map((t) => (
            <button key={t.symbol} className={`tile ${t.symbol === sel?.symbol ? "sel" : ""} ${t.held ? "is-held" : ""}`} onClick={() => setFocus(t.symbol)}>
              <div className="tile-head">
                <b>{t.symbol}</b>
                <span className="kind">{t.kind}</span>
                {t.held && <span className="held-badge">HELD</span>}
                <span className={`chg ${pctColor(t.change)}`}>{fmt.pct(t.change)}</span>
              </div>
              <div className="tile-price">{fmt.price(t.price)}</div>
              <Sparkline values={t.spark} width={180} height={34} />
              {t.score != null
                ? <><ScoreBar score={t.score} entry={cfg.entry_score} exit={cfg.exit_score} /><div className="tile-score"><span className={pctColor(t.score)}>score {t.score.toFixed(2)}</span><span className="dim">{t.held ? "holding" : t.why || (t.score >= cfg.entry_score ? "buy signal" : "")}</span></div></>
                : <div className="tile-score dim">not traded by the champion</div>}
              <SignalBars values={t.sig} compact />
              <div className="tile-foot dim">
                <span>buzz {t.mentions}</span><span className={pctColor(t.sig.news)}>news {t.sig.news >= 0 ? "+" : ""}{(t.sig.news || 0).toFixed(2)}</span>
              </div>
            </button>
          ))}
        </div>
      </section>
      {sel && <Detail t={sel} state={state} />}
    </div>
  );
}

function Detail({ t, state }) {
  const [range, setRange] = useState("1440");
  const [all, setAll] = useState(true);
  const [data] = usePoll(`candles/${t.symbol}?minutes=${range}`, 30000, [state.trades?.length]);
  return (
    <section className="panel detail">
      <div className="row-head">
        <h3>{t.symbol} <span className="dim">{fmt.price(t.price)}</span> <span className={pctColor(t.change)}>{fmt.pct(t.change)} 24h</span>
          {data && !data.tradable && <span className="badge dim-badge">market closed</span>}</h3>
        <div className="row-tools">
          <label className="toggle"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> trades of all strategies</label>
          <Tabs value={range} options={RANGES} onChange={setRange} />
        </div>
      </div>
      <div className="detail-grid">
        {data ? <PriceChart candles={data.candles} trades={data.trades} showAll={all} height={300} /> : <div className="empty" style={{ height: 300 }}>loading…</div>}
        <div>
          <h3>RAW SIGNALS</h3>
          <SignalBars values={t.sig} />
          <p className="dim small">Every signal runs from −1 to +1. Strategies weigh them differently: see Controls.</p>
          {data && <><h3>TRADES IN VIEW</h3><p className="small">{data.trades.length} by all strategies, {data.trades.filter((x) => x.champion).length} by the champion. Big triangles = champion.</p></>}
        </div>
      </div>
    </section>
  );
}
