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
export function LineChart({ series, height = 220, unit = "", baseline = null }) {
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
  const tfmt = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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
