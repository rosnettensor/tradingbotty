import { ago, usePoll } from "./useBot.js";

const fmt = (n, digits = 2) => Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: digits }) : "—";

export default function Speculation() {
  const [data] = usePoll("speculation", 15000);
  if (!data) return <section className="panel"><div className="empty">loading…</div></section>;
  const p = data.paper || {};
  return <section className="panel">
    <h3>FUSION VOLATILITY SCANNER <span className="dim">· PAPER ONLY · {data.currency}</span></h3>
    <p className="small">All listed Fusion spot pairs, ranked by 24h high–low range. A paper entry needs ≥8% range, ≥2% momentum since the previous scan, a break above the previously observed 24h high, an acceptable spread and ≥1,000 {data.currency} displayed depth within 1%. Checks every 2 minutes.</p>
    <p className="dim small">The experiment starts with 1,000 virtual {data.currency}: three positions, ≤100 per entry, fees and slippage included. Exit at −8%, +20%, or after 24h at the next observed price. Gaps can exceed the stop. No live execution or automatic promotion. High volatility does not predict 10× or 100× returns.</p>
    {data.error && <p className="err">{data.error}</p>}
    <div className="row-tools"><b>{data.stale ? "STALE / waiting for Fusion read access" : `Updated ${ago(data.ts)}`}</b><span>Paper equity: {fmt(p.equity)} {data.currency}</span><span className={p.return_pct >= 0 ? "up" : "down"}>{fmt(p.return_pct)}%</span><span>Cash: {fmt(p.cash)}</span><span>Closed P/L: {fmt(p.realized)}</span></div>
    <div className="table-wrap"><table>
      <thead><tr><th>Coin</th><th>24h range</th><th>Scan momentum</th><th>Spread</th><th>Depth ({data.currency})</th><th>Minimum</th><th>Signal</th></tr></thead>
      <tbody>{data.rows.map(r => <tr key={r.symbol}>
        <td><b>{r.symbol}</b>{r.newly_seen && <span className="dim small"> · newly observed</span>}</td><td>{fmt(r.range_pct)}%</td><td>{r.momentum_pct == null ? "—" : `${fmt(r.momentum_pct)}%`}</td><td>{r.spread_pct == null ? "—" : `${fmt(r.spread_pct, 3)}%`}</td><td>{fmt(r.depth_quote, 0)}</td><td>{fmt(r.min_order)}</td><td>{data.stale ? "stale" : r.note || (r.eligible ? (r.breakout && r.momentum_pct >= 2 ? "paper signal" : "watch") : "excluded")}</td>
      </tr>)}</tbody>
    </table></div>
    <h4>VIRTUAL POSITIONS</h4>
    <div className="chip-row">{Object.entries(p.positions || {}).map(([s, v]) => <span className="chip" key={s}>{s} · {fmt(v.qty * v.mark)} {data.currency} · entry {fmt(v.entry, 6)}</span>)}</div>
    {!Object.keys(p.positions || {}).length && <p className="dim small">No paper positions yet. First scan establishes the baseline; buys require a later confirmed signal.</p>}
    <h4>PAPER TRADE LOG</h4>
    <div className="table-wrap"><table><thead><tr><th>Time</th><th>Coin</th><th>Side</th><th>{data.currency}</th></tr></thead><tbody>{(p.trades || []).slice(-20).reverse().map((t, i) => <tr key={`${t.ts}-${t.symbol}-${i}`}><td>{new Date(t.ts * 1000).toLocaleString()}</td><td>{t.symbol}</td><td>{t.side}</td><td>{fmt(t.side === "BUY" ? t.amount : t.pnl)}</td></tr>)}</tbody></table></div>
  </section>;
}
