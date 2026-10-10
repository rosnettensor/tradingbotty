import { ago, pctColor } from "./useBot.js";

export const money = (n, digits = 2) => Number.isFinite(n) ? n.toLocaleString("de-CH", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "—";
export function openResearch(lab) {
  try { localStorage.setItem("tb-lab", lab); } catch { /* private mode */ }
  window.dispatchEvent(new CustomEvent("tb-research", { detail: lab }));
  window.dispatchEvent(new CustomEvent("tb-tab", { detail: "research" }));
}

export function TradingStatus({ data, error, state, onDetails }) {
  const outdated = !data || Date.now() / 1000 - data.ts > 90 || !!error;
  const enabled = data?.lanes.filter(l => l.enabled) || [];
  const ready = enabled.filter(l => l.status === "armed").length;
  const headline = outdated ? "Betriebsstatus wird geprüft" : !data.execution_allowed ? "Echtgeldhandel pausiert"
    : !enabled.length ? "LIVE verbunden · kein Händler aktiviert" : ready ? "Echtgeld aktiv · wartet auf gültige Signale" : "Echtgeld aktiv · Kaufprüfungen offen";
  const tone = outdated || !ready ? "warn" : "up";
  return <section className="panel mission-status">
    <div className="mission-status-head"><div><span className="vol-eyebrow">TRADINGBOTTY / OPERATION CENTER</span><h2 className={tone}>{headline}</h2></div><span className="badge">SPOT · OHNE KREDIT</span></div>
    <p className="dim small">{outdated ? error || "Verbindung zum Server wird hergestellt." : data.global_reasons.length ? data.global_reasons.join(" · ") : "LIVE erlaubt echte Orders. Signalbereit bedeutet: die Strategie darf prüfen – jede Order muss zusätzlich Risiko, Guthaben und Spread bestehen."}</p>
    <div className="mission-lanes">{(data?.lanes || []).map(l => <button key={l.id} className={`mission-lane ${l.status}`} onClick={() => openResearch(l.id === "daily" ? "daily" : "fast")}>
      <span><b>{l.name}</b><small>{l.id === "daily" ? "Tageskerzen" : "4h-Kerzen · Ausstiege jede Minute"}</small></span>
      <span><b className={outdated ? "dim" : l.status === "armed" ? "up" : l.enabled ? "warn" : "dim"}>{outdated ? "Status veraltet" : l.label}</b><small>{money(l.budget)} {data.currency} Budget</small></span>
      <span className="mission-lane-reason">{l.reasons[0] || (l.enabled ? "Nächster Kauf erst bei passendem Signal." : "Strategie im Research-Bereich auswählen und aktivieren.")}</span>
    </button>)}</div>
    <div className="mission-context"><span><b>Bank:</b> {data?.bank.label || "wird geprüft"}{data?.bank.mode !== "shadow" && !data?.bank.effective ? " · momentan nicht ausführend" : ""}</span><span className="dim">Radar & Schatten: virtuell</span><button className="linkish" onClick={onDetails}>Betrieb & Prüfungen →</button></div>
    {!!data?.conflicts.length && <details className="mission-conflicts"><summary>{data.conflicts.length} Hinweis{data.conflicts.length > 1 ? "e" : ""} zu Budget oder Daten</summary><ul>{data.conflicts.map(c => <li key={c}>{c}</li>)}</ul></details>}
  </section>;
}

export function AccountStrip({ state, data }) {
  const w = state.wallet || {}, cur = w.currency || data?.currency || "CHF";
  return <div className="mission-metrics">
    <Metric label="Kontowert" value={`${money(w.total)} ${cur}`} sub={w.ts ? `Fusion ${ago(w.ts)}${data?.wallet_fresh ? "" : " · Stand prüfen"}` : "Konto wird verbunden"} />
    <Metric label="Verfügbares Bargeld" value={`${money(w.fiat)} ${cur}`} sub="Noch nicht investiert · Reservierungen beachten" />
    <Metric label="Mehrwert des Bots" value={`${money(w.bot_edge)} ${cur}`} tone={pctColor(w.bot_edge)} sub="Seit Start gegenüber unverändertem Bestand" />
    <Metric label="Offene Bot-Positionen" value={`${(w.coins || []).length + (w.fast_coins || []).length}`} sub="Daily Brain und Fast · eigene Altbestände separat" />
  </div>;
}
function Metric({ label, value, sub, tone = "" }) {
  return <div><span className="vol-eyebrow">{label}</span><strong className={tone}>{value}</strong><small className="dim">{sub}</small></div>;
}

export function Observation({ data, error }) {
  const w = data?.window, cur = data?.currency || "CHF";
  if (!w) return <section className="panel mission-observation"><h3>12-STUNDEN-BEOBACHTUNG</h3><p className="dim small">{error || "Lädt echte Ausführungen vom Server…"}</p></section>;
  const pct = Math.min(100, w.hours / 12 * 100);
  return <section className="panel mission-observation"><div className="row-head"><h3>{w.complete ? "LETZTE 12 STUNDEN" : "ERSTE 12 STUNDEN"}</h3><span className="dim small">{money(w.hours, 1)} / 12 h</span></div>
    <div className="mission-progress" role="progressbar" aria-label="Beobachtungszeit" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${pct}%` }} /></div>
    <div className="mission-result"><span className="dim">Realisierte P/L</span><strong className={pctColor(w.realized)}>{money(w.realized)} <small>{cur}</small></strong><small className="dim">Gebuchte Verkäufe nach Handelsgebühren; offene Gewinne fehlen hier.</small></div>
    <div className="mission-mini-metrics"><span><small>Echte Orders</small><b>{w.orders}</b></span><span><small>Handelsgebühren</small><b>{money(w.fees)} {cur}</b></span><span><small>KI-Ausgaben</small><b>${money(w.ai_usd, 3)}</b></span></div>
    <p className="dim small">{w.orders ? `Letzte Ausführung ${ago(w.last)}. Testkäufe und virtuelle Trades sind ausgeschlossen.` : "Noch keine echten Orders in diesem Zeitraum. Ohne gültiges Kaufsignal eröffnet der Bot keine neue Position."} Gebühren sind separat zur Kontrolle gezeigt, nicht nochmals von P/L abzuziehen. KI-Ausgaben sind separat in USD.</p>
    <details className="mission-note"><summary>Was sagt diese Beobachtung aus?</summary><p>Sie zeigt, ob Daten, Entscheidungen und Ausführungen funktionieren. Zwölf Stunden sind kein belastbarer Profitabilitätsnachweis. Die Messung bleibt bei Neustarts erhalten; danach zeigt sie jeweils die letzten zwölf Stunden.</p></details>
  </section>;
}

export function PositionsBrief({ state, onMore, setFocus }) {
  const w = state.wallet || {};
  const rows = [...(w.coins || []).map(c => ({ ...c, owner: "Daily" })), ...(w.fast_coins || []).map(c => ({ ...c, owner: "Fast" }))];
  return <section className="panel mission-positions"><div className="row-head"><h3>ECHTE BOT-POSITIONEN</h3><button className="linkish" onClick={onMore}>Alle Details →</button></div>
    {!rows.length ? <p className="mission-empty">Noch keine Bot-Positionen.<small>Vorhandene eigene Coins findest du unter „Positionen & Verlauf“.</small></p> : rows.slice(0, 5).map(c => <button className="mission-position" key={`${c.owner}-${c.symbol}`} onClick={() => { setFocus(c.symbol); onMore(); }}><span><b>{c.symbol}</b><small>{c.owner}</small></span><span><b>{money(c.value)} {w.currency}</b><small className={pctColor(c.pnl)}>{money(c.pnl)} Buchgewinn/-verlust</small></span></button>)}
  </section>;
}

export function SignalBrief({ state, setFocus, onMore }) {
  const rows = (state.trend?.rows || []).filter(r => ["would buy", "near breakout", "would sell", "breakout, no slot"].includes(r.state)).slice(0, 3);
  const label = { "would buy": "Kaufsignal bei Kerzenschluss prüfen", "would sell": "Ausstieg bei Kerzenschluss prüfen", "near breakout": "Nahe an der Kaufgrenze", "breakout, no slot": "Signal · kein freier Platz" };
  return <section className="panel mission-signals"><div className="row-head"><h3>IM FOKUS</h3><button className="linkish" onClick={onMore}>Markt & Signale →</button></div>
    {!rows.length ? <p className="mission-empty">Kein Tages-Ausbruch im Fokus.<small>Fast prüft unabhängig davon seine eigene 4h-Strategie.</small></p> : rows.map(r => <button className="mission-signal" key={r.symbol} onClick={() => { setFocus(r.symbol); onMore(); }}><b>{r.symbol}</b><span>{state.guard?.[r.symbol] ? "Guardian blockiert Käufe" : label[r.state]}</span></button>)}
    <small className="dim">Momentaufnahme, kein bereits ausgeführter Auftrag.</small>
  </section>;
}
