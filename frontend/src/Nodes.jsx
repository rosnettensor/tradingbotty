import { useCallback, useEffect, useMemo, useState } from "react";
import { Background, Controls, Handle, MiniMap, Position, ReactFlow, applyNodeChanges } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { fmt } from "./useBot.js";

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
    <div className={`agent-node ${n.kind} st-${n.status}`} style={{ "--k": KIND_COLOR[n.kind] }}>
      <Handle type="target" position={Position.Left} />
      <div className="an-head">
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

export default function Nodes({ nodes: agentNodes }) {
  const [positions, setPositions] = useState(loadPositions);
  const [selected, setSelected] = useState(null);
  const [rfNodes, setRfNodes] = useState([]);

  useEffect(() => {
    setRfNodes((prev) => agentNodes.map((n) => {
      const old = prev.find((p) => p.id === n.id);
      return {
        id: n.id, type: "agent", data: { node: n },
        position: old?.position || positions[n.id] || defaultPosition(n.id),
      };
    }));
  }, [agentNodes]);

  const edges = useMemo(() => agentNodes.flatMap((n) => n.inputs.map((src) => {
    const from = agentNodes.find((x) => x.id === src);
    const active = from && Date.now() / 1000 - from.last_run < 30;
    return {
      id: `${src}-${n.id}`, source: src, target: n.id, animated: active,
      style: { stroke: from?.status === "error" ? "var(--red)" : from?.kind === "source" ? "var(--violet)" : "var(--cyan)", strokeWidth: 2, opacity: active ? 0.9 : 0.35 },
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
          onNodeClick={(_, n) => setSelected(n.id)} onPaneClick={() => setSelected(null)} fitView colorMode="dark"
          proOptions={{ hideAttribution: true }}>
          <Background color="#1b2440" gap={24} />
          <MiniMap pannable zoomable nodeColor={(n) => (n.data.node.kind === "source" ? "#8a5cff" : n.data.node.kind === "gate" ? "#ffb020" : "#00f0ff")} maskColor="rgba(5,6,10,0.7)" />
          <Controls />
        </ReactFlow>
        <button className="reset-layout" onClick={() => { localStorage.removeItem("tb-node-pos"); setPositions({}); setRfNodes((r) => r.map((n) => ({ ...n, position: defaultPosition(n.id) }))); }}>reset layout</button>
      </div>
      <aside className={`inspector ${sel ? "open" : ""}`}>
        {sel ? (
          <>
            <h3>{sel.name}</h3>
            <p className="dim">{sel.role}</p>
            <dl>
              <dt>status</dt><dd className={`st-${sel.status}`}>{sel.status}</dd>
              <dt>runs</dt><dd>{sel.runs}</dd>
              <dt>AI cost</dt><dd>{fmt.usd(sel.cost)}</dd>
              <dt>inputs</dt><dd>{sel.inputs.join(", ") || "none"}</dd>
              <dt>now</dt><dd>{sel.summary}</dd>
            </dl>
            <h4>last output</h4>
            <pre>{JSON.stringify(sel.detail, null, 2)}</pre>
          </>
        ) : <p className="dim">Click a node to inspect what it knows and what it last produced.</p>}
      </aside>
    </div>
  );
}
