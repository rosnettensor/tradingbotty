import { fmt, pctColor, usePoll } from "./useBot.js";

const ROLE_CLASS = { Regierung: "gov", Gericht: "court", Du: "you", Parlament: "parl", Dienste: "serv" };
const sign = (x) => `${x >= 0 ? "+" : "−"}${Math.abs(Number(x || 0)).toFixed(2)}`;
const daymon = (ts) => new Date(ts * 1000).toLocaleString([], { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

/** Bauplan 2, phase 1: every agent's worth in your currency, the loss check, and the ghost trades behind the guards. */
export default function Scoreboard({ state }) {
  const [d] = usePoll("scoreboard", 60000, [state.live_trades?.n]);
  if (!d) return <div className="empty small">loading…</div>;
  const cur = d.currency || "CHF";
  const rows = d.rows || [];
  const top = Math.max(1, ...rows.map((r) => Math.abs(r.score)));
  const l = d.losses || {};
  return (
    <>
      <div className="row-head">
        <h3>PUNKTESTAND <span className="dim">· was jeder Agent in {cur} gebracht hat</span></h3>
        <span className="dim small" title={d.note}>Händler: echter Gewinn · Wächter: Geister-Trades · KI zahlt selbst</span>
      </div>
      <div className="score-grid">
        <div className="score-rows">
          {rows.map((r) => (
            <div key={r.id} className="score-row" title={r.what}>
              <span className="score-icon">{r.icon}</span>
              <div className="score-name">
                <b>{r.name}</b> <span className={`role-chip ${ROLE_CLASS[r.role] || ""}`}>{r.role}</span>
                <div className="dim small">{r.detail}{r.vs_btc != null && <> · gegen BTC halten <span className={pctColor(r.vs_btc)}>{sign(r.vs_btc)}</span>{r.vs_btc_n < r.trades ? ` (${r.vs_btc_n} von ${r.trades})` : ""}</>}</div>
              </div>
              <div className="score-bar" aria-hidden="true">
                <i className={r.score >= 0 ? "pos" : "neg"} style={{ width: `${(Math.abs(r.score) / top) * 50}%` }} />
              </div>
              <b className={`score-val ${pctColor(r.score)}`}>{sign(r.score)}</b>
            </div>
          ))}
        </div>
        <div className="score-side">
          <div className="losscheck">
            <h4>VERLUST-CHECK</h4>
            {l.n ? <>
              <div className="loss-stats">
                <span><small>Treffer</small><b>{l.hit_pct}%</b></span>
                <span><small>Ø Gewinn</small><b className="up">{l.won ? `${sign(l.avg_win_pct)}%` : "–"}</b></span>
                <span><small>Ø Verlust</small><b className="down">{l.lost ? `${sign(l.avg_loss_pct)}%` : "–"}</b></span>
                <span><small>nötig</small><b>{l.need_pct != null ? `${l.need_pct}%` : "–"}</b></span>
              </div>
              <p className="small">{l.verdict}</p>
              <div className="loss-exits">
                {(l.by_exit || []).map((x) => (
                  <div key={x.kind}><span>{x.name} <span className="dim">({x.n})</span></span><b className={pctColor(x.pnl)}>{sign(x.pnl)}</b></div>
                ))}
              </div>
            </> : <p className="dim small">{l.verdict}</p>}
          </div>
          <div className="ghosts">
            <h4>👻 GEISTER-TRADES <span className="dim">· gestoppte Trades, weiterverfolgt</span></h4>
            {!(d.ghosts || []).length ? (
              <p className="dim small">Noch keine. Sobald der Guardian, dein Kurs, der Risk Officer oder du einen Trade stoppt, verfolgt der Bot den Coin, als wäre der Trade passiert, und schreibt das Ergebnis dem gut, der ihn gestoppt hat.</p>
            ) : (d.ghosts || []).slice(0, 10).map((g) => {
              const move = g.last && g.price ? (g.last / g.price - 1) * 100 : 0;
              return (
                <div key={g.id} className={`ghost ${g.closed ? "done" : ""}`}>
                  <div><b>{g.symbol}</b> <span className="dim small">{g.book === "fast" ? "⚡" : "🧠"} {fmt.price(g.price)} → {fmt.price(g.last)} (<span className={pctColor(move)}>{move >= 0 ? "+" : ""}{move.toFixed(1)}%</span>)</span></div>
                  <div className="dim small">{g.why}</div>
                  <div className="small">{g.blocker_name || g.blocker}: <b className={pctColor(g.credit)}>{sign(g.credit || 0)} {cur}</b> <span className="dim">{g.closed ? "beendet" : `läuft bis ${daymon(g.until)}`}</span></div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </>
  );
}

/** The facts behind one diary trade: for a sell also those of its buy. */
export function WhyLines({ e }) {
  const mark = { ok: ["✓", "up"], no: ["✗", "down"], warn: ["!", "warn"], info: ["·", "dim"] };
  const block = (title, lines) => lines?.length ? (
    <div className="why-block">
      <div className="dim small">{title}</div>
      {lines.map((x, i) => {
        const [m, cls] = mark[x[0]] || mark.info;
        return <div key={i} className="why-line"><span className={cls}>{m}</span><span>{x[1]}</span></div>;
      })}
    </div>
  ) : null;
  const btc = e.btc_in && e.btc_out ? (e.btc_out / e.btc_in - 1) * 100 : null;
  return (
    <div className="why">
      {block(e.side === "SELL" ? "Warum verkauft" : "Warum gekauft", e.why)}
      {block("Warum gekauft", e.entry_why)}
      {btc != null && <div className="why-line"><span className="dim">₿</span><span>Bitcoin im selben Zeitraum <span className={pctColor(btc)}>{btc >= 0 ? "+" : ""}{btc.toFixed(1)}%</span>, dieser Trade <span className={pctColor(e.pnl_pct)}>{e.pnl_pct >= 0 ? "+" : ""}{Number(e.pnl_pct).toFixed(1)}%</span></span></div>}
    </div>
  );
}
