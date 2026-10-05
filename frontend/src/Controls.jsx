import { useState } from "react";
import { ChipList, Slider, Toggle } from "./components.jsx";
import { api, usePoll } from "./useBot.js";

export default function Controls({ state }) {
  return (
    <div className="controls">
      <BotSettings />
      <div className="col">
        <HowItTrades state={state} />
        <Phone state={state} />
      </div>
      <Sources />
    </div>
  );
}

function groupBy(list, key) {
  const out = {};
  for (const x of list) (out[x[key]] ||= []).push(x);
  return out;
}

function BotSettings() {
  const [data, reload] = usePoll("controls", 0);
  const [msg, setMsg] = useState("");
  if (!data) return <section className="panel"><div className="empty">loading…</div></section>;
  const set = async (key, value) => {
    try { await api("controls", { changes: { [key]: value } }); setMsg(""); reload(); } catch (e) { setMsg(e.message); }
  };
  const groups = groupBy(data.controls, "group");
  return (
    <section className="panel settings">
      <h3>SETTINGS <span className="dim">· apply instantly · ↺ = back to config.toml</span></h3>
      {msg && <div className="err">{msg}</div>}
      {Object.entries(groups).map(([g, items]) => (
        <div key={g} className="group">
          <h4>{g.toUpperCase()}</h4>
          {items.map((c) => c.bool
            ? <Toggle key={c.key} label={c.label} help={c.help} checked={!!c.value} onChange={(v) => set(c.key, v)} />
            : <Slider key={c.key} {...c} value={c.value} onCommit={(v) => set(c.key, v)} onReset={() => set(c.key, null)} />)}
          {g === "Live money" && <p className="dim small">Changing a money limit makes the Daily Brain decide again right away.</p>}
        </div>
      ))}
      <p className="dim small">Not here on purpose: margin, leverage and short selling. The bot has no code for them, so it can never owe money. The strategy itself is picked in Research, from the ones that pass the history test.</p>
    </section>
  );
}

function HowItTrades({ state }) {
  const b = state.brain || {};
  const t = state.trend || {};
  return (
    <section className="panel explainer">
      <h3>HOW THE REAL MONEY IS TRADED</h3>
      <dl>
        <dt>Who decides</dt>
        <dd>The <b>Daily Brain</b>, with one strategy that passed the history test: <b>{b.strategy || "none picked yet"}</b>. It decides once a day right after the daily candle closes (00:00 UTC), on the same daily data it was tested on. No paper money, no guessing every minute.</dd>
        <dt>When it buys</dt>
        <dd>A coin closes above its <b>{t.entry_days ?? 20}-day high</b> while Bitcoin is above its <b>{t.btc?.days ?? 50}-day average</b>. Up to 3 coins, a third of your limit each. The Guardian can block a coin (hack, delisting, crash, Professor veto).</dd>
        <dt>When it sells</dt>
        <dd>A coin closes under its <b>{t.exit_days ?? 10}-day low</b> or falls clearly from its peak (trailing stop). The Guardian can sell early if two witnesses confirm a hack or delisting.</dd>
        <dt>Every order</dt>
        <dd>The <b>Risk Officer</b> checks the kill switch, your caps, Fusion's minimum, the spread and the cash (with fee room) before the <b>Live Desk</b> sends it.</dd>
        <dt>What AI does</dt>
        <dd>The <b>News Hunter</b> (Haiku) reads the news and spots hacks and delistings. <b>The Professor</b> (Opus, once a day) reviews the decision and may veto a buy for 24h. Neither can force a trade: the tested math stays in charge.</dd>
        <dt>Expect</dt>
        <dd>Few trades, often none for days; cash about half the time; drops of up to about 50% in bad phases, big gains in trends. Every trade pays about 0.25% fee on Fusion.</dd>
      </dl>
    </section>
  );
}

function Phone({ state }) {
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [report] = usePoll("report", 300000);
  const test = async () => {
    setBusy(true); setMsg("");
    try { await api("telegram/test", {}); setMsg("Sent: check your phone."); } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  };
  return (
    <section className="panel">
      <h3>PHONE BRIEFING (TELEGRAM) <span className={state.telegram ? "up" : "dim"}>· {state.telegram ? "connected" : "not set up"}</span></h3>
      <p className="dim small">Every morning after the daily decision: account, the bot's gain or loss, hours since the last trade, what it holds, coins near a breakout, Guardian blocks and the Professor's review. Also right away when the Guardian sells a coin in an emergency or the brain switches strategy.</p>
      {state.telegram
        ? <button onClick={test} disabled={busy}>{busy ? "sending…" : "send a test message"}</button>
        : <p className="small">To switch it on, follow the steps Claude sent you in the chat (BotFather, then two lines in .env), then restart the bot.</p>}
      {msg && <p className={/Sent/.test(msg) ? "ok-msg" : "err-msg"}>{msg}</p>}
      {report?.text && <><h4>TODAY'S BRIEFING (PREVIEW)</h4><pre className="report">{report.text}</pre></>}
    </section>
  );
}

function Sources() {
  const [s, reload] = usePoll("sources", 60000);
  if (!s) return <section className="panel"><div className="empty">loading…</div></section>;
  const add = async (value, name) => { const r = await api("sources/add", { kind: "feeds", value, name }); if (r.ok) reload(); return r; };
  const remove = async (value) => { const r = await api("sources/remove", { kind: "feeds", value }); if (r.ok) reload(); return r; };
  return (
    <section className="panel sources">
      <h3>NEWS FEEDS <span className="dim">· the News Hunter reads them · every new feed is tested first</span></h3>
      <ChipList items={Object.keys(s.feeds)} status={s.status} onAdd={add} onRemove={remove} placeholder="https://…/rss" nameField />
      <p className="dim small">A dot shows each feed's last read: green worked, red failed (hover for the reason).</p>
      <h4>COINS THE BRAIN MAY TRADE</h4>
      <div className="chip-row">{(s.coins || []).map((c) => <span key={c} className="chip">{c}</span>)}</div>
      <p className="dim small">The {s.coins?.length} big coins with daily history since 2017 that the history test covers. A strategy only trades what it was tested on; the Fusion Scout checks each is really tradable on Fusion and what its minimum order is.</p>
      <h4>FREE DATA (NO KEYS)</h4>
      <p className="small">Kraken live prices · Binance, Coinbase and Kraken daily history · alternative.me Fear & Greed · Binance futures funding · Wikipedia page views · DefiLlama stablecoins · blockchain.com hash rate. See Research for what they're worth.</p>
    </section>
  );
}
