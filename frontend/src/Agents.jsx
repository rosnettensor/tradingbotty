import { useCallback, useEffect, useMemo, useState } from "react";
import { Background, Controls, Handle, MiniMap, Position, ReactFlow, applyNodeChanges } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Toggle } from "./components.jsx";
import { ago, api, fmt, usePoll } from "./useBot.js";

// Data on the left, decisions flowing right to the one place that touches real money (the Live Desk).
const COLUMNS = [
  ["src_fusion", "src_kraken", "src_binance", "src_free", "src_news"],
  ["radar", "trend", "collector", "news"],
  ["patterns", "researcher"],
  ["guardian", "professor"],
  ["brain"],
  ["risk"],
  ["livedesk"],
];
const KIND_COLOR = { source: "var(--violet)", agent: "var(--cyan)", gate: "var(--amber)" };
const POS_KEY = "tb-node-pos-v5";

function defaultPosition(id) {
  for (let c = 0; c < COLUMNS.length; c++) {
    const r = COLUMNS[c].indexOf(id);
    if (r >= 0) return { x: c * 280, y: r * 150 + (5 - COLUMNS[c].length) * 75 };
  }
  return { x: 0, y: 0 };
}

function loadPositions() {
  try { return JSON.parse(localStorage.getItem(POS_KEY) || "{}"); } catch { return {}; }
}

const until = (ts) => {
  const s = ts - Date.now() / 1000;
  if (!ts || s <= 0) return null;
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
};

function AgentNode({ data }) {
  const n = data.node;
  const next = until(n.next_run);
  return (
    <div className={`agent-node ${n.kind} st-${n.status} ${data.selected ? "selected" : ""}`} style={{ "--k": KIND_COLOR[n.kind] }}>
      <Handle type="target" position={Position.Left} />
      <div className="an-head">
        {data.avatar && <Avatar src={data.avatar} size={26} />}
        <span className={`dot st-${n.status}`} />
        <b>{n.name}</b>
        {n.uses_ai && <span className="ai">AI</span>}
      </div>
      <div className="an-summary">{n.summary}</div>
      <div className="an-foot dim">
        {n.kind === "source" ? "source" : n.cadence || n.kind}{n.cost ? ` · ${fmt.usd(n.cost)}` : ""}{next ? ` · next ${next}` : ""}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { agent: AgentNode };

export default function Agents({ state, avatars = {}, want }) {
  const agentNodes = state.nodes || [];
  const [positions, setPositions] = useState(loadPositions);
  const [selected, setSelected] = useState(() => {
    try { return want || localStorage.getItem("tb-agent") || "brain"; } catch { return want || "brain"; }
  });
  const [rfNodes, setRfNodes] = useState([]);
  const [, tick] = useState(0);
  useEffect(() => { if (want) setSelected(want); }, [want]);
  useEffect(() => { try { localStorage.setItem("tb-agent", selected || ""); } catch { /* ignore */ } }, [selected]);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 5000); return () => clearInterval(t); }, []);

  useEffect(() => {
    setRfNodes((prev) => agentNodes.map((n) => {
      const old = prev.find((p) => p.id === n.id);
      return {
        id: n.id, type: "agent", data: { node: n, selected: n.id === selected, avatar: avatars[n.id] },
        position: old?.position || positions[n.id] || defaultPosition(n.id),
      };
    }));
  }, [agentNodes, selected, avatars]);

  const edges = useMemo(() => agentNodes.flatMap((n) => n.inputs.filter((src) => agentNodes.some((x) => x.id === src)).map((src) => {
    const from = agentNodes.find((x) => x.id === src);
    const active = from && from.status !== "off" && from.status !== "idle" && Date.now() / 1000 - from.last_run < 90;
    return {
      id: `${src}-${n.id}`, source: src, target: n.id, animated: active,
      style: { stroke: from?.status === "error" ? "var(--red)" : from?.kind === "source" ? "var(--violet)" : from?.kind === "gate" ? "var(--amber)" : "var(--cyan)", strokeWidth: 2, opacity: active ? 0.9 : 0.25 },
    };
  })), [agentNodes]);

  const onNodesChange = useCallback((changes) => {
    setRfNodes((nds) => {
      const next = applyNodeChanges(changes, nds);
      const pos = Object.fromEntries(next.map((n) => [n.id, n.position]));
      setPositions(pos);
      try { localStorage.setItem(POS_KEY, JSON.stringify(pos)); } catch { /* ignore */ }
      return next;
    });
  }, []);

  const sel = agentNodes.find((n) => n.id === selected);
  const byId = Object.fromEntries(agentNodes.map((x) => [x.id, x]));
  const counts = agentNodes.filter((n) => n.kind !== "source").reduce((m, n) => ({ ...m, [n.status]: (m[n.status] || 0) + 1 }), {});
  return (
    <div className="nodes-view">
      <div className="flow">
        <ReactFlow nodes={rfNodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange}
          onNodeClick={(_, n) => setSelected(n.id)} fitView colorMode="dark" proOptions={{ hideAttribution: true }}>
          <Background color="#1b2440" gap={24} />
          <MiniMap pannable zoomable nodeColor={(n) => (n.data.node.kind === "source" ? "#8a5cff" : n.data.node.kind === "gate" ? "#ffb020" : "#00f0ff")} maskColor="rgba(5,6,10,0.7)" />
          <Controls />
        </ReactFlow>
        <button className="reset-layout" onClick={() => { try { localStorage.removeItem(POS_KEY); } catch { /* ignore */ } setPositions({}); setRfNodes((r) => r.map((n) => ({ ...n, position: defaultPosition(n.id) }))); }}>reset layout</button>
        <div className="roster">
          <span className="roster-sum">{agentNodes.filter((n) => n.kind !== "source").length} agents · {counts.ok || 0} ok{counts.warn ? ` · ${counts.warn} warn` : ""}{counts.error ? ` · ${counts.error} error` : ""}</span>
          {agentNodes.map((n) => (
            <button key={n.id} className={`${n.id === selected ? "active" : ""} k-${n.kind}`} onClick={() => setSelected(n.id)}>
              <span className={`dot st-${n.status}`} />{n.name}
            </button>
          ))}
        </div>
        <div className="flow-legend">
          <span><i style={{ background: "var(--violet)" }} />data source</span>
          <span><i style={{ background: "var(--cyan)" }} />agent</span>
          <span><i style={{ background: "var(--amber)" }} />gate on real money</span>
          <span className="dim">moving lines = data flowed in the last 90s</span>
        </div>
      </div>
      <aside className="inspector">
        {sel ? <Inspector key={sel.id} n={sel} avatar={avatars[sel.id]} byId={byId} log={state.log || []} pick={setSelected} />
          : <p className="dim">Click an agent to see exactly what it does and what it just did.</p>}
      </aside>
    </div>
  );
}

function Avatar({ src, size }) {
  return /\.(mp4|webm)$/i.test(src)
    ? <video className="avatar" src={src} autoPlay loop muted playsInline style={{ width: size, height: size }} />
    : <img className="avatar" src={src} alt="" style={{ width: size, height: size }} />;
}

function Inspector({ n, byId, avatar, log, pick }) {
  const [models] = usePoll(n.uses_ai ? "models" : null, 0);
  const [prompt, setPrompt] = useState(n.prompt || "");
  const [msg, setMsg] = useState("");
  const [showPrompt, setShowPrompt] = useState(false);
  useEffect(() => { setPrompt(n.prompt || ""); }, [n.prompt]);
  const save = async (body, ok) => {
    try { await api(`agents/${n.id}`, body); setMsg(ok); } catch (e) { setMsg(e.message); }
  };
  const d = n.detail || {};
  const mine = log.filter((l) => l.agent === n.name).slice(-15).reverse();
  const feeds = Object.values(byId).filter((x) => x.inputs.includes(n.id));
  const next = until(n.next_run);
  return (
    <>
      <div className="insp-title">
        {avatar && <Avatar src={avatar} size={72} />}
        <div>
          <h3>{n.name} <span className={`kind-badge k-${n.kind}`}>{n.kind === "gate" ? "gate on real money" : n.kind}</span></h3>
          <p className="role">{n.role}</p>
        </div>
      </div>
      <div className={`insp-status st-${n.status}`}>
        <span className={`dot st-${n.status}`} /> <b>{n.status}</b> <span>· {n.summary}</span>
      </div>
      <div className="insp-clock">
        {n.cadence && <span>⟳ {n.cadence}</span>}
        <span>last run {n.last_run ? ago(n.last_run) : "–"}</span>
        {next && <span>next in {next}</span>}
        <span>{n.runs} runs</span>
        {n.uses_ai && <span>AI cost {fmt.usd(n.cost)}</span>}
      </div>

      {d.did?.length > 0 && (
        <>
          <h4>WHAT IT JUST DID</h4>
          <ol className="did">{d.did.map((s, i) => <li key={i}>{s}</li>)}</ol>
        </>
      )}

      {d.assessment && (
        <div className="prof-box">
          <p>{d.assessment}</p>
          {d.watch && <p><b>Watch:</b> {d.watch}</p>}
          {d.idea && <p className="idea"><b>Idea:</b> {d.idea}</p>}
        </div>
      )}

      {d.trend && <TrendDetail t={d.trend} />}
      {d.target && Object.keys(d.target).length > 0 && (
        <>
          <h4>TARGET (SHARE OF THE BOT'S MONEY)</h4>
          <div className="target-bars">
            {Object.entries(d.target).map(([s, w]) => (
              <div key={s}><b>{s}</b><span className="bar"><i style={{ width: `${w * 100}%` }} /></span><span>{Math.round(w * 100)}%</span></div>
            ))}
          </div>
        </>
      )}

      {d.facts?.length > 0 && (
        <>
          <h4>KEY FACTS</h4>
          <dl className="facts">{d.facts.map(([k, v], i) => <FactRow key={i} k={k} v={v} />)}</dl>
        </>
      )}

      {d.checks?.length > 0 && <Checks checks={d.checks} />}
      {d.table?.rows?.length > 0 && <DetailTable t={d.table} />}

      <h4>WHAT IT DOES</h4>
      <p className="explain">{n.explain}</p>
      <dl className="facts">
        <dt>reads from</dt>
        <dd>{n.inputs.length ? n.inputs.map((i) => <button key={i} className="mini linkish" onClick={() => pick(i)}>{byId[i]?.name || i}</button>) : "the internet"}</dd>
        {(n.outputs || feeds.length > 0) && <><dt>hands on</dt><dd>{n.outputs}{feeds.length > 0 && <div>{feeds.map((x) => <button key={x.id} className="mini linkish" onClick={() => pick(x.id)}>→ {x.name}</button>)}</div>}</dd></>}
      </dl>

      {n.can_disable && (
        <Toggle label={n.enabled ? "Active" : "Switched off"} checked={n.enabled}
          help={n.enabled ? "Switch off to stop it (and its AI cost)." : "Off: it does nothing until switched on."}
          onChange={(on) => save({ enabled: on }, on ? "switched on" : "switched off")} />
      )}

      {n.uses_ai && (
        <>
          <h4>CLAUDE MODEL</h4>
          <select value={n.model || ""} onChange={(e) => save({ model: e.target.value }, "model changed")}>
            {(models || []).map((m) => <option key={m.id} value={m.id}>{m.id} (${m.input}/${m.output} per M tokens)</option>)}
          </select>
          <div className="btn-row">
            <button onClick={() => setShowPrompt(!showPrompt)}>{showPrompt ? "hide" : "show"} instructions{n.prompt_changed ? " (edited)" : ""}</button>
            {n.id === "professor" && <button className="primary" onClick={() => save({ run_now: true }, "review queued: it runs within a minute if budget allows")}>review now</button>}
          </div>
          {showPrompt && (
            <>
              <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={10} />
              <div className="btn-row">
                <button onClick={() => save({ prompt }, "saved: used from the next call")} disabled={prompt === n.prompt}>save</button>
                {n.prompt_changed && <button onClick={() => save({ reset_prompt: true }, "back to the original")}>reset</button>}
              </div>
              <p className="dim small">The answer format is fixed, so an edit can't break the bot.</p>
            </>
          )}
        </>
      )}
      {msg && <p className="ok-msg">{msg}</p>}

      <h4>ITS OWN LOG</h4>
      {mine.length ? (
        <div className="feed mini-feed">
          {mine.map((l, i) => <div key={i} className={`line ${l.level}`}><span className="dim">{fmt.time(l.ts)}</span> {l.message}</div>)}
        </div>
      ) : <p className="dim small">Nothing logged yet in the last 300 lines.</p>}
    </>
  );
}

function FactRow({ k, v }) {
  return <><dt>{k}</dt><dd>{v == null || v === "" ? "–" : String(v)}</dd></>;
}

function TrendDetail({ t }) {
  const b = t.btc || {};
  const pv = t.preview || {};
  const gap = b.gap_pct ?? 0;
  return (
    <>
      <h4>BITCOIN FILTER</h4>
      <div className="gauge">
        <span className="gauge-track"><i style={{ left: `${Math.max(0, Math.min(100, 50 + gap * 2.5))}%` }} className={b.ok ? "ok" : "bad"} /></span>
        <span className={b.ok ? "up" : "down"}>{gap > 0 ? "+" : ""}{gap}% vs {b.days}-day average: {b.ok ? "buys allowed" : "cash only"}</span>
      </div>
      <h4>TONIGHT AT TODAY'S PRICES</h4>
      <dl className="facts">
        <dt>holds now</dt><dd>{pv.hold_now?.join(", ") || "cash"}</dd>
        <dt>would hold</dt><dd>{pv.hold_next?.join(", ") || "cash"}</dd>
        <dt>would buy</dt><dd className="up">{pv.buy?.join(", ") || "–"}</dd>
        <dt>would sell</dt><dd className="down">{pv.sell?.join(", ") || "–"}</dd>
      </dl>
    </>
  );
}

function Checks({ checks }) {
  return (
    <>
      <h4>LAST ORDERS, CHECK BY CHECK</h4>
      {checks.slice(0, 8).map((c, i) => (
        <div key={i} className="check-card">
          <div><span className="dim">{c.time}</span> <b>{c.order}</b> <span className={c.result === "sent" ? "up" : "down"}>{c.result}</span></div>
          <div className="check-list">
            {(c.checks || []).map(([name, st, why], k) => (
              <span key={k} className={`check ${st === "ok" ? "ok" : st === "stop" ? "bad" : "mid"}`} title={why}>
                {st === "ok" ? "✓" : st === "stop" ? "✗" : "!"} {name}: <span className="dim">{why}</span>
              </span>
            ))}
          </div>
        </div>
      ))}
    </>
  );
}

function DetailTable({ t }) {
  const [all, setAll] = useState(false);
  const rows = all ? t.rows : t.rows.slice(0, 15);
  const cell = (v) => (v == null ? "–" : typeof v === "number" ? (Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : Number.isInteger(v) ? v : v.toFixed(Math.abs(v) < 1 ? 3 : 2)) : String(v));
  const tone = (v, col) => (typeof v === "number" && /24h|%|corr|spread|yr/.test(col) ? (v > 0 ? "up" : v < 0 ? "down" : "") : /^(pattern|yes|sent)$/.test(String(v)) ? "up" : /^(hint)$/.test(String(v)) ? "warn" : "");
  return (
    <>
      <h4>DETAIL TABLE <span className="dim">({t.rows.length} rows)</span></h4>
      <div className="scroll insp-table">
        <table>
          <thead><tr>{t.cols.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, i) => <tr key={i}>{r.map((v, k) => <td key={k} className={tone(v, t.cols[k] || "")} title={String(v ?? "")}>{cell(v)}</td>)}</tr>)}
          </tbody>
        </table>
      </div>
      {t.rows.length > 15 && <button className="mini" onClick={() => setAll(!all)}>{all ? "show fewer" : `show all ${t.rows.length}`}</button>}
    </>
  );
}
