// Read-only presentation rules. These never authorize an order.
export function missionState(ops, state, connected, error, now) {
  const fresh = connected && !error && ops?.ts && now - ops.ts >= -10 && now - ops.ts <= 90;
  const rows = ops?.order_trace || [];
  const pending = rows.find(o => o.state === "submitting");
  const unresolved = (ops?.architecture?.journal || []).some(o => o.state === "uncertain" && o.count > 0);
  const running = (state.nodes || []).filter(n => n.status === "running" && n.last_run && now - n.last_run < 90);
  const channels = [running.some(n => n.kind === "source"), running.some(n => ["brain", "fast", "risk"].includes(n.id)), !!pending];
  const armed = ops?.lanes?.filter(l => l.status === "armed") || [];
  const enabled = ops?.lanes?.filter(l => l.enabled) || [];
  let phase, title, detail;
  if (!fresh) [phase, title, detail] = ["offline", "Verbindung wird geprüft", error || "Der aktuelle Serverstatus ist noch nicht bestätigt."];
  else if (unresolved) [phase, title, detail] = ["blocked", "Order braucht Abgleich", "Ein Börsenauftrag ist ungeklärt. Keine erneute Übermittlung, bis Fusion und Buchhaltung abgeglichen sind."];
  else if (pending) [phase, title, detail] = ["submitting", "Börsenantwort ausstehend", `${pending.side} ${pending.symbol} wurde im Orderjournal erfasst. Eine Ausführung ist noch nicht bestätigt.`];
  else if (!ops.execution_allowed) [phase, title, detail] = ["paused", "Handel pausiert", ops.global_reasons?.join(" · ") || "Echtgeld-Ausführung ist gesperrt."];
  else if (channels[1]) [phase, title, detail] = ["checking", "Handelsentscheidung wird geprüft", "Strategie oder Risikoprüfung meldet Aktivität. Das ist noch keine Order."];
  else if (!enabled.length) [phase, title, detail] = ["paused", "Keine Strategie aktiviert", "Live ist verbunden. Daily und Fast sind ausgeschaltet."];
  else if (!armed.length) [phase, title, detail] = ["blocked", "Käufe warten auf Freigaben", enabled.flatMap(l => l.reasons || [])[0] || "Die Strategieprüfungen sind noch nicht erfüllt."];
  else if (!ops.wallet_fresh) [phase, title, detail] = ["offline", "Kontostand wird aktualisiert", "Der letzte Kontostand ist veraltet. Die Anzeige bestätigt keine aktuelle Kaufbereitschaft."];
  else if (channels[0]) [phase, title, detail] = ["reading", "Marktdaten werden gelesen", "Eine Datenquelle meldet Aktivität. Neue Käufe benötigen weiterhin ein gültiges Signal."];
  else [phase, title, detail] = ["waiting", "Bereit für das nächste Signal", "Der Bot wartet auf seine Regeln. Warten ist eine Entscheidung, kein technischer Leerlauf."];
  return { phase, title, detail, fresh: !!fresh, channels: fresh ? [channels[0], channels[1], phase === "submitting"] : [false, false, false], running: fresh ? running : [] };
}

export function countdown(at, now) {
  if (!at) return "wird ermittelt";
  const s = Math.ceil(at - now);
  if (s <= 0) return "Prüfung fällig";
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : m ? `${m}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}
