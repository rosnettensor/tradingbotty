import { useEffect, useMemo, useState } from "react";
import { BacktestResult } from "./Lab.jsx";
import { ChipList, Slider, Toggle } from "./components.jsx";
import { api, usePoll } from "./useBot.js";

export default function Controls({ state }) {
  return (
    <div className="controls">
      <BotSettings />
      <StrategyEditor state={state} />
      <div className="col">
        <Explainer state={state} />
        <Sources />
      </div>
    </div>
  );
}

const FEE_PRESETS = [["Bitpanda Fusion", 0.25], ["Kraken Pro", 0.8], ["Bitpanda app", 1.5]];

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
      <h3>BOT SETTINGS <span className="dim">· apply instantly, to every strategy · ↺ = back to config.toml</span></h3>
      {msg && <div className="err">{msg}</div>}
      {Object.entries(groups).map(([g, items]) => (
        <div key={g} className="group">
          <h4>{g.toUpperCase()}</h4>
          {g === "Costs" && (
            <div className="presets">
              <span className="dim small">crypto fee preset:</span>
              {FEE_PRESETS.map(([label, v]) => (
                <button key={label} className={`mini ${items.find((c) => c.key === "paper.fee_pct")?.value === v ? "on" : ""}`}
                  onClick={() => set("paper.fee_pct", v)}>{label} {v}%</button>
              ))}
            </div>
          )}
          {items.map((c) => c.bool
            ? <Toggle key={c.key} label={c.label} help={c.help} checked={!!c.value} onChange={(v) => set(c.key, v)} />
            : <Slider key={c.key} {...c} value={c.value} onCommit={(v) => set(c.key, v)} onReset={() => set(c.key, null)} />)}
        </div>
      ))}
      <p className="dim small">Not here on purpose: margin, leverage and short selling. The bot has no code for them, so it can never owe money.</p>
    </section>
  );
}

function StrategyEditor({ state }) {
  const [exp, reloadExp] = usePoll("experiments", 30000);
  const [meta] = usePoll("controls", 0);
  const board = exp?.leaderboard || [];
  const [vid, setVid] = useState(null);
  const v = board.find((b) => b.id === vid) || board.find((b) => b.champion);
  const [draft, setDraft] = useState({});
  const [name, setName] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => { setDraft({}); setResult(null); setMsg(""); }, [v?.id]);
  const fields = meta?.strategy_fields || {};
  const groups = useMemo(() => groupBy(Object.entries(fields).map(([k, f]) => ({ k, ...f })), "group"), [fields]);
  if (!v) return <section className="panel"><div className="empty">loading strategies…</div></section>;
  const cfg = { ...v.config, ...draft };
  const dirty = Object.keys(draft).length > 0;

  const run = async (label, fn) => {
    setBusy(label); setMsg("");
    try { await fn(); } catch (e) { setMsg(e.message); } finally { setBusy(""); }
  };
  const backtest = () => run("backtest", async () => setResult(await api("backtest", { variant_id: v.id, config: draft, hours: 24 })));
  const apply = () => run("apply", async () => {
    await api(`variants/${v.id}/config`, { changes: draft });
    setDraft({}); setMsg(`saved to ${v.name}`); reloadExp();
  });
  const saveNew = () => run("new", async () => {
    const r = await api(`variants/${v.id}/config`, { changes: draft, as_new: true, name });
    setDraft({}); setName(""); setMsg(`created ${r.name}: it starts with a fresh 100 USD paper account`); reloadExp(); setVid(r.id);
  });

  return (
    <section className="panel strategy">
      <div className="row-head">
        <h3>STRATEGY SETTINGS</h3>
        <select value={v.id} onChange={(e) => setVid(e.target.value)}>
          {board.map((b) => <option key={b.id} value={b.id}>{b.champion ? "★ " : ""}{b.name} ({b.return_pct >= 0 ? "+" : ""}{b.return_pct}%)</option>)}
        </select>
      </div>
      <p className="dim small">
        {v.champion ? "This is the champion: its trades are the ones copied to live money. " : ""}
        Editing a running strategy mixes old and new results; saving as a new strategy keeps the experiment clean.
      </p>
      {Object.entries(groups).map(([g, items]) => (
        <div key={g} className="group">
          <h4>{g.toUpperCase()}</h4>
          <div className={g === "Signal weights" ? "weights" : ""}>
            {items.map((f) => f.min === undefined
              ? <Toggle key={f.k} label={f.label} help={f.help} checked={!!cfg[f.k]} onChange={(x) => setDraft({ ...draft, [f.k]: x })} />
              : <Slider key={f.k} {...f} value={cfg[f.k]} changed={f.k in draft}
                  onCommit={(x) => setDraft({ ...draft, [f.k]: x })}
                  onReset={() => { const d = { ...draft }; delete d[f.k]; setDraft(d); }} />)}
          </div>
        </div>
      ))}
      <div className="sticky-actions">
        <button onClick={backtest} disabled={!!busy}>{busy === "backtest" ? "replaying 24h…" : "backtest 24h"}</button>
        <button onClick={apply} disabled={!dirty || !!busy}>apply to {v.name}</button>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="new strategy name" />
        <button className="primary" onClick={saveNew} disabled={!!busy}>save as new strategy</button>
        {dirty && <button onClick={() => setDraft({})}>discard</button>}
      </div>
      {msg && <p className="ok-msg">{msg}</p>}
      {result && <BacktestResult r={result} compact />}
    </section>
  );
}

function Explainer({ state }) {
  const c = state.champion_config || {};
  return (
    <section className="panel explainer">
      <h3>WHAT DRIVES TRADING</h3>
      <dl>
        <dt>How often it buys</dt>
        <dd>Every <b>tick</b> the Predictor scores each symbol. A buy happens when the score is above the <b>buy threshold</b> ({c.entry_score}), a slot is free (<b>max positions</b> {c.max_positions}), the coin isn't <b>cooling down</b> ({c.cooldown_minutes} min), the <b>hourly buy limit</b> ({c.max_buys_per_hour}) isn't used up and the <b>fee guard</b> ({c.min_edge_pct}%) passes. Lower threshold and shorter cooldown = more trades and more fees.</dd>
        <dt>How much it bets</dt>
        <dd><b>Position size</b> ({c.position_pct}% of equity) × the Professor's <b>risk appetite</b> ({state.risk_appetite?.toFixed(2)}), then cut by the Risk Officer's hard caps.</dd>
        <dt>When it sells</dt>
        <dd><b>Stop loss</b> {c.stop_loss_pct}%, <b>take profit</b> {c.take_profit_pct}%, <b>trailing stop</b> {c.trailing_stop_pct}% from the peak, or the score falls under the <b>sell threshold</b> ({c.exit_score}) after the <b>min hold</b> ({c.min_hold_minutes} min).</dd>
        <dt>What it believes</dt>
        <dd>The <b>signal weights</b>. A weight of 0 ignores a signal; a negative weight bets against it.</dd>
        <dt>The hard truth</dt>
        <dd>Every trade pays the fee twice (buy and sell). At the Bitpanda app's 1.5% a trade must gain about 3% just to break even; on Bitpanda Fusion (0.25%) about 0.5%. A strategy only earns its keep if it beats the <b>Buy & Hold</b> yardstick in the Lab after fees.</dd>
      </dl>
    </section>
  );
}

function Sources() {
  const [s, reload] = usePoll("sources", 60000);
  if (!s) return <section className="panel"><div className="empty">loading…</div></section>;
  const add = (kind) => async (value, name) => { const r = await api("sources/add", { kind, value, name }); if (r.ok) reload(); return r; };
  const remove = (kind) => async (value) => { const r = await api("sources/remove", { kind, value }); if (r.ok) reload(); return r; };
  return (
    <section className="panel sources">
      <h3>SOURCES <span className="dim">· every addition is tested before it's used</span></h3>
      <h4>COINS (KRAKEN)</h4>
      <ChipList items={s.crypto} locked={s.held} onAdd={add("crypto")} onRemove={remove("crypto")} placeholder="ticker, e.g. ARB" />
      <h4>STOCKS & ETFS (YAHOO · SWISS .SW · GERMAN .DE)</h4>
      <ChipList items={s.stocks} locked={s.held} onAdd={add("stocks")} onRemove={remove("stocks")} placeholder="e.g. PLTR, ROG.SW, SIE.DE" />
      <h4>SUBREDDITS</h4>
      <ChipList items={s.subreddits} status={s.status} statusKey={(x) => `r/${x}`} onAdd={add("subreddits")} onRemove={remove("subreddits")} placeholder="e.g. Bitcoin" />
      <h4>NEWS FEEDS (RSS)</h4>
      <ChipList items={Object.keys(s.feeds)} status={s.status} onAdd={add("feeds")} onRemove={remove("feeds")} placeholder="https://…/rss" nameField />
      <p className="dim small">A dot shows each source's last read: green worked, red failed (hover for the reason). Coins held by a strategy can't be removed until sold.</p>
    </section>
  );
}
