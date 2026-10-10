import { usePoll } from "./useBot.js";
import { money } from "./TradingOverview.jsx";

/** The same deployment catalogue used by the execution gateway; no frontend readiness rules. */
export default function StrategyHub({ pickLab }) {
  const [data, , poll] = usePoll("operations", 15000);
  if (!data?.architecture) return <section className="panel"><p role="status">{poll.error || "Handelsplan wird geladen…"}</p></section>;
  const { modules, allocation, journal } = data.architecture;
  const outdated = !!poll.error || !data.ts || Date.now() / 1000 - data.ts > 90;
  const live = modules.filter(m => m.live_capable), research = modules.filter(m => !m.live_capable);
  return <div className="strategy-hub">
    <section className="panel strategy-intro">
      <span className="vol-eyebrow">EIN KONTO · EIN PORTFOLIO · EINE AUSFÜHRUNG</span>
      <h2>Mehrere Strategien. Ein gemeinsamer Handelsplan.</h2>
      <p className="dim">Daily und Fast liefern unterschiedliche Handelssignale. Ein gemeinsamer Kern prüft Kapital, Eigentum, Risiko und jede Order. Agenten beschaffen Daten, prüfen Ideen und überwachen den Betrieb.</p>
      <div className="strategy-pipeline"><span>Marktdaten</span><i>→</i><span>Strategien</span><i>→</i><span>Portfolio & Risiko</span><i>→</i><span>Ausführung & Buchung</span></div>
    </section>
    <section className="panel"><div className="row-head"><h3>ECHTGELD-STRATEGIEN</h3><span className={!outdated && data.execution_allowed ? "up small" : "warn small"}>{outdated ? "Status veraltet" : data.execution_allowed ? "Global LIVE" : "Echtgeld pausiert"}</span></div>
      {outdated && <p className="warn small" role="status">Der aktuelle Betriebsstatus ist nicht bestätigt. {poll.error || "Serververbindung wird geprüft."}</p>}
      <div className="strategy-cards">{live.map(m => {
        const lane = data.lanes.find(l => l.id === m.id);
        return <article className="strategy-card" key={m.id}><div className="row-head"><h3>{m.name}</h3><span className="dim small">{m.cadence}</span></div><strong className={!outdated && lane?.status === "armed" ? "up" : "warn"}>{outdated ? "Status veraltet" : lane?.label}</strong><p className="dim small">{m.description}</p><b>{money(lane?.budget)} {data.currency} Zuteilung</b><p className="small">{lane?.reasons?.[0] || "Ein gültiges Signal und die Orderprüfungen entscheiden über den nächsten Kauf."}</p><button onClick={() => pickLab(m.id)}>Strategie & Einstellungen →</button></article>;
      })}</div>
      <p className="dim small">Gemeinsamer Kontowert {money(allocation.total)} {data.currency} · Fast reserviert davon {money(allocation.fast_cash_reserve)} in Cash. Daily erhält nur den verbleibenden Spielraum bis zu seiner Obergrenze. Strategie-Budgets sind keine zusätzlichen Konten.</p>
      {allocation.overallocated && <p className="warn">Die Fast-Zuteilung bzw. bestehende Positionen übersteigen den Kontowert. Unter Fast die Zuteilung prüfen.</p>}
    </section>
    <section className="panel"><div className="row-head"><h3>FORSCHUNG · KEIN ECHTGELDHANDEL</h3><span className="badge">KEINE AUTOMATISCHE FREIGABE</span></div>
      <div className="strategy-cards">{research.map(m => <article className="strategy-card research-card" key={m.id}><h3>{m.name}</h3><p className="dim small">{m.description}</p><button className="linkish" onClick={() => pickLab(m.id)}>Forschung ansehen →</button></article>)}</div>
      <p className="dim small">„Promising“ bedeutet vielversprechend im Test, nicht handelbar. Volatility wird nicht nach einer Wartezeit automatisch live. Schatten- und Geister-Trades vergleichen Entscheidungen; sie senden keine Orders.</p>
    </section>
    <details className="panel strategy-journal"><summary>Wie die Ausführung abgesichert ist</summary><p className="small dim">Jeder Börsenauftrag wird vorher im Orderjournal erfasst. Bestätigte Ausführungen und Positionen werden gemeinsam gebucht. Bleibt nach einem Fehler oder Neustart eine Order ungeklärt, stoppt die Ausführung bis zum Abgleich mit der Börse.</p><div className="strategy-pipeline">{journal.map(j => <span key={j.state}>{({ filled: "Verbucht", rejected: "Abgelehnt", uncertain: "Ungeklärt", submitting: "In Übermittlung", no_fill: "Ohne Ausführung" })[j.state] || j.state}: {j.count}</span>)}{!journal.length && <span>Noch keine Aufträge seit dem Architektur-Update</span>}</div></details>
  </div>;
}
