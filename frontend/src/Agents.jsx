import { useCallback, useEffect, useMemo, useState } from "react";
import { Background, Controls, Handle, MiniMap, Position, ReactFlow, applyNodeChanges } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Toggle } from "./components.jsx";
import { api, fmt, usePoll } from "./useBot.js";

// Default layout: data on the left, decisions flowing right. Drag nodes to rearrange; positions are remembered.
const COLUMNS = [
  ["src_kraken", "src_yahoo", "src_feargreed", "src_reddit", "src_coingecko", "src_news"],
  ["crypto", "market", "hype", "news"],
  ["detective", "professor"],
  ["predictor"],
  ["risk"],
  ["buyer"],
  ["optimizer"],
];
const KIND_COLOR = { source: "var(--violet)", agent: "var(--cyan)", gate: "var(--amber)" };

function defaultPosition(id) {
  for (let c = 0; c < COLUMNS.length; c++) {
    const r = COLUMNS[c].indexOf(id);
    if (r >= 0) return { x: c * 290, y: r * 150 + (6 - COLUMNS[c].length) * 60 };
  }
  return { x: 0, y: 0 };
}

function loadPositions() {
  try { return JSON.parse(localStorage.getItem("tb-node-pos") || "{}"); } catch { return {}; }
}

function AgentNode({ data }) {
  const n = data.node;
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
        {n.kind}{n.cost ? ` · ${fmt.usd(n.cost)}` : ""} · {n.last_run ? fmt.time(n.last_run) : "–"}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { agent: AgentNode };

export default function Agents({ nodes: agentNodes, avatars = {} }) {
  const [positions, setPositions] = useState(loadPositions);
  const [selected, setSelected] = useState(() => localStorage.getItem("tb-agent") || "news");
  const [rfNodes, setRfNodes] = useState([]);
  useEffect(() => { try { localStorage.setItem("tb-agent", selected || ""); } catch { /* ignore */ } }, [selected]);

  useEffect(() => {
    setRfNodes((prev) => agentNodes.map((n) => {
      const old = prev.find((p) => p.id === n.id);
      return {
        id: n.id, type: "agent", data: { node: n, selected: n.id === selected, avatar: avatars[n.id] },
        position: old?.position || positions[n.id] || defaultPosition(n.id),
      };
    }));
  }, [agentNodes, selected, avatars]);

  const edges = useMemo(() => agentNodes.flatMap((n) => n.inputs.map((src) => {
    const from = agentNodes.find((x) => x.id === src);
    const active = from && from.status !== "off" && Date.now() / 1000 - from.last_run < 60;
    return {
      id: `${src}-${n.id}`, source: src, target: n.id, animated: active,
      style: { stroke: from?.status === "error" ? "var(--red)" : from?.kind === "source" ? "var(--violet)" : "var(--cyan)", strokeWidth: 2, opacity: active ? 0.9 : 0.25 },
    };
  })), [agentNodes]);

  const onNodesChange = useCallback((changes) => {
    setRfNodes((nds) => {
      const next = applyNodeChanges(changes, nds);
      const pos = Object.fromEntries(next.map((n) => [n.id, n.position]));
      setPositions(pos);
      try { localStorage.setItem("tb-node-pos", JSON.stringify(pos)); } catch { /* ignore */ }
      return next;
    });
  }, []);

  const sel = agentNodes.find((n) => n.id === selected);
  return (
    <div className="nodes-view">
      <div className="flow">
        <ReactFlow nodes={rfNodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange}
          onNodeClick={(_, n) => setSelected(n.id)} fitView colorMode="dark" proOptions={{ hideAttribution: true }}>
          <Background color="#1b2440" gap={24} />
          <MiniMap pannable zoomable nodeColor={(n) => (n.data.node.kind === "source" ? "#8a5cff" : n.data.node.kind === "gate" ? "#ffb020" : "#00f0ff")} maskColor="rgba(5,6,10,0.7)" />
          <Controls />
        </ReactFlow>
        <button className="reset-layout" onClick={() => { localStorage.removeItem("tb-node-pos"); setPositions({}); setRfNodes((r) => r.map((n) => ({ ...n, position: defaultPosition(n.id) }))); }}>reset layout</button>
        <div className="roster">
          {agentNodes.map((n) => (
            <button key={n.id} className={n.id === selected ? "active" : ""} onClick={() => setSelected(n.id)}>
              <span className={`dot st-${n.status}`} />{n.name}
            </button>
          ))}
        </div>
      </div>
      <aside className="inspector">
        {sel ? <Inspector n={sel} avatar={avatars[sel.id]} byId={Object.fromEntries(agentNodes.map((x) => [x.id, x.name]))} /> : <p className="dim">Click an agent to see its job and settings.</p>}
      </aside>
    </div>
  );
}

function Avatar({ src, size }) {
  return /\.(mp4|webm)$/i.test(src)
    ? <video className="avatar" src={src} autoPlay loop muted playsInline style={{ width: size, height: size }} />
    : <img className="avatar" src={src} alt="" style={{ width: size, height: size }} />;
}

function Inspector({ n, byId, avatar }) {
  const [models] = usePoll("models", 0);
  const [prompt, setPrompt] = useState(n.prompt || "");
  const [msg, setMsg] = useState("");
  useEffect(() => { setPrompt(n.prompt || ""); setMsg(""); }, [n.id, n.prompt]);
  const save = async (body, ok) => {
    try { await api(`agents/${n.id}`, body); setMsg(ok); } catch (e) { setMsg(e.message); }
  };
  const detail = { ...n.detail };
  return (
    <>
      <div className="insp-title">
        {avatar && <Avatar src={avatar} size={72} />}
        <div><h3>{n.name}</h3><p className="role">{n.role}</p></div>
      </div>
      <div className="insp-status">
        <span className={`dot st-${n.status}`} /> <b>{n.status}</b> <span className="dim">· {n.summary}</span>
      </div>
      {n.explain && <><h4>WHAT IT DOES</h4><p>{n.explain}</p></>}
      <dl>
        <dt>reads from</dt><dd>{n.inputs.map((i) => byId[i] || i).join(", ") || "the internet"}</dd>
        {n.outputs && <><dt>hands on</dt><dd>{n.outputs}</dd></>}
        <dt>runs</dt><dd>{n.runs}{n.last_run ? `, last ${fmt.time(n.last_run)}` : ""}</dd>
        {n.uses_ai && <><dt>AI cost</dt><dd>{fmt.usd(n.cost)} this session</dd></>}
      </dl>
      {n.can_disable && (
        <Toggle label={n.enabled ? "Active" : "Switched off"} checked={n.enabled}
          help={n.enabled ? "Switch off to remove its influence (its signal then counts as neutral)." : "Its signal counts as neutral while off."}
          onChange={(on) => save({ enabled: on }, on ? "switched on" : "switched off")} />
      )}
      {!n.can_disable && n.kind !== "source" && <p className="dim small">Essential: this agent can't be switched off.</p>}
      {n.uses_ai && (
        <>
          <h4>CLAUDE MODEL</h4>
          <select value={n.model || ""} onChange={(e) => save({ model: e.target.value }, "model changed")}>
            {(models || []).map((m) => <option key={m.id} value={m.id}>{m.id} (${m.input}/${m.output} per M tokens)</option>)}
          </select>
          <h4>INSTRUCTIONS (PROMPT) {n.prompt_changed && <span className="crown">edited</span>}</h4>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={9} />
          <div className="btn-row">
            <button onClick={() => save({ prompt }, "saved: used from the next call")} disabled={prompt === n.prompt}>save</button>
            {n.prompt_changed && <button onClick={() => save({ reset_prompt: true }, "back to the original")}>reset</button>}
            {n.id === "professor" && <button onClick={() => save({ run_now: true }, "review queued: it runs within a tick if data and budget allow")}>review now</button>}
          </div>
          <p className="dim small">The answer format is fixed, so an edit can't break the bot. {"{symbols}"} and {"{fee}"} are filled in automatically.</p>
        </>
      )}
      {msg && <p className="ok-msg">{msg}</p>}
      <h4>LAST OUTPUT</h4>
      <pre>{JSON.stringify(detail, null, 2)}</pre>
    </>
  );
}
