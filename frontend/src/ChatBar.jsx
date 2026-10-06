import { Fragment, useEffect, useRef, useState } from "react";
import { api } from "./useBot.js";

// "Ask the bot": Space (outside a text field) opens a command-palette style bar; the bot answers from its own state.
const KEY = "tb-chat";
const SUGGESTIONS = [
  "Wie geht's dem Konto?",
  "Was hat der Daily Brain heute entschieden?",
  "Was macht der Fast-Topf?",
  "Läuft jeder Agent sauber?",
  "kaufe ADA für 30 CHF im Fast Pot",
];

const load = () => {
  try { const m = JSON.parse(sessionStorage.getItem(KEY)); return Array.isArray(m) ? m : []; } catch { return []; }
};

const typing = (el) => !!el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable);

// **bold** is the only markup worth showing; everything else stays plain text
const Text = ({ s }) => s.split(/(\*\*[^*]+\*\*)/g).map((p, i) =>
  p.startsWith("**") && p.endsWith("**") && p.length > 4 ? <b key={i}>{p.slice(2, -2)}</b> : <Fragment key={i}>{p}</Fragment>);

export default function ChatBar() {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState(load);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const input = useRef(null);
  const log = useRef(null);

  useEffect(() => { try { sessionStorage.setItem(KEY, JSON.stringify(msgs.slice(-40))); } catch { /* private mode */ } }, [msgs]);
  useEffect(() => {
    const onKey = (e) => {
      if (open && e.key === "Escape") { e.preventDefault(); setOpen(false); return; }
      if (open || e.key !== " " || e.repeat || e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      e.preventDefault();
      setOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  useEffect(() => { if (open) setTimeout(() => input.current?.focus(), 0); }, [open]);
  useEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight; }, [msgs, busy, open]);

  const send = async (q) => {
    const message = (q ?? text).trim();
    if (!message || busy) return;
    const history = msgs.filter((m) => !m.error).map(({ role, content }) => ({ role, content })).slice(-8);
    setMsgs((m) => [...m, { role: "user", content: message }]);
    setText("");
    setBusy(true);
    try {
      const r = await api("chat", { message, history });
      setMsgs((m) => [...m, { role: "assistant", content: r.answer || "…", cost: r.cost, offline: !r.ok && !r.order, order: r.order }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", content: `Keine Antwort: ${e.message}`, error: true }]);
    } finally {
      setBusy(false);
      input.current?.focus();
    }
  };

  // a proposed order: "Ausführen" sends it (one-time token), "Abbrechen" drops it; either way the buttons go away
  const settle = (i, state) => setMsgs((m) => m.map((x, j) => (j === i ? { ...x, order: { ...x.order, state } } : x)));
  const confirm = async (i) => {
    const o = msgs[i]?.order;
    if (!o || o.state || busy) return;
    settle(i, "sending");
    setBusy(true);
    try {
      const r = await api("chat/confirm", { token: o.token });
      settle(i, r.ok ? "done" : "failed");
      setMsgs((m) => [...m, { role: "assistant", content: r.answer, offline: !r.ok }]);
    } catch (e) {
      settle(i, "failed");
      setMsgs((m) => [...m, { role: "assistant", content: `Nicht gesendet: ${e.message}`, error: true }]);
    } finally {
      setBusy(false);
    }
  };

  const phone = typeof window !== "undefined" && window.matchMedia?.("(max-width: 640px)").matches;
  return (
    <>
      <button className="chat-fab" onClick={() => setOpen(true)} aria-label="Ask the bot" title="Ask the bot">💬</button>
      {open && (
        <div className="chat-backdrop" onMouseDown={(e) => e.target === e.currentTarget && setOpen(false)}>
          <div className="chat-box" role="dialog" aria-label="Ask the bot">
            {(msgs.length > 0 || busy) && (
              <div className="chat-log" ref={log}>
                {msgs.map((m, i) => (
                  <div key={i} className={`chat-msg ${m.role === "user" ? "me" : "bot"}${m.error || m.offline ? " warn" : ""}`}>
                    <div className="chat-bubble"><Text s={m.content} /></div>
                    {m.order && <OrderButtons o={m.order} busy={busy} onGo={() => confirm(i)} onDrop={() => settle(i, "dropped")} />}
                    {m.cost > 0 && <div className="chat-cost">${m.cost.toFixed(4)}</div>}
                  </div>
                ))}
                {busy && <div className="chat-msg bot"><div className="chat-bubble chat-thinking">thinking…</div></div>}
              </div>
            )}
            {!msgs.length && !busy && (
              <div className="chat-chips">
                {SUGGESTIONS.map((s) => <button key={s} onClick={() => send(s)}>{s}</button>)}
              </div>
            )}
            <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(); }}>
              <span className="chat-prompt" aria-hidden>›</span>
              <input ref={input} value={text} maxLength={2000} onChange={(e) => setText(e.target.value)}
                placeholder={phone ? "Frag den Bot…" : "Frag den Bot… (press space to ask the bot · Esc closes)"}
                enterKeyHint="send" autoComplete="off" />
              {msgs.length > 0 && !busy && (
                <button type="button" className="chat-clear" onClick={() => setMsgs([])} title="Start a new conversation">neu</button>
              )}
            </form>
          </div>
        </div>
      )}
    </>
  );
}

function OrderButtons({ o, busy, onGo, onDrop }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (o.state) return undefined;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [o.state]);
  const left = Math.max(0, Math.round(o.until - now));
  if (o.state === "done") return <div className="chat-order-state up">gesendet</div>;
  if (o.state === "failed") return <div className="chat-order-state down">nicht gesendet</div>;
  if (o.state === "dropped") return <div className="chat-order-state">abgebrochen</div>;
  if (o.state === "sending") return <div className="chat-order-state">sende…</div>;
  if (!left) return <div className="chat-order-state">abgelaufen: schreib die Order nochmal</div>;
  return (
    <div className="chat-order">
      <button className={`chat-go ${o.side === "SELL" ? "sell" : "buy"}`} disabled={busy} onClick={onGo}>
        {o.side === "SELL" ? "Verkaufen" : "Kaufen"}: Ausführen
      </button>
      <button className="chat-drop" onClick={onDrop}>Abbrechen</button>
      <span className="chat-order-left">{left}s</span>
    </div>
  );
}
