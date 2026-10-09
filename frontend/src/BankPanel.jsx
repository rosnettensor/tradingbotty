import { useState } from "react";
import { api, pctColor, usePoll } from "./useBot.js";

const STAGE = {
  incumbent: ["IM AMT", "gov"], passed: ["BESTANDEN", "you"], shadow: ["IM SCHATTEN", "parl"],
  failed: ["DURCHGEFALLEN", "court"], lab: ["WARTET AUFS LABOR", "serv"],
};
const MODE = { shadow: ["SCHATTEN: zeigt nur", "parl"], probe: ["PROBE: kleiner echter Anteil", "you"], live: ["LIVE: verteilt echtes Geld", "court"] };
const COLORS = ["var(--cyan)", "var(--magenta)", "var(--green)", "var(--amber)", "#9b8cff", "#ff8a5c", "#5cc8ff"];
const pct = (x, d = 1) => (x == null ? "–" : `${x >= 0 ? "+" : ""}${Number(x).toFixed(d)}%`);
const day = (ts) => new Date(ts * 1000).toLocaleDateString([], { day: "2-digit", month: "2-digit" });

function SplitBar({ split, color }) {
  const parts = Object.entries(split || {}).sort((a, b) => b[1] - a[1]);
  return (
    <div className="split">
      <div className="split-bar">
        {parts.map(([k, v]) => <i key={k} title={`${k}: ${(v * 100).toFixed(0)}%`} style={{ width: `${v * 100}%`, background: k === "Cash" ? "var(--line)" : color(k) }} />)}
      </div>
      <div className="split-legend">
        {parts.map(([k, v]) => <span key={k}><i style={{ background: k === "Cash" ? "var(--line)" : color(k) }} />{k.length > 48 ? `${k.slice(0, 46)}…` : k} <b>{(v * 100).toFixed(0)}%</b></span>)}
      </div>
    </div>
  );
}

/** Bauplan 2, phase 2: parliament proposes, the examiner checks, the bank splits the daily brain's money. */
export default function BankPanel() {
  const [d, reload] = usePoll("bank", 120000);
  const [word, setWord] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [ask, setAsk] = useState(null);
  if (!d) return <div className="empty small">loading…</div>;
  const rep = d.report;
  const rows = rep?.rows || [];
  const color = (name) => COLORS[Math.max(0, rows.findIndex((r) => r.name === name)) % COLORS.length];
  const act = async (fn) => {
    setBusy(true);
    try { await fn(); setMsg(""); reload(); } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  };
  return (
    <>
      <div className="row-head">
        <h3>🏦 BANK & PRÜFER <span className="dim">· wer schlägt vor, wer prüft, wer bekommt das Geld des Daily Brain</span></h3>
        <span className={`role-chip ${MODE[d.mode]?.[1] || "parl"}`}>{MODE[d.mode]?.[0] || "SCHATTEN"}</span>
      </div>
      <div className="powers-line small">
        <span><b className="parl-t">Parlament</b> schlägt vor (History Lab, Think Tank)</span>
        <span>→ <b className="court-t">Prüfer</b>: robust, doppelte Gebühren, {d.shadow_days} Tage Schatten</span>
        <span>→ <b className="you-t">Bank</b>: Thompson Sampling, max {Math.round(d.max_share * 100)}% pro Strategie</span>
        <span>→ <b className="gov-t">Daily Brain</b> kauft</span>
        <span>→ <b>Punktestand</b> misst</span>
      </div>
      {!rep ? (
        <div className="empty small">Die Bank rechnet nach dem nächsten Lauf des History Labs (einmal am Tag), oder jetzt: <button disabled={busy} onClick={() => act(() => api("bank/refresh", {}))}>Runde jetzt</button></div>
      ) : (
        <>
          <div className="bank-top">
            <div>
              <div className="dim small">ECHTES GELD{d.live ? "" : " (wenn live)"}: nur Geprüfte und Cash</div>
              <SplitBar split={rep.split} color={color} />
            </div>
            {rep.probe_split && (
              <div>
                <div className="dim small">PROBE{d.mode === "probe" ? " (läuft)" : ""}: Strategie im Amt plus {Math.round(d.probe_share * 100)}% für {rep.probe ? "den besten Kandidaten im Schatten" : "niemanden (kein Kandidat)"}</div>
                <SplitBar split={rep.probe_split} color={color} />
              </div>
            )}
            <div>
              <div className="dim small">SCHATTEN: wenn alle geprüft wären</div>
              <SplitBar split={rep.shadow_split} color={color} />
            </div>
            <div className="bank-result">
              <small>Bank im Schatten{d.since ? ` seit ${day(d.since)}` : ""}</small>
              <b className={pctColor(d.shadow_pct)}>{pct(d.shadow_pct, 2)}</b>
            </div>
          </div>
          <div className="tbl-wrap">
            <table className="bank-table">
              <thead><tr><th>Strategie</th><th>Prüfer</th><th>Labor</th><th>Schatten</th><th>Bank</th></tr></thead>
              <tbody>
                {rows.map((r) => {
                  const [label, cls] = STAGE[r.stage] || [r.stage, ""];
                  return (
                    <tr key={r.name}>
                      <td><i className="dot" style={{ background: color(r.name) }} /> <b>{r.name}</b><div className="dim small">vorgeschlagen: {r.by}</div></td>
                      <td><span className={`role-chip ${cls}`}>{label}</span><div className="dim small">{r.why}</div></td>
                      <td className="num">{r.test?.cagr_pct != null ? `${pct(r.test.cagr_pct, 0)}/J` : "–"}<div className="dim small">tiefster Fall {r.test?.max_dd_pct ?? "–"}% · {r.years} J. besser als BTC{r.robust ? " · robust ✓" : ""}</div></td>
                      <td className="num"><span className={pctColor(r.shadow_pct)}>{pct(r.shadow_pct, 2)}</span><div className="dim small">{r.days} Tage · BTC {pct(r.btc_pct)}</div></td>
                      <td className="num"><b>{d.mode === "probe" ? r.probe_pct : r.share_pct}%</b> echt<div className="dim small">gewinnt {r.win_pct}% der Ziehungen · erwartet {pct(r.belief_pct_yr, 0)}/J</div></td>
                    </tr>
                  );
                })}
                <tr><td colSpan={4} className="dim small">Cash gewinnt {rep.cash_win_pct}% der Ziehungen</td><td className="num"><b>{(((d.mode === "probe" ? rep.probe_split : rep.split)?.Cash || 0) * 100).toFixed(1)}%</b> echt</td></tr>
              </tbody>
            </table>
          </div>
          <div className="bank-actions">
            <button disabled={busy} onClick={() => act(() => api("bank/refresh", {}))}>Runde jetzt</button>
            <div className="seg" role="group" aria-label="Was die Bank darf">
              {["shadow", "probe", "live"].map((m) => (
                <button key={m} className={d.mode === m ? "active" : ""} disabled={busy}
                  onClick={() => (m === "shadow" ? act(() => api("bank", { mode: "shadow" })) : setAsk(m === d.mode ? null : m))}>
                  {{ shadow: "Schatten", probe: "Probe", live: "Live" }[m]}
                </button>
              ))}
            </div>
            {ask === "probe" && d.mode !== "probe" && (
              <button className="primary" disabled={busy || !rep.probe} onClick={() => act(async () => { await api("bank", { mode: "probe", confirm: "PROBE" }); setAsk(null); })}>
                Ja: {Math.round(d.probe_share * 100)}% echt für {rep.probe ? `"${rep.probe.length > 30 ? `${rep.probe.slice(0, 28)}…` : rep.probe}"` : "–"}
              </button>
            )}
            {ask === "live" && d.mode !== "live" && (
              <>
                <input id="bank-live-word" value={word} onChange={(e) => setWord(e.target.value)} placeholder="BANK LIVE tippen" aria-label="Zum Einschalten BANK LIVE tippen" />
                <button disabled={busy || word.trim().toUpperCase() !== "BANK LIVE"} onClick={() => act(async () => { await api("bank", { mode: "live", confirm: word }); setAsk(null); })}>Bank verteilt echtes Geld</button>
              </>
            )}
            <span className="dim small">{msg || (d.mode === "live"
              ? "Der Daily Brain kauft ab seiner nächsten Entscheidung den Mix oben. Alle Prüfungen des Risk Officers bleiben."
              : d.mode === "probe"
                ? `Die Strategie im Amt behält ${100 - Math.round(d.probe_share * 100)}%, der beste Kandidat handelt ${Math.round(d.probe_share * 100)}% mit echtem Geld (pro Coin mindestens 30 CHF, zusammen höchstens 25%). Alle Prüfungen des Risk Officers bleiben.`
                : ask === "probe" ? (rep.probe
                  ? "Probe: der beste Kandidat, der noch im Schatten geprüft wird, handelt ab sofort einen kleinen echten Anteil. Du musst nicht 14 Tage warten."
                  : "Probe geht erst, wenn ein Kandidat das History Lab besteht (robust und mit doppelten Gebühren im Plus). Gerade besteht keiner. Daily Brain und Fast Pot handeln trotzdem nach ihren Regeln.")
                  : rep.eligible?.length > 1 ? "Mehrere Strategien sind geprüft: live würde die Bank das Geld wie oben verteilen."
                    : "Noch hat keine neue Strategie den Prüfer bestanden: live würde nur die Strategie im Amt Geld bekommen (plus Cash, falls Cash oft gewinnt).")}</span>
          </div>
        </>
      )}
    </>
  );
}
