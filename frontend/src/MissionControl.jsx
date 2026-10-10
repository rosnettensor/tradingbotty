import { useState } from "react";
import { money, openResearch } from "./TradingOverview.jsx";
import { countdown } from "./missionState.js";

const clock = ts => ts ? new Date(ts * 1000).toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
const date = ts => new Date(ts * 1000).toLocaleString("de-CH", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const CHANNELS = ["Daten", "Entscheidung", "Ausführung"];
const STATES = { filled: "Bestätigt & verbucht", submitting: "Antwort ausstehend", uncertain: "Abgleich nötig", rejected: "Abgelehnt", no_fill: "Ohne Ausführung" };

export function CoreTelemetry({ mission, ops, now }) {
  return <>
    <div className={`core-telemetry phase-${mission.phase}`}>
      <div><span className="vol-eyebrow">PORTFOLIO / LIVE OBSERVATORY</span><strong><i className="telemetry-dot" />{mission.title}</strong></div>
      <span className="telemetry-clock">{clock(now)}<small>LOKALE ZEIT</small></span>
    </div>
    <div className="core-signal-path" aria-label="Gemeldete Systemaktivität">{CHANNELS.map((label, i) => <span key={label} className={mission.channels[i] ? "active" : ""}><i />{label}{i < 2 && <b>→</b>}</span>)}</div>
  </>;
}

export function DecisionConsole({ mission, ops, now, onDetails, openAgent }) {
  return <section className={`panel decision-console phase-${mission.phase}`}>
    <div className="row-head"><h3>ENTSCHEIDUNGSZENTRALE</h3><span className="decision-index">01 / NOW</span></div>
    <div className="decision-heading"><div className="decision-glyph" aria-hidden><i /><i /><i /></div><h2 key={mission.title}>{mission.title}</h2></div>
    <p className="decision-reason" key={mission.detail}>{mission.detail}</p>
    {!!mission.running.length && <div className="activity-chips">{mission.running.slice(0, 4).map(n => <button key={n.id} onClick={() => openAgent(n.id)}><i />{n.name} ↗</button>)}</div>}
    <div className="decision-schedules">{(ops?.lanes || []).map(l => <button key={l.id} onClick={() => openResearch(l.id)} className="decision-schedule">
      <span><b>{l.id === "daily" ? "Daily" : l.id === "speculation" ? "Volatility" : "Fast"}</b><small>{l.enabled ? "Nächste Signalprüfung" : "Strategie ausgeschaltet"}</small></span>
      <strong>{!mission.fresh ? "—" : !l.enabled ? "AUS" : countdown(l.next_at, now)}</strong>
      <span className="schedule-track"><i style={{ width: `${!mission.fresh || !l.enabled || !l.next_at ? 0 : Math.max(0, Math.min(100, 100 * (1 - (l.next_at - now) / (l.id === "daily" ? 86400 : l.id === "speculation" ? 120 : 14400))))}%` }} /></span>
      <small className="schedule-note">{mission.fresh ? l.reasons?.[0] || "Signalprüfung; kein versprochener Kaufzeitpunkt." : "Warte auf aktuellen Serverstatus."}</small>
    </button>)}</div>
    <div className="decision-bottom"><span><i className={mission.fresh && ops?.wallet_fresh ? "up" : "warn"}>●</i> Konto {ops?.wallet_ts ? clock(ops.wallet_ts) : "noch nicht gelesen"}</span><button className="linkish" onClick={onDetails}>Prüfungen öffnen ↗</button></div>
  </section>;
}

/** Durable order stages only; no invented signal timestamp, fill or pre-trade benchmark. */
export function OrderJourney({ ops, error, now }) {
  const [selected, select] = useState(null);
  const rows = ops?.order_trace || [];
  const row = rows.find(r => r.id === selected) || rows[0];
  const stale = error || !ops?.ts || now - ops.ts > 90;
  const ex = row?.execution || {}, filled = row?.state === "filled";
  const stateLabel = STATES[row?.state] || "Journalstatus prüfen";
  return <section className="panel order-journey">
    <div className="row-head"><div><span className="vol-eyebrow">EXECUTION / AUDIT TRAIL</span><h3>JEDER AUFTRAG. NACHVOLLZIEHBAR.</h3></div><span className="badge">BÖRSENJOURNAL</span></div>
    {stale && <p className="warn small" role="status">Verbindung wird geprüft. Gespeicherte Aufträge zeigen keinen bestätigten aktuellen Betriebsstatus.</p>}
    {!row ? <div className="journey-empty"><div className="empty-orbits" aria-hidden><i /><i /><b>◎</b></div><p>Noch keine Order-Spur vorhanden.<small>Die Aufzeichnung beginnt mit dem gemeinsamen Handelskern. Ältere Trades bleiben unten im Verlauf sichtbar.</small></p></div> : <>
      <div className="journey-selector" role="group" aria-label="Börsenauftrag auswählen">{rows.map(r => <button key={r.id} aria-pressed={row.id === r.id} className={row.id === r.id ? "active" : ""} onClick={() => select(r.id)}><span className={r.side === "BUY" ? "up" : "down"}>{r.side === "BUY" ? "↗" : "↘"}</span><b>{r.symbol}</b><small>{clock(r.created_at)}</small></button>)}</div>
      <div className="journey-detail" key={row.id}>
        <div className="journey-title"><div><span className="vol-eyebrow">{({ brain: "Daily", fast: "Fast", volatility: "Volatility-Pilot", test: "Technischer Test", funding: "Cash-Beschaffung" })[row.book] || row.book}</span><h2>{row.side} <span>{row.symbol}</span></h2></div><span className={`order-state ${filled ? "up" : "warn"}`}>{stateLabel}</span></div>
        <ol className="journey-steps">
          <li className="done"><i>01</i><div><b>Entscheidungsgrund</b><p>{row.reason}</p></div></li>
          <li className="done"><i>02</i><div><b>Übermittlung begonnen</b><p>{date(row.created_at)}{Number.isFinite(row.requested_amount) ? ` · angefragt ${money(row.requested_amount)} ${ops.currency}` : ""}</p></div></li>
          <li className={filled ? "done" : "pending"}><i>03</i><div><b>{stateLabel}</b><p>{row.state === "submitting" ? "Ein Eintrag bestätigt noch keine Ausführung." : `${date(row.updated_at)} · letzter gespeicherter Stand`}</p></div></li>
        </ol>
        {filled && <div className="fill-metrics"><div><small>Bestätigter Betrag</small><b>{money(ex.notional)} {ops.currency}</b></div><div><small>Handelsgebühr</small><b>{money(ex.fee, 4)} {ops.currency}</b></div><div><small>Mittlerer Preis</small><b>{money(ex.price, 4)} {ops.currency}</b></div><div><small>Bestätigte Menge</small><b>{money(ex.quantity, 6)} {row.symbol}</b></div></div>}
        <details className="journey-meta"><summary>Messgrenzen & Referenz</summary><p>Preisabweichung: nicht gemessen. Vor dieser Order wurde kein vergleichbarer Referenzkurs gespeichert. Gebühren sind echte Ausführungskosten; der Betrag ist kein Gewinn. Realisierte Ergebnisse stehen im Handelsverlauf.</p><code>{row.exchange_order_id || row.id}</code></details>
      </div>
    </>}
  </section>;
}
