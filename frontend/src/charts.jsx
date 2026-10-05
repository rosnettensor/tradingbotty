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

// Price line with buy/sell markers and a hover readout. candles: [[ts, close]], trades: [{ts, side, price, ...}]
export function PriceChart({ candles, trades = [], height = 240, showAll = false }) {
  const [box, W] = useWidth([candles.length < 2]);
  const [hover, setHover] = useState(null);
  if (!candles || candles.length < 2) return <div ref={box} className="empty" style={{ height }}>no price history yet</div>;
  const H = height, padL = 58, padB = 20, padT = 10, padR = 12;
  const xs = candles.map((c) => c[0]), ys = candles.map((c) => c[1]);
  const x0 = xs[0], x1 = xs[xs.length - 1];
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (y1 - y0 < y1 * 1e-6) { y0 *= 0.999; y1 *= 1.001; }
  const pad = (y1 - y0) * 0.1; y0 -= pad; y1 += pad;
  const sx = (x) => padL + ((x - x0) / (x1 - x0 || 1)) * (W - padL - padR);
  const sy = (y) => padT + (1 - (y - y0) / (y1 - y0)) * (H - padT - padB);
  const line = candles.map((c) => `${sx(c[0]).toFixed(1)},${sy(c[1]).toFixed(1)}`).join(" ");
  const area = `${sx(x0)},${H - padB} ${line} ${sx(x1)},${H - padB}`;
  const up = ys[ys.length - 1] >= ys[0];
  const color = up ? "var(--green)" : "var(--red)";
  const shown = trades.filter((t) => (showAll || t.champion) && t.ts >= x0 - 60);
  const ticks = [0, 0.33, 0.66, 1].map((f) => y0 + pad + f * (y1 - y0 - 2 * pad));
  const pf = (p) => (p >= 1000 ? p.toFixed(0) : p >= 1 ? p.toFixed(2) : p.toPrecision(3));
  const tf = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const df = (ts) => new Date(ts * 1000).toLocaleDateString([], { day: "2-digit", month: "short" });
  const multiDay = x1 - x0 > 86400;
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const t = x0 + ((e.clientX - r.left - padL) / (W - padL - padR)) * (x1 - x0);
    let i = 0, best = Infinity;
    for (let k = 0; k < candles.length; k++) { const d = Math.abs(candles[k][0] - t); if (d < best) { best = d; i = k; } }
    const near = shown.find((tr) => Math.abs(sx(tr.ts) - sx(candles[i][0])) < 6);
    setHover({ c: candles[i], near });
  };
  const gid = `g${Math.round(x0)}${up ? "u" : "d"}`;
  return (
    <div ref={box} className="pricechart" style={{ height: H }}>
      <svg width={W} height={H} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <defs>
          <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={up ? "#39ff88" : "#ff3b5c"} stopOpacity="0.28" />
            <stop offset="100%" stopColor={up ? "#39ff88" : "#ff3b5c"} stopOpacity="0" />
          </linearGradient>
        </defs>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W - padR} y1={sy(t)} y2={sy(t)} className="grid" />
            <text x={padL - 6} y={sy(t) + 4} className="axis" textAnchor="end">{pf(t)}</text>
          </g>
        ))}
        <polygon points={area} fill={`url(#${gid})`} />
        <polyline points={line} fill="none" stroke={color} strokeWidth="1.6" />
        {shown.map((t, i) => {
          const x = sx(Math.max(x0, Math.min(x1, t.ts))), y = sy(t.price);
          const buy = t.side === "BUY";
          const s = t.champion ? 7 : 4;
          return (
            <g key={i} className={`marker ${buy ? "buy" : "sell"} ${t.champion ? "champ" : ""}`}>
              <line x1={x} x2={x} y1={padT} y2={H - padB} className="marker-line" />
              <polygon points={buy ? `${x},${y + 3} ${x - s},${y + 3 + s * 1.5} ${x + s},${y + 3 + s * 1.5}` : `${x},${y - 3} ${x - s},${y - 3 - s * 1.5} ${x + s},${y - 3 - s * 1.5}`} />
            </g>
          );
        })}
        <text x={padL} y={H - 4} className="axis">{multiDay ? df(x0) + " " : ""}{tf(x0)}</text>
        <text x={W - padR} y={H - 4} className="axis" textAnchor="end">{multiDay ? df(x1) + " " : ""}{tf(x1)}</text>
        {hover && (
          <g>
            <line x1={sx(hover.c[0])} x2={sx(hover.c[0])} y1={padT} y2={H - padB} className="cross" />
            <circle cx={sx(hover.c[0])} cy={sy(hover.c[1])} r="3.5" fill={color} />
          </g>
        )}
      </svg>
      {hover && (
        <div className="chart-tip" style={{ left: Math.min(W - 190, Math.max(0, sx(hover.c[0]) + 10)) }}>
          <div>{multiDay ? df(hover.c[0]) + " " : ""}{tf(hover.c[0])} · <b>{pf(hover.c[1])}</b></div>
          {hover.near && (
            <div className={hover.near.side === "BUY" ? "up" : "down"}>
              {hover.near.variant ? `${hover.near.variant}: ` : ""}{hover.near.side} ${hover.near.notional?.toFixed?.(2)}
              {hover.near.pnl != null ? ` (P&L ${hover.near.pnl >= 0 ? "+" : ""}${hover.near.pnl.toFixed(2)})` : ""}
              <div className="dim">{hover.near.reason}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Tiny ring gauge, value 0..1
export function Ring({ value, size = 44, color = "var(--cyan)", label }) {
  const r = size / 2 - 4, c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} className="ring">
      <circle cx={size / 2} cy={size / 2} r={r} className="ring-bg" />
      <circle cx={size / 2} cy={size / 2} r={r} stroke={color} strokeDasharray={`${c * Math.max(0, Math.min(1, value))} ${c}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`} className="ring-fg" />
      <text x="50%" y="54%" textAnchor="middle" dominantBaseline="middle">{label}</text>
    </svg>
  );
}
