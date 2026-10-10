import{H as e,W as t,Y as n,Z as r,i,n as a,o,q as s,r as c,t as l}from"./index-B0tuSCOT.js";var u=r(n(),1),d=`// TradingBotty home-screen widget for the free iPhone app "Scriptable".
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
`,f=e();function p({state:e}){return(0,f.jsxs)(`div`,{className:`controls`,children:[(0,f.jsx)(g,{state:e}),(0,f.jsxs)(`div`,{className:`col`,children:[(0,f.jsx)(m,{state:e}),(0,f.jsx)(b,{state:e}),(0,f.jsx)(_,{state:e}),(0,f.jsx)(v,{state:e}),(0,f.jsx)(S,{}),(0,f.jsx)(y,{state:e})]}),(0,f.jsx)(x,{})]})}function m({state:e}){let t=e.risk_status||{},n=e.wallet?.currency||t.currency||`CHF`;return(0,f.jsxs)(`section`,{className:`panel`,children:[(0,f.jsx)(`h3`,{children:`CASH-ONLY RISK GATE`}),(0,f.jsxs)(`p`,{className:`small`,children:[`All purchase paths: ≤100 `,n,` per order, gross daily loss budget 200 `,n,`, shared position count, no margin or borrowing. Your smaller order setting wins. Missing prices or spreads block buys; exits remain possible.`]}),(0,f.jsxs)(`p`,{className:`dim small`,children:[`Last pre-order check: `,t.daily_loss==null?`waiting`:`${t.daily_loss.toFixed(2)} / 200 ${n} losses · ${t.positions} positions`,`. Loss accounting resets at 00:00 UTC; current unrealized downside is included. The budget stops new buys and cannot guarantee a maximum loss.`]})]})}function h(e,t){let n={};for(let r of e)(n[r[t]]||=[]).push(r);return n}function g({state:e}){let[n,r]=s(`controls`,0),[a,c]=(0,u.useState)(``);if(!n)return(0,f.jsx)(`section`,{className:`panel`,children:(0,f.jsx)(`div`,{className:`empty`,children:`loading…`})});let l=async(e,n)=>{try{await t(`controls`,{changes:{[e]:n}}),c(``),r()}catch(e){c(e.message)}},d=h(n.controls,`group`),p=e.wallet||{},m=p.currency||`CHF`,g=p.ts&&Date.now()/1e3-p.ts<120&&!p.stale&&!p.error,_=Math.min(2e3,Math.floor((p.total||0)*.98));return(0,f.jsxs)(`section`,{className:`panel settings`,children:[(0,f.jsxs)(`h3`,{children:[`SETTINGS `,(0,f.jsx)(`span`,{className:`dim`,children:`· apply instantly · ↺ = back to config.toml`})]}),(0,f.jsxs)(`div`,{className:`go-settings`,children:[(0,f.jsx)(`span`,{className:`vol-eyebrow`,children:`STARTPROFIL · CASH ONLY`}),(0,f.jsxs)(`p`,{children:[`Verfügbaren Kontorahmen nutzen: `,(0,f.jsxs)(`b`,{children:[_,` `,m]}),` Daily-Budget (98% des letzten Kontowerts, höchstens 2.000), maximal `,(0,f.jsxs)(`b`,{children:[`100 `,m]}),` je Order, Spread höchstens `,(0,f.jsx)(`b`,{children:`1%`}),`. Der Fast-Topf wird separat reserviert.`]}),(0,f.jsx)(`button`,{disabled:!g||_<30||e.simulate,onClick:async()=>{if(!(!g||_<30)&&window.confirm(`Kontorahmen übernehmen?\n\nDaily-Budget bis ${_} ${m}, maximal 100 je Order, Spread maximal 1%. Bestehende Coins dürfen zur Finanzierung verkauft werden. Der Fast-Topf behält seine Reservierung.\n\nBei bereits aktivem LIVE-Handel können die neuen Limits ab der nächsten Prüfung Orders auslösen. Der gesamte eingesetzte Betrag kann verloren gehen. Kredit, Margin und Hebel bleiben ausgeschlossen.`))try{await t(`controls`,{changes:{"live.max_invest":_,"live.max_order":100,"live.max_spread_pct":1,"live.use_my_coins":!0}}),c(`Kontorahmen gespeichert. Strategie, Fast-Topf und LIVE-Schalter im Systemcheck prüfen.`),r()}catch(e){c(e.message)}},children:`Kontorahmen übernehmen`}),(0,f.jsx)(`p`,{className:`dim small`,children:`Benötigt einen aktuellen Kontostand. Aktiviert weder LIVE noch eine Strategie. Vorhandene Coins werden als Finanzierung freigegeben; bei bereits laufendem Handel wirken die Limits ab der nächsten Prüfung.`})]}),a&&(0,f.jsx)(`div`,{role:`status`,className:`small`,children:a}),Object.entries(d).map(([e,t])=>(0,f.jsxs)(`div`,{className:`group`,children:[(0,f.jsx)(`h4`,{children:e.toUpperCase()}),t.map(e=>e.bool?(0,f.jsx)(o,{label:e.label,help:e.help,checked:!!e.value,onChange:t=>l(e.key,t)},e.key):(0,f.jsx)(i,{...e,value:e.value,onCommit:t=>l(e.key,t),onReset:()=>l(e.key,null)},e.key)),e===`Live money`&&(0,f.jsx)(`p`,{className:`dim small`,children:`Changing a money limit makes the Daily Brain decide again right away.`})]},e)),(0,f.jsx)(`p`,{className:`dim small`,children:`Not here on purpose: margin, leverage and short selling. The bot has no code for them, so it can never owe money. The strategy itself is picked in Research, from the ones that pass the history test.`})]})}function _({state:e}){let t=e.brain||{},n=e.trend||{};return(0,f.jsxs)(`section`,{className:`panel explainer`,children:[(0,f.jsx)(`h3`,{children:`HOW THE REAL MONEY IS TRADED`}),(0,f.jsxs)(`dl`,{children:[(0,f.jsx)(`dt`,{children:`Who decides`}),(0,f.jsxs)(`dd`,{children:[`The `,(0,f.jsx)(`b`,{children:`Daily Brain`}),`, with one strategy that passed the history test: `,(0,f.jsx)(`b`,{children:t.strategy||`none picked yet`}),`. It decides once a day right after the daily candle closes (00:00 UTC), on the same daily data it was tested on. No paper money, no guessing every minute.`]}),(0,f.jsx)(`dt`,{children:`When it buys`}),(0,f.jsxs)(`dd`,{children:[`A coin closes above its `,(0,f.jsxs)(`b`,{children:[n.entry_days??20,`-day high`]}),` while Bitcoin is above its `,(0,f.jsxs)(`b`,{children:[n.btc?.days??50,`-day average`]}),`. Up to 3 coins, a third of your limit each. The Guardian can block a coin (hack, delisting, crash, Professor veto).`]}),(0,f.jsx)(`dt`,{children:`When it sells`}),(0,f.jsxs)(`dd`,{children:[`A coin closes under its `,(0,f.jsxs)(`b`,{children:[n.exit_days??10,`-day low`]}),` or falls clearly from its peak (trailing stop). The Guardian can sell early if two witnesses confirm a hack or delisting.`]}),(0,f.jsx)(`dt`,{children:`Every order`}),(0,f.jsxs)(`dd`,{children:[`The `,(0,f.jsx)(`b`,{children:`Risk Officer`}),` checks the kill switch, your caps, Fusion's minimum, the spread and the cash (with fee room) before the `,(0,f.jsx)(`b`,{children:`Live Desk`}),` sends it.`]}),(0,f.jsx)(`dt`,{children:`What AI does`}),(0,f.jsxs)(`dd`,{children:[`The `,(0,f.jsx)(`b`,{children:`News Hunter`}),` (Haiku) reads the news and spots hacks and delistings. `,(0,f.jsx)(`b`,{children:`The Professor`}),` (Opus, once a day) reviews the decision and may veto a buy for 24h. Neither can force a trade: the tested math stays in charge.`]}),(0,f.jsx)(`dt`,{children:`Expect`}),(0,f.jsx)(`dd`,{children:`Few trades, often none for days; cash about half the time; drops of up to about 50% in bad phases, big gains in trends. Every trade pays about 0.25% fee on Fusion.`})]})]})}function v({state:e}){let[n,r]=(0,u.useState)(``),[i,a]=(0,u.useState)(!1),[o]=s(`report`,3e5),[c]=s(`report/weekly?send=0`,3e5),l=e.phone||{channels:[]},d=l.channels.length>0;return(0,f.jsxs)(`section`,{className:`panel`,children:[(0,f.jsxs)(`h3`,{children:[`PHONE BRIEFING `,(0,f.jsxs)(`span`,{className:d?`up`:`dim`,children:[`· `,d?l.channels.join(` + `):`not set up`]})]}),(0,f.jsxs)(`p`,{className:`dim small`,children:[`Every morning at `,l.hour??7,`:00 Swiss time: account, the bot's own gain or loss, the night's decision, the fast pot, real trades of the last 24 hours, Guardian blocks and the Professor's review.`,l.trades?` Plus a short message on every real trade.`:``,l.weekly?` Every Sunday at 19:00 the weekly report card: the bot's own result with a grade A to F, both traders' trades, the best and worst trade, Bitcoin's week.`:``,` Right away when the Guardian sells in an emergency or the fast pot hits its floor. Time, trade and weekly messages: Settings, Phone.`]}),d?(0,f.jsxs)(`div`,{className:`row-tools`,children:[(0,f.jsx)(`button`,{onClick:async()=>{a(!0),r(``);try{await t(`phone/test`,{}),r(`Sent: check your phone.`)}catch(e){r(e.message)}finally{a(!1)}},disabled:i,children:i?`sending…`:`send a test message`}),(0,f.jsx)(`button`,{onClick:async()=>{a(!0),r(``);try{await t(`report/weekly?send=1`,{}),r(`Sent the weekly report card: check your phone.`)}catch(e){r(e.message)}finally{a(!1)}},disabled:i,children:`send weekly report now`})]}):(0,f.jsx)(`p`,{className:`small`,children:`Easiest: install the free ntfy app, subscribe to a long secret topic name and put it into .env as NTFY_TOPIC, restart. WhatsApp: send the activation message from callmebot.com to their WhatsApp number, put your number and the apikey you get into .env (WHATSAPP_PHONE, WHATSAPP_APIKEY), restart. Telegram works too (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID).`}),n&&(0,f.jsx)(`p`,{className:/Sent/.test(n)?`ok-msg`:`err-msg`,children:n}),o?.text&&(0,f.jsxs)(f.Fragment,{children:[(0,f.jsx)(`h4`,{children:`TODAY'S BRIEFING (PREVIEW)`}),(0,f.jsx)(`pre`,{className:`report`,children:o.text})]}),c?.text&&(0,f.jsxs)(f.Fragment,{children:[(0,f.jsx)(`h4`,{children:`WEEKLY REPORT CARD (PREVIEW)`}),(0,f.jsxs)(`pre`,{className:`report`,children:[c.title,`

`,c.text]})]})]})}function y({state:e}){let t=(e.trades||[])[0]?.symbol||e.trend?.rows?.[0]?.symbol||`BTC`;return(0,f.jsxs)(`section`,{className:`panel trade-show`,children:[(0,f.jsxs)(`h3`,{children:[`TRADE ANIMATION `,(0,f.jsx)(`span`,{className:`dim`,children:`· this screen only`})]}),(0,f.jsx)(`p`,{className:`dim small`,children:`A real trade plays a short full-screen moment here (tap or Esc closes it). Sound is off until you switch it on.`}),(0,f.jsxs)(`div`,{className:`btn-row`,children:[(0,f.jsx)(`button`,{onClick:()=>a(`BUY`,t),children:`▶ demo buy`}),(0,f.jsx)(`button`,{onClick:()=>a(`SELL`,t),children:`▶ demo sell`}),(0,f.jsx)(l,{})]})]})}function b({state:e}){let[n,r]=(0,u.useState)(``);return e.moved?(0,f.jsxs)(`section`,{className:`panel guard-item`,children:[(0,f.jsxs)(`h3`,{children:[`THIS BOT MOVED TO `,String(e.moved.to||`the server`).toUpperCase()]}),(0,f.jsx)(`p`,{className:`small`,children:`This copy stays on STANDBY so your money is never traded twice. The server trades now.`}),(0,f.jsx)(`button`,{onClick:async()=>{if(window.confirm(`Trade on this computer again?

Only if the server copy is on STANDBY or switched off: two copies would trade the same money twice.`))try{await t(`move/back`,{}),r(`Done. Switch LIVE at the top when you're ready.`)}catch(e){r(e.message)}},children:`trade on this computer again`}),n&&(0,f.jsx)(`p`,{className:`ok-msg`,children:n})]}):null}function x(){let[e,n]=s(`sources`,6e4);return e?(0,f.jsxs)(`section`,{className:`panel sources`,children:[(0,f.jsxs)(`h3`,{children:[`NEWS FEEDS `,(0,f.jsx)(`span`,{className:`dim`,children:`· the News Hunter reads them · every new feed is tested first`})]}),(0,f.jsx)(c,{items:Object.keys(e.feeds),status:e.status,onAdd:async(e,r)=>{let i=await t(`sources/add`,{kind:`feeds`,value:e,name:r});return i.ok&&n(),i},onRemove:async e=>{let r=await t(`sources/remove`,{kind:`feeds`,value:e});return r.ok&&n(),r},placeholder:`https://…/rss`,nameField:!0}),(0,f.jsx)(`p`,{className:`dim small`,children:`A dot shows each feed's last read: green worked, red failed (the reason is listed below the feeds). A feed that fails three times rests for 6 hours.`}),(0,f.jsx)(`h4`,{children:`COINS THE BRAIN MAY TRADE`}),(0,f.jsx)(`div`,{className:`chip-row`,children:(e.coins||[]).map(e=>(0,f.jsx)(`span`,{className:`chip`,children:e},e))}),(0,f.jsxs)(`p`,{className:`dim small`,children:[`The `,e.coins?.length,` big coins with daily history since 2017 that the history test covers. A strategy only trades what it was tested on; the Fusion Scout checks each is really tradable on Fusion and what its minimum order is.`]}),(0,f.jsx)(`h4`,{children:`FREE DATA (NO KEYS)`}),(0,f.jsx)(`p`,{className:`small`,children:`Kraken live prices · Binance, Coinbase and Kraken daily history · alternative.me Fear & Greed · Binance futures funding · Wikipedia page views · DefiLlama stablecoins · blockchain.com hash rate. See Research for what they're worth.`})]}):(0,f.jsx)(`section`,{className:`panel`,children:(0,f.jsx)(`div`,{className:`empty`,children:`loading…`})})}function S(){let[e,n]=(0,u.useState)(null),[r,i]=(0,u.useState)(``),a=async()=>{let{key:e}=await t(`widget/key`);return d.replaceAll(`__URL__`,window.location.origin).replaceAll(`__KEY__`,e||``)};return(0,f.jsxs)(`section`,{className:`panel widget-setup`,children:[(0,f.jsxs)(`h3`,{children:[`IPHONE WIDGET `,(0,f.jsx)(`span`,{className:`dim`,children:`· your numbers on the home screen, read-only`})]}),(0,f.jsxs)(`ol`,{className:`steps`,children:[(0,f.jsxs)(`li`,{children:[`Lade die Gratis-App `,(0,f.jsx)(`b`,{children:`Scriptable`}),` aus dem App Store.`]}),(0,f.jsxs)(`li`,{children:[`Tippe hier auf `,(0,f.jsx)(`b`,{children:`Script kopieren`}),`. In Scriptable: `,(0,f.jsx)(`b`,{children:`+`}),` oben rechts, einfügen, oben den Namen auf `,(0,f.jsx)(`b`,{children:`TradingBotty`}),` setzen, `,(0,f.jsx)(`b`,{children:`Fertig`}),`.`]}),(0,f.jsxs)(`li`,{children:[`Homescreen lange drücken, `,(0,f.jsx)(`b`,{children:`+`}),` oben links, `,(0,f.jsx)(`b`,{children:`Scriptable`}),` wählen, Grösse wählen (klein, mittel oder gross), `,(0,f.jsx)(`b`,{children:`Widget hinzufügen`}),`.`]}),(0,f.jsxs)(`li`,{children:[`Das neue Widget lange drücken, `,(0,f.jsx)(`b`,{children:`Widget bearbeiten`}),`, bei Script `,(0,f.jsx)(`b`,{children:`TradingBotty`}),` wählen.`]})]}),(0,f.jsx)(`p`,{className:`dim small`,children:`Das Script enthält einen eigenen Schlüssel, der nur diese Zahlen öffnet, nicht dein Passwort und nie Orders. iOS aktualisiert Widgets etwa alle 15 Minuten; ein Tipp aufs Widget öffnet das Dashboard. Ein neues Passwort macht den alten Schlüssel ungültig: dann das Script neu kopieren.`}),(0,f.jsxs)(`div`,{className:`row-tools`,children:[(0,f.jsx)(`button`,{className:`primary`,onClick:async()=>{try{let t=e||await a();n(t),await navigator.clipboard.writeText(t),i(`Kopiert. Jetzt in Scriptable einfügen (Schritt 2).`)}catch(t){if(i(t?.message?.includes(`widget`)?t.message:`Kopieren ging nicht: halte den Text unten gedrückt und kopiere ihn von Hand.`),!e)try{n(await a())}catch{}}},children:`Script kopieren`}),r&&(0,f.jsx)(`span`,{className:`dim small`,children:r})]}),e&&(0,f.jsx)(`textarea`,{className:`widget-script`,readOnly:!0,value:e,rows:6,onFocus:e=>e.target.select()})]})}export{p as default};