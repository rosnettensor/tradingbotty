// Small shared building blocks: sliders, toggles, chip lists, stat tiles, tabs.
import { useEffect, useState } from "react";

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

export function Tabs({ value, options, onChange }) {
  return (
    <div className="seg">
      {options.map(([k, label]) => (
        <button key={k} className={value === k ? "active" : ""} onClick={() => onChange(k)}>{label}</button>
      ))}
    </div>
  );
}
