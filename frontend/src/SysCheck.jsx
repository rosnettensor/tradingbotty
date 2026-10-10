import { useState } from "react";
import { api, usePoll } from "./useBot.js";

const MARK = { ok: ["✓", "up"], warn: ["!", "warn"], fail: ["✗", "down"], info: ["·", "dim"] };

/** Every link of the real-money chain, checked live, and a small real round trip that proves it. */
export default function SysCheck({ compact = false }) {
  const [d, reload, poll] = usePoll("syscheck", 30000);
  const [open, setOpen] = useState(false);
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  if (!d) return <div className="empty small">prüfe…</div>;
  const bad = d.rows.filter((r) => r.state === "fail" || r.state === "warn");
  const head = d.overall === "ok" ? "Technische Prüfungen bestanden · auf Handelssignal warten"
    : d.overall === "warn" ? `${bad.length} offene Hinweise · Kaufprüfungen beachten`
      : `Blockiert: ${bad.filter((r) => r.state === "fail").map((r) => r.title).join(", ")}`;
  const test = async () => {
    setBusy(true);
    setMsg("");
    try { const r = await api("syscheck/test", { confirm: "TEST" }); setMsg(r.test?.summary || ""); setAsk(false); reload(); }
    catch (e) { setMsg(e.message); }
    finally { setBusy(false); }
  };
  const t = d.test || {};
  return (
    <>
      {poll.error && <p role="status" className="warn">Systemcheck nicht aktualisiert: {poll.error}</p>}
      {!compact && <div className="readiness-strip">
        {Object.entries(d.entry_checks || {}).map(([key, proof]) => <div key={key}>
          <span className="dim">{key === "fast" ? "FAST POT · 4H" : "DAILY BRAIN · 1D"}</span>
          <strong className={proof.ready ? "up" : "warn"}>{proof.ready ? "Backtest bestanden" : "Neue Käufe gesperrt"}</strong>
          <small title={proof.strategy}>{proof.reason}</small>
        </div>)}
        <div><span className="dim">VOLATILITY · 2 MIN</span><strong>Paper-Test aktiv</strong><small>Sammelt Live-Marktdaten. Führt keine echten Orders aus.</small></div>
      </div>}
      <div className="row-head syscheck-head">
        <button className={`linkish syscheck-sum st-${d.overall}`} aria-expanded={open} onClick={() => setOpen(!open)}>
          <span className={MARK[d.overall === "fail" ? "fail" : d.overall === "warn" ? "warn" : "ok"][1]}>{MARK[d.overall === "fail" ? "fail" : d.overall === "warn" ? "warn" : "ok"][0]}</span>
          <b>SYSTEMCHECK</b> <span>{head}</span> <span className="dim">{open ? "▴" : "▾"}</span>
        </button>
        {(!compact || open) && <div className="syscheck-test">
          {t.ts && <span className={`small ${t.ok ? "up" : "down"}`} title={t.summary}>Testtrade {t.ok ? "✓" : "✗"}</span>}
          {!ask ? (
            <button disabled={busy || !d.trade_ready} onClick={() => setAsk(true)}
              title={d.trade_ready ? "" : "Erst die rot markierten Punkte lösen"}>Optionaler Testkauf (Echtgeld)</button>
          ) : (
            <>
              <button className="primary" disabled={busy} onClick={test}>{busy ? "läuft…" : `Ja: ${d.test_amount} ${d.currency} BTC kaufen und sofort verkaufen`}</button>
              <button disabled={busy} onClick={() => setAsk(false)}>Nein</button>
            </>
          )}
        </div>}
      </div>
      {(ask || msg) && <p className="small dim syscheck-note">{msg || `Echter Beweis für die ganze Kette: Risk Officer, Fusion, Buchung, Tagebuch. Kostet nur Gebühren und Spread (etwa 0.15 bis 0.30 ${d.currency}) und zählt nicht für die Strategien.`}</p>}
      {(open || d.overall === "fail") && (
        <div className="syscheck-rows">
          {d.rows.map((r) => {
            const [m, cls] = MARK[r.state] || MARK.info;
            return (
              <div key={r.id} className={`syscheck-row st-${r.state}`}>
                <span className={cls}>{m}</span>
                <b>{r.title}</b>
                <span className="dim">{r.detail}{r.fix && <> · <span className="warn">{r.fix}</span></>}</span>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
