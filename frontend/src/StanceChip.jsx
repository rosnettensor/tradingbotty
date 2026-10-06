import { useState } from "react";
import { api } from "./useBot.js";

// Your course (Mutig, Bunkern, Pause) while it lasts: what it is, how long it still runs, and a button to end it.
// Normal shows nothing. Set it in the chat ("heute mehr Risiko", "Gewinne bunkern", "Pause").
export default function StanceChip({ stance, compact = false }) {
  const [busy, setBusy] = useState(false);
  if (!stance || stance.key === "normal") return null;
  const hours = stance.until ? Math.max(0, (stance.until - Date.now() / 1000) / 3600) : null;
  const left = hours == null ? "" : hours >= 1 ? `noch ${Math.round(hours)} h` : `noch ${Math.max(1, Math.round(hours * 60))} min`;
  const end = async () => {
    setBusy(true);
    try { await api("stance", { key: "normal" }); } catch { /* the next poll shows the truth */ } finally { setBusy(false); }
  };
  return (
    <div className={`stance-chip stance-${stance.key} ${compact ? "compact" : ""}`} title={stance.what}>
      <span className="stance-name">Kurs {stance.name}</span>
      <span className="stance-left">{left}</span>
      <button className="mini" disabled={busy} onClick={end}>beenden</button>
    </div>
  );
}
