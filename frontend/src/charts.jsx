// Small hand-rolled SVG charts: no chart library needed.
import { useEffect, useRef, useState } from "react";

export function Sparkline({ values, width = 60, height = 18 }) {
  if (!values || values.length < 2) return <svg width={width} height={height} />;
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const d = values.map((v, i) => `${(i / (values.length - 1)) * width},${height - ((v - min) / span) * height}`).join(" ");
  const up = values[values.length - 1] >= values[0];
  return (
    <svg width={width} height={height} className="spark">
      <polyline points={d} fill="none" stroke={up ? "var(--green)" : "var(--red)"} strokeWidth="1.2" />
    </svg>
  );
}

// series: [{ id, name, color, points: [[ts, value], ...] }]
export function LineChart({ series, height = 220, unit = "", baseline = null, xfmt = null }) {
  const box = useRef(null);
  const [W, setW] = useState(800);
  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(200, e.contentRect.width)));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, [series.flatMap((s) => s.points).length < 2]);
  const all = series.flatMap((s) => s.points);
  if (all.length < 2) return <div ref={box} style={{ height }}><div className="empty" style={{ height }}>collecting data… the first points arrive within a minute</div></div>;
  const H = height, padL = 52, padB = 22, padT = 10, padR = 10;
  const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
  if (baseline != null) ys.push(baseline);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (y1 - y0 < 1e-9) { y0 -= 1; y1 += 1; }
  const pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad;
  const sx = (x) => padL + ((x - x0) / (x1 - x0 || 1)) * (W - padL - padR);
  const sy = (y) => padT + (1 - (y - y0) / (y1 - y0)) * (H - padT - padB);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => y0 + f * (y1 - y0));
  const tfmt = xfmt || ((ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  return (
    <div ref={box} style={{ height: H }}>
    <svg width={W} height={H} className="linechart">
      {ticks.map((t) => (
        <g key={t}>
          <line x1={padL} x2={W - padR} y1={sy(t)} y2={sy(t)} className="grid" />
          <text x={padL - 6} y={sy(t) + 4} className="axis" textAnchor="end">{t.toFixed(Math.abs(t) < 10 ? 2 : 1)}{unit}</text>
        </g>
      ))}
      {baseline != null && <line x1={padL} x2={W - padR} y1={sy(baseline)} y2={sy(baseline)} className="baseline" />}
      <text x={padL} y={H - 4} className="axis">{tfmt(x0)}</text>
      <text x={W - padR} y={H - 4} className="axis" textAnchor="end">{tfmt(x1)}</text>
      {series.map((s) => (
        <polyline key={s.id} fill="none" stroke={s.color} strokeWidth={s.bold ? 2.4 : 1.4}
          points={s.points.map((p) => `${sx(p[0])},${sy(p[1])}`).join(" ")} />
      ))}
    </svg>
    </div>
  );
}

function useWidth(deps) {
  const box = useRef(null);
  const [W, setW] = useState(600);
  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(160, e.contentRect.width)));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, deps);
  return [box, W];
}

// Daily candles with the Daily Brain's lines: buy above the 20-day high (dashed green), sell under the 10-day low
// (dashed red), today's live price as a last candle, and the bot's real trades as arrows.
export function DailyChart({ d, entry = 20, exit = 10, height = 260 }) {
  const [box, W] = useWidth([!!d]);
  const [hover, setHover] = useState(null);
  if (!d || !d.c?.length) return <div ref={box} className="empty" style={{ height }}>loading daily candles…</div>;
  const n = d.c.length;
  const days = [...d.days], o = [...d.o], h = [...d.h], l = [...d.l], c = [...d.c];
  if (d.live && Date.now() / 1000 - days[n - 1] > 86400 && d.live < c[n - 1] * 2 && d.live > c[n - 1] / 2) {  // today, not closed yet
    const last = c[n - 1];
    days.push(days[n - 1] + 86400); o.push(last); c.push(d.live); h.push(Math.max(last, d.live)); l.push(Math.min(last, d.live));
  }
  const N = c.length;
  const hiLine = c.map((_, i) => (i >= entry ? Math.max(...h.slice(i - entry, i).filter((x) => x != null)) : null));
  const loLine = c.map((_, i) => (i >= exit ? Math.min(...l.slice(i - exit, i).filter((x) => x != null)) : null));
  const vals = [...h, ...l, ...hiLine, ...loLine].filter((x) => x != null && isFinite(x));
  let y0 = Math.min(...vals), y1 = Math.max(...vals);
  const pad = (y1 - y0) * 0.06 || y1 * 0.01; y0 -= pad; y1 += pad;
  const H = height, padL = 58, padB = 20, padT = 8, padR = 10;
  const cw = (W - padL - padR) / N;
  const sx = (i) => padL + (i + 0.5) * cw;
  const sy = (y) => padT + (1 - (y - y0) / (y1 - y0)) * (H - padT - padB);
  const path = (arr) => arr.map((v, i) => (v == null ? null : `${sx(i).toFixed(1)},${sy(v).toFixed(1)}`)).filter(Boolean).join(" ");
  const pf = (p) => (p >= 1000 ? p.toFixed(0) : p >= 1 ? p.toFixed(2) : p.toPrecision(3));
  const df = (ts) => new Date(ts * 1000).toLocaleDateString([], { day: "2-digit", month: "short" });
  const ticks = [0.1, 0.4, 0.7, 0.95].map((f) => y0 + f * (y1 - y0));
  const idx = (ts) => Math.max(0, Math.min(N - 1, Math.floor((ts - days[0]) / 86400)));
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.max(0, Math.min(N - 1, Math.floor((e.clientX - r.left - padL) / cw)));
    setHover(i);
  };
  return (
    <div ref={box} className="pricechart" style={{ height: H }}>
      <svg width={W} height={H} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W - padR} y1={sy(t)} y2={sy(t)} className="grid" />
            <text x={padL - 6} y={sy(t) + 4} className="axis" textAnchor="end">{pf(t)}</text>
          </g>
        ))}
        <polyline points={path(hiLine)} fill="none" stroke="var(--green)" strokeDasharray="4 3" strokeWidth="1.2" opacity="0.8" />
        <polyline points={path(loLine)} fill="none" stroke="var(--red)" strokeDasharray="4 3" strokeWidth="1.2" opacity="0.8" />
        {c.map((cl, i) => {
          if (cl == null || o[i] == null) return null;
          const up = cl >= o[i];
          const col = up ? "var(--green)" : "var(--red)";
          const x = sx(i), bw = Math.max(1, cw * 0.62);
          return (
            <g key={i} opacity={i === N - 1 && N > n ? 0.55 : 1}>
              <line x1={x} x2={x} y1={sy(h[i] ?? cl)} y2={sy(l[i] ?? cl)} stroke={col} strokeWidth="1" />
              <rect x={x - bw / 2} width={bw} y={sy(Math.max(o[i], cl))} height={Math.max(1, Math.abs(sy(o[i]) - sy(cl)))} fill={col} />
            </g>
          );
        })}
        {(d.trades || []).map((t, k) => {
          const i = idx(t.ts), buy = t.side === "BUY";
          const y = sy(t.price || c[i]);
          const x = sx(i);
          return (
            <g key={k} className={`marker ${buy ? "buy" : "sell"} champ`}>
              <polygon points={buy ? `${x},${y + 4} ${x - 6},${y + 14} ${x + 6},${y + 14}` : `${x},${y - 4} ${x - 6},${y - 14} ${x + 6},${y - 14}`} />
            </g>
          );
        })}
        <text x={padL} y={H - 4} className="axis">{df(days[0])}</text>
        <text x={W - padR} y={H - 4} className="axis" textAnchor="end">{N > n ? "today (live)" : df(days[N - 1])}</text>
        {hover != null && <line x1={sx(hover)} x2={sx(hover)} y1={padT} y2={H - padB} className="cross" />}
      </svg>
      {hover != null && (
        <div className="chart-tip" style={{ left: Math.min(W - 210, Math.max(0, sx(hover) + 10)) }}>
          <div>{hover === N - 1 && N > n ? "today, live" : df(days[hover])} · close <b>{pf(c[hover])}</b></div>
          <div className="dim">high {pf(h[hover])} · low {pf(l[hover])}</div>
          {hiLine[hover] != null && <div className="up">buy above {pf(hiLine[hover])} ({entry}-day high)</div>}
          {loLine[hover] != null && <div className="down">sell under {pf(loLine[hover])} ({exit}-day low)</div>}
          {(d.trades || []).filter((t) => idx(t.ts) === hover).map((t, k) => (
            <div key={k} className={t.side === "BUY" ? "up" : "down"}>{t.side} {t.notional?.toFixed(2)} · {t.reason}</div>
          ))}
        </div>
      )}
    </div>
  );
}
