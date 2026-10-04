// Small shared building blocks: sliders, toggles, chip lists, stat tiles, signal bars.
import { useEffect, useState } from "react";

export const SIGNAL_LABELS = {
  momentum: "Momentum", trend: "Trend", reversion: "Dip", breakout: "Breakout", swing: "Multi-day",
  hype: "Hype", news: "News", market: "Market",
};

// A slider that only reports when you let go, so dragging doesn't spam the bot.
export function Slider({ label, help, min, max, step, value, onCommit, changed, onReset, int, format, disabled }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  const commit = () => { if (v !== value) onCommit(int ? Math.round(v) : Number(v)); };
  const show = format ? format(v) : fmtNum(v, step);
  return (
    <div className={`slider ${changed ? "changed" : ""} ${disabled ? "disabled" : ""}`}>
      <div className="slider-head">
        <span className="slider-label" title={help}>{label}</span>
        <span className="slider-value">
          <input type="number" value={v} min={min} max={max} step={step} disabled={disabled}
            onChange={(e) => setV(e.target.value === "" ? "" : Number(e.target.value))}
            onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} aria-label={label} />
          {format && <span className="slider-show">{show}</span>}
          {changed && onReset && <button className="mini" onClick={onReset} title="back to default">↺</button>}
        </span>
      </div>
      <input type="range" min={min} max={max} step={step} value={v === "" ? min : v} disabled={disabled}
        onChange={(e) => setV(Number(e.target.value))} onMouseUp={commit} onTouchEnd={commit} onKeyUp={commit}
        style={{ "--fill": `${((Number(v) - min) / (max - min || 1)) * 100}%` }} aria-label={label} />
      {help && <div className="slider-help">{help}</div>}
    </div>
  );
}

function fmtNum(v, step) {
  if (v === "" || v == null) return "";
  const d = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
  return Number(v).toFixed(d);
}

export function Toggle({ label, help, checked, onChange, disabled }) {
  return (
    <label className={`toggle-row ${disabled ? "disabled" : ""}`} title={help}>
      <span className={`switch ${checked ? "on" : ""}`} onClick={() => !disabled && onChange(!checked)} role="switch"
        aria-checked={checked} tabIndex={0} onKeyDown={(e) => (e.key === " " || e.key === "Enter") && !disabled && onChange(!checked)}>
        <i />
      </span>
      <span>
        <span className="toggle-label">{label}</span>
        {help && <span className="slider-help">{help}</span>}
      </span>
    </label>
  );
}

export function ChipList({ items, onRemove, onAdd, placeholder, status = {}, statusKey = (x) => x, locked = [], nameField }) {
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const add = async () => {
    if (!text.trim()) return;
    setBusy(true); setErr("");
    try {
      const r = await onAdd(text.trim(), name.trim());
      if (r && r.ok === false) setErr(r.error);
      else { setText(""); setName(""); }
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="chips">
      <div className="chip-row">
        {items.map((it) => {
          const st = status[statusKey(it)];
          return (
            <span key={it} className={`chip ${st ? (st.ok ? "ok" : "bad") : ""}`} title={st?.error || (st ? `${st.items} items` : "")}>
              {st && <i className="chip-dot" />}{it}
              {locked.includes(it)
                ? <span className="dim" title="a strategy holds it"> ●</span>
                : <button className="chip-x" onClick={async () => { const r = await onRemove(it); if (r?.ok === false) setErr(r.error); }} aria-label={`remove ${it}`}>×</button>}
            </span>
          );
        })}
      </div>
      <div className="chip-add">
        {nameField && <input value={name} onChange={(e) => setName(e.target.value)} placeholder="name" style={{ width: 110 }} />}
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder}
          onKeyDown={(e) => e.key === "Enter" && add()} disabled={busy} />
        <button onClick={add} disabled={busy}>{busy ? "checking…" : "add"}</button>
      </div>
      {err && <div className="err">{err}</div>}
    </div>
  );
}

export function Stat({ label, value, sub, tone, title }) {
  return (
    <div className="stat" title={title}>
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${tone || ""}`}>{value}</div>
      {sub != null && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

// Horizontal bars centered on zero, one per signal. values: {name: number}, scale: max abs value.
export function SignalBars({ values, scale = 1, labels = SIGNAL_LABELS, compact }) {
  return (
    <div className={`sigbars ${compact ? "compact" : ""}`}>
      {Object.entries(values).map(([k, v]) => {
        const w = Math.min(50, (Math.abs(v) / (scale || 1)) * 50);
        return (
          <div className="sigbar" key={k} title={`${labels[k] || k}: ${v >= 0 ? "+" : ""}${v.toFixed(3)}`}>
            {!compact && <span className="sig-name">{labels[k] || k}</span>}
            <div className="track"><div className={v >= 0 ? "pos" : "neg"} style={{ width: `${w}%`, [v >= 0 ? "left" : "right"]: "50%" }} /></div>
            {!compact && <span className={`sig-val ${v >= 0 ? "up" : "down"}`}>{v >= 0 ? "+" : ""}{v.toFixed(2)}</span>}
          </div>
        );
      })}
    </div>
  );
}

// Score bar with the strategy's buy and sell thresholds drawn on it.
export function ScoreBar({ score, entry, exit }) {
  const pos = (x) => `${Math.max(0, Math.min(100, (x + 1) * 50))}%`;
  const w = Math.min(50, Math.abs(score) * 50);
  return (
    <div className="scorebar">
      <div className={score >= 0 ? "pos" : "neg"} style={{ width: `${w}%`, [score >= 0 ? "left" : "right"]: "50%" }} />
      {entry != null && <i className="mark buy" style={{ left: pos(entry) }} title={`buy above ${entry}`} />}
      {exit != null && <i className="mark sell" style={{ left: pos(exit) }} title={`sell below ${exit}`} />}
    </div>
  );
}

export function Tabs({ value, options, onChange }) {
  return (
    <div className="seg">
      {options.map(([k, label]) => (
        <button key={k} className={value === k ? "active" : ""} onClick={() => onChange(k)}>{label}</button>
      ))}
    </div>
  );
}
