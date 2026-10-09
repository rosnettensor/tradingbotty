import { useState } from "react";
import { api, usePoll } from "./useBot.js";

const when = (ts) => new Date(ts * 1000).toLocaleString("de-CH", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

/** Bauplan 2, phase 4: the strategist's latest review, its proposals (taken only on your tap) and its ideas. */
export default function StrategistPanel() {
  const [d, reload] = usePoll("strategist", 30000);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [old, setOld] = useState(false);
  if (!d) return <div className="empty small">loading…</div>;
  const m = d.memo;
  const act = async (path, body, key) => {
    setBusy(key);
    setMsg("");
    try { await api(path, body); reload(); if (key !== "ask") setMsg("übernommen"); else setMsg("Der Stratege denkt nach: Antwort in ein, zwei Minuten."); }
    catch (e) { setMsg(e.message); }
    finally { setBusy(""); }
  };
  return (
    <>
      <div className="row-head">
        <h3>🧭 STRATEGE <span className="dim">· Claude's stärkstes Modell · bei Ereignissen und sonntags 18:00</span></h3>
        <span className="dim small">{d.cost ? `bisher $${d.cost.toFixed(2)}` : ""}</span>
      </div>
      {!m ? (
        <p className="dim small">{d.note || (d.ai ? "Noch keine Prüfung. Er wacht auf, wenn ein Trade schliesst, die Marktlage wechselt, der Prüfer urteilt oder der Bot Geld verliert, spätestens Sonntag 18:00. Oder jetzt:" : "Ohne KI-Schlüssel ruht er.")}</p>
      ) : (
        <div className="strat">
          <div className="strat-meta dim small">{when(m.ts)} · Anlass: {(m.trigger || []).join(" · ")} · Sicherheit {m.confidence}</div>
          <p className="strat-diag">{m.diagnosis}</p>
          {m.regime_view && <p className="small"><b>Marktlage:</b> {m.regime_view}</p>}
          {(m.actions || []).length > 0 && (
            <div className="strat-actions">
              {m.actions.map((a, i) => (
                <div key={i} className="strat-action">
                  <div><b>{a.label}</b><div className="dim small">{a.why}</div></div>
                  {a.kind !== "keep" && <button disabled={!!busy} onClick={() => act("strategist/act", { kind: a.kind }, a.kind)}>{busy === a.kind ? "…" : "Übernehmen"}</button>}
                </div>
              ))}
            </div>
          )}
          <div className="strat-grid small">
            {m.ideas?.length > 0 && <div><span className="dim">An den Think Tank (geheimer Test):</span> {m.ideas.join(", ")}</div>}
            {m.lab_test && <div><span className="dim">Fürs History Lab:</span> {m.lab_test}</div>}
            {m.watch && <div><span className="dim">Achten auf:</span> {m.watch}</div>}
          </div>
        </div>
      )}
      <div className="bank-actions">
        <button disabled={!!busy || d.busy || !d.ai || !d.enabled} onClick={() => act("strategist/ask", {}, "ask")}>{d.busy ? "denkt…" : "Jetzt fragen"}</button>
        {d.history?.length > 1 && <button className="linkish" onClick={() => setOld(!old)}>{old ? "weniger" : `frühere (${d.history.length - 1})`}</button>}
        {(d.pending || []).length > 0 && <span className="dim small">wartet: {d.pending.join(", ")}</span>}
        {msg && <span className="small">{msg}</span>}
      </div>
      {old && (d.history || []).slice(1).map((h) => (
        <div key={h.ts} className="strat-old small"><span className="dim">{when(h.ts)} · {(h.trigger || []).join(", ")}</span><div>{h.diagnosis}</div></div>
      ))}
    </>
  );
}
