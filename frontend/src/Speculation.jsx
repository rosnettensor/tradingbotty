import { useState } from "react";
import { ago, usePoll } from "./useBot.js";
import { LineChart } from "./charts.jsx";

const fmt = (n, digits = 2) => Number.isFinite(n) ? n.toLocaleString("de-CH", { maximumFractionDigits: digits }) : "—";
const color = n => n > 0 ? "up" : n < 0 ? "down" : "";

export default function Speculation() {
  const [data, reload, poll] = usePoll("speculation", 15000);
  const [query, setQuery] = useState("");
  const [qualified, setQualified] = useState(false);
  if (!data) return <section className="panel"><div className="empty" role="status">{poll.error ? `Scanner nicht erreichbar: ${poll.error}` : "Fusion-Scanner wird geladen…"}<button onClick={reload}>Erneut laden</button></div></section>;
  const p = data.paper || {}, cur = data.currency;
  const stale = data.stale || !!data.error || !!poll.error;
  const rows = (data.rows || []).filter(r => r.symbol.includes(query.toUpperCase().trim()) && (!qualified || r.eligible));
  const positions = Object.entries(p.positions || {});
  return <div className="volatility-desk">
    <section className="panel vol-hero">
      <div><span className="vol-eyebrow">FORSCHUNG / VIRTUELLER VORWÄRTSTEST</span><h2>Volatility radar<span className="badge">KEIN ECHTGELD</span></h2><p className="dim">Eigener Strategiekandidat mit festen Regeln. Er sendet weder echte Orders noch Kaufaufträge an Daily oder Fast.</p></div>
      <div className="vol-feed"><span className={`badge ${stale ? "warn" : "ok"}`}>{stale ? "DATEN AUSSTEHEND" : "● MARKTDATEN AKTUELL"}</span><small className="dim">{data.ts ? `Letzter Scan ${ago(data.ts)} · Takt 2 min` : "Wartet auf Fusion-Lesezugriff"}</small><button onClick={reload} disabled={poll.loading}>Ansicht aktualisieren</button></div>
    </section>
    {(data.error || poll.error) && <div role="status" className="vol-alert">Daten nicht aktuell: {data.error || poll.error}. Angezeigt wird der letzte empfangene Stand.</div>}
    <div className="vol-kpis">
      <Metric label="Virtuelles Portfolio" value={`${fmt(p.equity)} ${cur}`} detail={`${fmt(p.return_pct)}% seit Start · Startkapital 1.000`} tone={color(p.return_pct)} />
      <Metric label="Marktuniversum" value={fmt(data.total_pairs, 0)} detail={`${data.checked_pairs || 0} Orderbücher geprüft · ${data.eligible_pairs || 0} Filter bestanden`} />
      <Metric label="Geschlossene Trades¹" value={fmt(p.closed_count, 0)} detail={`${fmt(p.wins, 0)} profitabel · ${fmt(p.fees_paid)} ${cur} Modellgebühren¹`} />
      <Metric label="Freies Paper-Kapital" value={`${fmt(p.cash)} ${cur}`} detail={`${positions.length} / 3 Plätze · maximal 100 je Kauf`} />
    </div>
    <div className="vol-main">
      <section className="panel vol-equity"><div className="row-head"><h3>PAPER EQUITY</h3><span className="dim small">Letzte 720 Beobachtungen</span></div>
        {(p.history || []).length > 1 ? <LineChart series={[{ id: "paper", color: "var(--cyan)", points: p.history, bold: true }]} baseline={1000} height={210} /> : <div className="vol-wait">Die Kurve entsteht aus echten Beobachtungen.<small>Nach zwei erfolgreichen Scans erscheinen die ersten Punkte.</small></div>}
        <div className="vol-chart-foot"><span>Realisiert <b className={color(p.realized)}>{fmt(p.realized)} {cur}</b></span><span>Max. Rückgang¹ <b>{fmt(p.max_drawdown_pct)}%</b></span><span>Scans¹ <b>{fmt(p.observations, 0)}</b></span></div>
        {!!p.stale_positions?.length && <p className="warn small">Letzte bekannte Kurse für {p.stale_positions.join(", ")}: Portfoliowert enthält veraltete Bewertungen.</p>}
      </section>
      <section className="panel vol-rules"><span className="vol-eyebrow">ENTRY CHECKLIST</span><h3>Ein Signal muss bestehen.</h3><ol><li><b>Bewegung</b><span>24h-Spanne ≥8%, Scan-Momentum ≥2%</span></li><li><b>Ausbruch</b><span>Über dem zuvor beobachteten 24h-Hoch</span></li><li><b>Handelbarkeit</b><span>Spread im Limit, ≥1.000 {cur} Tiefe innerhalb 1%</span></li></ol><p className="small dim">Paper-Ausstieg bei −8%, +20% oder nach 24h zum nächsten beobachteten Kurs. Kurslücken können den Stopp überschreiten.</p><span className={`badge ${p.paused ? "warn" : ""}`}>{p.paused ? "PAPER-KÄUFE PAUSIERT" : "KEINE AUTOMATISCHE LIVE-FREIGABE"}</span></section>
    </div>
    <section className="panel vol-watchlist"><div className="vol-list-head"><div><h3>MARKT-RADAR</h3><small className="dim">Top 30 nach 24h-Spanne · Spanne ist keine Rendite</small></div><div className="vol-filters"><input aria-label="Coin suchen" placeholder="Coin suchen…" value={query} onChange={e => setQuery(e.target.value)} /><button aria-pressed={qualified} className={qualified ? "active" : ""} onClick={() => setQualified(!qualified)}>Nur Filter bestanden</button></div></div>
      <div className="vol-scroll"><table><thead><tr><th>Coin</th><th>24h-Spanne</th><th>Scan-Momentum</th><th>Spread</th><th>Tiefe · {cur}</th><th>Prüfergebnis</th></tr></thead><tbody>{rows.map(r => <tr key={r.symbol}><td><b>{r.symbol}</b>{r.newly_seen && <small className="vol-new">NEU ERFASST</small>}</td><td><span className="vol-range" style={{ "--range": `${Math.min(100, r.range_pct)}%` }}>{fmt(r.range_pct)}%</span></td><td className={color(r.momentum_pct)}>{r.momentum_pct == null ? "—" : `${r.momentum_pct > 0 ? "+" : ""}${fmt(r.momentum_pct)}%`}</td><td>{r.spread_pct == null ? "—" : `${fmt(r.spread_pct, 3)}%`}</td><td>{fmt(r.depth_quote, 0)}</td><td className={stale ? "warn" : r.eligible ? "up" : "dim"}>{stale ? "Veralteter Stand" : r.note || "Nicht geprüft"}</td></tr>)}</tbody></table></div>
      {!rows.length && <div className="empty">{data.rows?.length ? "Keine Coins für diesen Filter." : "Noch keine Marktdaten. Fusion-Verbindung im Systemcheck prüfen."}</div>}
    </section>
    <div className="vol-main"><section className="panel"><h3>VIRTUELLE POSITIONEN <span className="dim">{positions.length} / 3</span></h3>{!positions.length && <div className="vol-wait">Noch kein bestätigter Einstieg.<small>Der erste Scan setzt die Referenz. Ein späterer Scan muss alle Regeln erfüllen.</small></div>}{positions.map(([s, v]) => <div className="vol-position" key={s}><div><b>{s}</b><small className="dim">Einstieg {fmt(v.entry, 6)}</small></div><div><b className={color(v.qty * v.mark - v.cost)}>{fmt(v.qty * v.mark - v.cost)} {cur}</b><small className={v.stale ? "warn" : "dim"}>{v.stale ? "Kurs veraltet" : `${fmt(v.qty * v.mark)} ${cur} Marktwert`}</small></div></div>)}</section>
    <section className="panel"><h3>PAPER-JOURNAL <span className="dim">Letzte 20 Orders</span></h3><div className="vol-scroll"><table><thead><tr><th>Zeit</th><th>Coin</th><th>Aktion</th><th>{cur}</th></tr></thead><tbody>{(p.trades || []).slice(-20).reverse().map((t, i) => <tr key={`${t.ts}-${i}`}><td>{new Date(t.ts * 1000).toLocaleString("de-CH", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</td><td>{t.symbol}</td><td>{t.side === "BUY" ? "KAUF" : "VERKAUF"}</td><td className={t.side === "SELL" ? color(t.pnl) : ""}>{fmt(t.side === "BUY" ? t.amount : t.pnl)} <small className="dim">{t.side === "BUY" ? "Einsatz" : "P/L"}</small></td></tr>)}</tbody></table></div>{!p.trades?.length && <div className="empty">Neue Paper-Orders erscheinen hier.</div>}</section></div>
    <p className="dim small vol-footnote">Forward-Test mit virtuellen Mitteln, kein Nachweis profitabler Live-Ausführung. Gebührenmodell je Seite: {fmt(data.fee_pct)}%, Slippage: {fmt(data.slippage_pct)}%; der tatsächliche Spread wird als Filter geprüft, nicht zusätzlich als Ausführungskosten abgerechnet. ¹ Kennzahlen seit {p.metrics_since ? new Date(p.metrics_since * 1000).toLocaleString("de-CH") : "Beginn der Messung"}; ältere Trades fließen weiterhin in Cash und realisierte P/L ein.</p>
  </div>;
}
function Metric({ label, value, detail, tone = "" }) {
  return <section className="panel vol-metric"><span className="vol-eyebrow">{label}</span><strong className={tone}>{value}</strong><small className="dim">{detail}</small></section>;
}
