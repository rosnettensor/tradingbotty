// TradingBotty home-screen widget for the free iPhone app "Scriptable".
// Read-only: it shows your numbers and can never trade. Tap it to open the dashboard.
// The Controls tab fills in your server address and widget key; the key only opens these numbers.
const DASH = "__URL__";
const API = DASH + "/api/widget?key=__KEY__";

const C = {
  bg1: new Color("#060a14"), bg2: new Color("#0d1730"), text: new Color("#eef3ff"), dim: new Color("#7f91b5"),
  green: new Color("#39ff88"), red: new Color("#ff4d6a"), cyan: new Color("#4fe3ff"), amber: new Color("#ffb020"),
};

function money(n) {
  if (n === null || n === undefined) return "–";
  return Number(n).toLocaleString("de-CH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function signed(n) {
  if (n === null || n === undefined) return "–";
  return (n >= 0 ? "+" : "−") + money(Math.abs(n));
}
function tone(n) {
  return n === null || n === undefined ? C.dim : n >= 0 ? C.green : C.red;
}
function hm(ts) {
  const d = new Date(ts * 1000);
  return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
}

async function load() {
  try {
    const r = new Request(API);
    r.timeoutInterval = 15;
    const d = await r.loadJSON();
    if (r.response && r.response.statusCode !== 200) throw new Error(d.detail || "HTTP " + r.response.statusCode);
    Keychain.set("tb-widget-last", JSON.stringify(d));
    return { d: d, stale: false };
  } catch (e) {
    const why = String((e && e.message) || e);
    if (Keychain.contains("tb-widget-last")) return { d: JSON.parse(Keychain.get("tb-widget-last")), stale: true, why: why };
    return { d: null, why: why };
  }
}

function text(stack, s, size, color, bold) {
  const t = stack.addText(s);
  t.font = bold ? Font.boldRoundedSystemFont(size) : Font.mediumRoundedSystemFont(size);
  t.textColor = color;
  t.lineLimit = 1;
  t.minimumScaleFactor = 0.6;
  return t;
}

function spark(values, w, h, color) {
  const ctx = new DrawContext();
  ctx.size = new Size(w, h);
  ctx.opaque = false;
  ctx.respectScreenScale = true;
  if (!values || values.length < 2) return ctx.getImage();
  const lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
  const span = hi - lo || 1;
  const pts = values.map(function (v, i) {
    return new Point((i / (values.length - 1)) * w, h - 3 - ((v - lo) / span) * (h - 6));
  });
  const fill = new Path();
  fill.move(new Point(0, h));
  pts.forEach(function (p) { fill.addLine(p); });
  fill.addLine(new Point(w, h));
  fill.closeSubpath();
  ctx.addPath(fill);
  ctx.setFillColor(new Color(color.hex, 0.14));
  ctx.fillPath();
  const line = new Path();
  line.move(pts[0]);
  pts.slice(1).forEach(function (p) { line.addLine(p); });
  ctx.addPath(line);
  ctx.setStrokeColor(color);
  ctx.setLineWidth(2);
  ctx.strokePath();
  return ctx.getImage();
}

function header(stack, d, stale) {
  const row = stack.addStack();
  row.centerAlignContent();
  const live = d.mode === "LIVE" && !d.kill;
  text(row, live ? "● LIVE" : d.kill ? "■ KILL" : "○ STANDBY", 10, live ? C.green : C.amber, true);
  row.addSpacer();
  text(row, (stale ? "⚠ " : "") + hm(d.ts), 10, stale ? C.amber : C.dim, false);
}

function money_block(stack, d, big) {
  text(stack, "TRADINGBOTTY", 9, C.cyan, true);
  stack.addSpacer(2);
  const row = stack.addStack();
  row.bottomAlignContent();
  text(row, money(d.total), big, C.text, true);
  row.addSpacer(3);
  text(row, d.currency || "CHF", 11, C.dim, true);
  stack.addSpacer(3);
  const e = stack.addStack();
  text(e, "Bot ", 11, C.dim, false);
  text(e, signed(d.bot_edge), 11, tone(d.bot_edge), true);
  const c = stack.addStack();
  text(c, "24h ", 11, C.dim, false);
  text(c, signed(d.change_24h), 11, tone(d.change_24h), true);
}

function details(stack, d, lines) {
  const rows = [];
  rows.push(["🧠", d.brain && d.brain.length ? d.brain.join(" · ") : "Cash"]);
  if (d.fast) rows.push(["⚡", money(d.fast.value) + (d.fast.coins.length ? " · " + d.fast.coins.join(" ") : " · wartet")]);
  if (d.course) rows.push(["🧭", "Kurs " + d.course]);
  else if (d.mood) rows.push(["🧭", d.mood]);
  if (d.last) {
    const l = d.last;
    rows.push([l.side === "BUY" ? "🟢" : "🔴", l.symbol + " " + money(l.amount) + (l.pnl !== null && l.pnl !== undefined ? " (" + signed(l.pnl) + ")" : "") + " · " + hm(l.ts)]);
  }
  rows.slice(0, lines).forEach(function (r) {
    const s = stack.addStack();
    s.centerAlignContent();
    text(s, r[0] + " ", 11, C.text, false);
    text(s, r[1], 11, C.text, false);
    stack.addSpacer(2);
  });
}

async function build() {
  const fam = config.widgetFamily || "medium";
  const w = new ListWidget();
  const g = new LinearGradient();
  g.colors = [C.bg1, C.bg2];
  g.locations = [0, 1];
  g.startPoint = new Point(0, 0);
  g.endPoint = new Point(1, 1);
  w.backgroundGradient = g;
  w.url = DASH;
  w.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
  w.setPadding(12, 14, 12, 14);

  const res = await load();
  if (!res.d) {
    text(w, "TRADINGBOTTY", 9, C.cyan, true);
    w.addSpacer(6);
    text(w, "Keine Verbindung", 14, C.amber, true);
    const t = w.addText(res.why || "");
    t.font = Font.systemFont(10);
    t.textColor = C.dim;
    return w;
  }
  const d = res.d;
  header(w, d, res.stale);
  w.addSpacer(4);
  const lineColor = (d.bot_edge || 0) >= 0 ? C.green : C.red;
  if (fam === "small") {
    money_block(w, d, 22);
    w.addSpacer();
    w.addImage(spark(d.spark, 130, 22, lineColor)).imageSize = new Size(130, 22);
    return w;
  }
  const body = w.addStack();
  const left = body.addStack();
  left.layoutVertically();
  money_block(left, d, fam === "large" ? 30 : 24);
  body.addSpacer(12);
  const right = body.addStack();
  right.layoutVertically();
  if (fam === "medium") {
    right.addImage(spark(d.spark, 150, 34, lineColor)).imageSize = new Size(150, 34);
    right.addSpacer(4);
    details(right, d, 3);
  } else {
    details(right, d, 4);
    w.addSpacer(10);
    text(w, "LETZTE 7 TAGE", 9, C.dim, true);
    w.addSpacer(4);
    w.addImage(spark(d.spark, 300, 90, lineColor)).imageSize = new Size(300, 90);
  }
  w.addSpacer();
  return w;
}

const widget = await build();
if (config.runsInWidget) Script.setWidget(widget);
else await widget.presentMedium();
Script.complete();
