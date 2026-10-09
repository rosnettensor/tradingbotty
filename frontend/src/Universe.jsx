import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

// The agent universe: every agent is a floating sphere, every string a real input.
// - size: what the agent earned or cost in your currency (scoreboard), the ring around it: green gain / red loss,
//   its arc = its share of the biggest score
// - color: violet data source, cyan agent, amber gate on real money; red = error, grey = off
// - breathing: fast and bright while it works (ran, logged or changed in the last 20 s), slow while it waits
// - lights on a string: data flowing right now; a burst and a shockwave when the agent writes to the log
// - small moons: the agent thinks with Claude (AI)
// Colors come from the palette (CSS tokens) and follow it when you switch.

// left to right: data, scouts, thinkers, judges, traders, the gate, the desk that touches real money
const LAYERS = [
  ["src_fusion", "src_kraken", "src_binance", "src_free", "src_news"],
  ["radar", "trend", "collector", "news", "regime"],
  ["patterns", "researcher", "thinktank"],
  ["guardian", "professor", "aimanager", "bank", "strategist"],
  ["brain", "fast", "you"],
  ["risk"],
  ["livedesk"],
];
// parts of the bot that aren't agents on the server but belong in the picture (Bauplan 2)
const VIRTUAL = [
  { id: "bank", name: "Bank & Prüfer", kind: "agent", inputs: ["researcher", "thinktank"], status: "ok",
    summary: "verteilt das Geld des Daily Brain auf geprüfte Strategien", virtual: true },
  { id: "you", name: "Du", kind: "you", inputs: [], status: "ok", summary: "Chat-Orders und dein Kurs (Mutig, Bunkern, Pause)", virtual: true },
];
const EXTRA_INPUTS = { brain: ["bank"], risk: ["you"] };
const SCORE_ID = { brain: "brain", fast: "fast", guardian: "guardian", risk: "risk", professor: "professor", you: ["you", "course"] };

function position(id) {
  for (let c = 0; c < LAYERS.length; c++) {
    const r = LAYERS[c].indexOf(id);
    if (r < 0) continue;
    const n = LAYERS[c].length;
    const a = (r / n) * Math.PI * 2 + c * 0.9;
    const rad = n > 1 ? 1.25 + n * 0.32 : 0;
    return new THREE.Vector3((c - (LAYERS.length - 1) / 2) * 2.6, Math.sin(a) * rad, Math.cos(a) * rad);
  }
  return new THREE.Vector3(0, -4, 0);
}

function glowTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grd.addColorStop(0, "rgba(255,255,255,1)");
  grd.addColorStop(0.18, "rgba(255,255,255,0.55)");
  grd.addColorStop(0.5, "rgba(255,255,255,0.12)");
  grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd;
  g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

const VERT = `
varying vec3 vN; varying vec3 vV; varying vec3 vP;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vN = normalize(normalMatrix * normal); vV = normalize(-mv.xyz); vP = position;
  gl_Position = projectionMatrix * mv;
}`;
// glass sphere: bright fresnel rim, drifting bands inside, a second color mixed in from the rim
const FRAG = `
uniform vec3 uColor; uniform vec3 uRim; uniform float uTime; uniform float uGlow; uniform float uAlpha;
varying vec3 vN; varying vec3 vV; varying vec3 vP;
void main() {
  float f = pow(1.0 - max(dot(vN, vV), 0.0), 2.4);
  float bands = 0.5 + 0.5 * sin(vP.y * 9.0 + uTime * 1.3 + sin(vP.x * 4.0 + uTime * 0.7) * 1.6);
  float swirl = 0.5 + 0.5 * sin((vP.x + vP.z) * 6.0 - uTime * 0.9);
  vec3 col = uColor * (0.10 + 0.22 * bands * swirl) * uGlow + mix(uColor, uRim, 0.55) * f * (1.1 + uGlow * 0.6);
  gl_FragColor = vec4(col, (0.25 + f * 0.9) * uAlpha);
}`;

function readPalette() {
  const css = getComputedStyle(document.documentElement);
  const c = (name, fb) => { const col = new THREE.Color(fb); try { const v = css.getPropertyValue(name).trim(); if (v) col.setStyle(v); } catch { /* keep */ } return col; };
  return {
    source: c("--violet", "#b49bff"), agent: c("--cyan", "#5ee7ff"), gate: c("--amber", "#ffc861"), you: c("--magenta", "#ff7ad9"),
    green: c("--green", "#4dffa6"), red: c("--red", "#ff6b85"), dim: c("--dim", "#a3b0d4"), hi: c("--hi", "#ffffff"),
  };
}

// light palettes draw ink on paper (normal blending); dark ones glow (additive light)
const inkMode = () => getComputedStyle(document.documentElement).getPropertyValue("--sphere-blend").trim() === "normal";

const fmtScore = (x) => `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(2)}`;

export default function Universe({ nodes, log, scores, currency = "CHF", selected, onSelect }) {
  const wrap = useRef(null);
  const mount = useRef(null);
  const labels = useRef(null);
  const tip = useRef(null);
  const live = useRef({});
  const [hover, setHover] = useState(null);
  const [ink, setInk] = useState(inkMode);
  useEffect(() => {  // a light palette needs other materials: rebuild the scene when it flips
    const on = () => setInk(inkMode());
    window.addEventListener("tb-skin", on);
    return () => window.removeEventListener("tb-skin", on);
  }, []);
  live.current.nodes = nodes;
  live.current.log = log;
  live.current.scores = scores;
  live.current.selected = selected;
  live.current.onSelect = onSelect;

  useEffect(() => {
    const el = mount.current;
    const BLEND = ink ? THREE.NormalBlending : THREE.AdditiveBlending;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 200);
    camera.position.set(-3.5, 3.2, 17);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;
    controls.enablePan = false;
    controls.minDistance = 5;
    controls.maxDistance = 46;
    const calm = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    controls.autoRotate = !calm;
    controls.autoRotateSpeed = 0.35;
    let idleAt = 0;
    controls.addEventListener("start", () => { controls.autoRotate = false; idleAt = 0; });
    controls.addEventListener("end", () => { idleAt = performance.now(); });

    const glow = glowTexture();
    let pal = readPalette();
    const outer = new THREE.Group();   // a slow sway of everything
    const world = new THREE.Group();   // turned upright on a portrait screen: data on top, the Live Desk at the bottom
    outer.add(world);
    scene.add(outer);

    // star dust in the back, so rotating feels like space
    const dustGeo = new THREE.BufferGeometry();
    const dust = new Float32Array(900 * 3);
    for (let i = 0; i < 900; i++) {
      const r = 26 + Math.random() * 30, th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
      dust.set([r * Math.sin(ph) * Math.cos(th), r * Math.cos(ph) * 0.6, r * Math.sin(ph) * Math.sin(th)], i * 3);
    }
    dustGeo.setAttribute("position", new THREE.BufferAttribute(dust, 3));
    const dustMat = new THREE.PointsMaterial({ size: 0.09, color: pal.hi, transparent: true, opacity: 0.35, depthWrite: false });
    scene.add(new THREE.Points(dustGeo, dustMat));

    const sphereGeo = new THREE.SphereGeometry(1, 48, 32);
    const coreGeo = new THREE.SphereGeometry(0.3, 20, 14);
    const moonGeo = new THREE.SphereGeometry(0.07, 10, 8);
    const bodies = new Map();   // id -> sphere and its parts
    const strings = new Map();  // "a>b" -> line and its lights
    const sparks = [];          // lights traveling on strings
    const waves = [];           // shockwaves when an agent logs
    const SEG = 40;

    const ensureBody = (n) => {
      let b = bodies.get(n.id);
      if (b) return b;
      const home = position(n.id);
      const g = new THREE.Group();
      g.position.copy(home);
      const mat = new THREE.ShaderMaterial({
        vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false, blending: BLEND,
        uniforms: { uColor: { value: new THREE.Color() }, uRim: { value: new THREE.Color() }, uTime: { value: 0 }, uGlow: { value: 1 }, uAlpha: { value: 1 } },
      });
      const shell = new THREE.Mesh(sphereGeo, mat);
      shell.userData.id = n.id;
      const coreMat = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.85, blending: BLEND, depthWrite: false });
      const core = new THREE.Mesh(coreGeo, coreMat);
      const haloMat = new THREE.SpriteMaterial({ map: glow, transparent: true, blending: BLEND, depthWrite: false, opacity: 0.5 });
      const halo = new THREE.Sprite(haloMat);
      const ringMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.9, blending: BLEND, depthWrite: false, side: THREE.DoubleSide });
      const ring = new THREE.Mesh(new THREE.TorusGeometry(1.32, 0.05, 8, 96, 0.001), ringMat);
      ring.rotation.x = 1.0;
      const selMat = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0, blending: BLEND, depthWrite: false, side: THREE.DoubleSide });
      const sel = new THREE.Mesh(new THREE.RingGeometry(1.55, 1.62, 96), selMat);
      const moons = [];
      g.add(halo, shell, core, ring, sel);
      world.add(g);
      b = { g, home, shell, mat, core, coreMat, halo, haloMat, ring, ringMat, sel, selMat, moons, arc: 0, r: 0.5, seed: Math.random() * 100,
        busyAt: 0, sig: "", n };
      bodies.set(n.id, b);
      return b;
    };

    const ensureString = (from, to) => {
      const key = `${from}>${to}`;
      let s = strings.get(key);
      if (s) return s;
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array((SEG + 1) * 3), 3));
      const mat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.3, blending: BLEND, depthWrite: false });
      const line = new THREE.Line(geo, mat);
      world.add(line);
      s = { key, from, to, line, geo, mat, curve: new THREE.QuadraticBezierCurve3(new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()), next: 0, hot: 0 };
      strings.set(key, s);
      return s;
    };

    const spark = (s, speed = 0.45, size = 0.5, bright = 1) => {
      const m = new THREE.SpriteMaterial({ map: glow, transparent: true, blending: BLEND, depthWrite: false, opacity: bright });
      m.color.copy(s.mat.color);
      const sp = new THREE.Sprite(m);
      sp.scale.setScalar(size);
      world.add(sp);
      sparks.push({ sp, s, t: 0, speed, bright });
    };

    const wave = (b, color) => {
      const m = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.8, blending: BLEND, depthWrite: false, side: THREE.DoubleSide });
      const w = new THREE.Mesh(new THREE.RingGeometry(0.96, 1.0, 64), m);
      w.position.copy(b.g.position);
      w.lookAt(camera.position);
      world.add(w);
      waves.push({ w, m, t: 0, r: b.r });
    };

    const onPalette = () => {
      pal = readPalette();
      dustMat.color.copy(pal.hi);
    };
    window.addEventListener("tb-skin", onPalette);

    // labels: plain HTML that follows the spheres (sharp text at any zoom)
    const labelEls = new Map();
    const label = (id) => {
      let d = labelEls.get(id);
      if (!d && labels.current) {
        d = document.createElement("div");
        d.className = "uv-label";
        labels.current.appendChild(d);
        labelEls.set(id, d);
      }
      return d;
    };

    // picking: a click (not a drag) selects, hovering shows a card
    const ray = new THREE.Raycaster();
    const ptr = new THREE.Vector2();
    let down = null;
    const pick = (ev) => {
      const r = renderer.domElement.getBoundingClientRect();
      ptr.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ptr, camera);
      const hit = ray.intersectObjects([...bodies.values()].map((b) => b.shell), false)[0];
      return hit?.object.userData.id || null;
    };
    let lastHover = null;
    const onMove = (ev) => {
      const id = pick(ev);
      renderer.domElement.style.cursor = id ? "pointer" : "grab";
      if (id !== lastHover) { lastHover = id; setHover(id); }
      if (tip.current) {
        const r = wrap.current.getBoundingClientRect();
        tip.current.style.transform = `translate(${Math.min(ev.clientX - r.left + 14, r.width - 250)}px, ${ev.clientY - r.top + 14}px)`;
      }
    };
    const onDown = (ev) => { down = [ev.clientX, ev.clientY]; };
    const onUp = (ev) => {
      if (!down || Math.hypot(ev.clientX - down[0], ev.clientY - down[1]) > 6) return;
      const id = pick(ev);
      if (id) {
        live.current.onSelect?.(id);
        focus = id;
      }
    };
    const onLeave = () => { lastHover = null; setHover(null); };
    let focus = null;
    renderer.domElement.addEventListener("pointermove", onMove);
    renderer.domElement.addEventListener("pointerdown", onDown);
    renderer.domElement.addEventListener("pointerup", onUp);
    renderer.domElement.addEventListener("pointerleave", onLeave);
    renderer.domElement.addEventListener("dblclick", () => { focus = null; fit(); });

    const view = new THREE.Vector3(-0.16, 0.2, 1).normalize();
    const fit = () => {   // the whole universe fills the frame, whatever the screen
      const portrait = camera.aspect < 0.85;
      world.rotation.z = portrait ? -Math.PI / 2 : 0;
      const halfW = portrait ? 3.6 : 8.3, halfH = portrait ? 9.8 : 3.8;
      const tv = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
      const dist = Math.max(halfH / tv, halfW / (tv * camera.aspect)) + 2.5;
      controls.target.set(0, 0, 0);
      camera.position.copy(view).multiplyScalar(Math.min(controls.maxDistance, dist));
      controls.update();
    };
    const resize = () => {
      const w = el.clientWidth || 600, h = el.clientHeight || 400;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.fov = 42;
      camera.updateProjectionMatrix();
      fit();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    let lastLog = Date.now() / 1000;
    let raf = 0, prev = performance.now(), visible = true;
    const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; });
    io.observe(el);
    const tmp = new THREE.Vector3(), mid = new THREE.Vector3(), target = new THREE.Vector3();

    const frame = (nowMs) => {
      raf = requestAnimationFrame(frame);
      if (!visible || document.hidden) { prev = nowMs; return; }
      const dt = Math.min(0.05, (nowMs - prev) / 1000);
      prev = nowMs;
      const t = nowMs / 1000, now = Date.now() / 1000;
      const L = live.current;
      const all = [...(L.nodes || []), ...VIRTUAL.filter((v) => !(L.nodes || []).some((n) => n.id === v.id))];
      const byId = new Map(all.map((n) => [n.id, n]));
      const sc = L.scores || {};
      const top = Math.max(1, ...Object.values(sc).map((v) => Math.abs(v)));

      // who is busy? running, logged or changed in the last 20 seconds
      const logged = {};
      for (const l of L.log || []) logged[l.agent] = Math.max(logged[l.agent] || 0, l.ts);
      for (const l of L.log || []) {
        if (l.ts <= lastLog) continue;
        const n = all.find((x) => x.name === l.agent);
        const b = n && bodies.get(n.id);
        if (b) {
          b.busyAt = now;
          wave(b, l.level === "error" ? pal.red : l.level === "live" || l.level === "trade" ? pal.green : b.mat.uniforms.uColor.value);
          for (const s of strings.values()) if (s.from === n.id) { spark(s, 0.9, 0.7, 1); spark(s, 0.75, 0.5, 0.8); }
        }
      }
      for (const l of L.log || []) lastLog = Math.max(lastLog, l.ts);

      // spheres
      for (const n of all) {
        const b = ensureBody(n);
        b.n = n;
        const sig = `${n.status}|${n.summary}`;
        if (b.sig && b.sig !== sig) b.busyAt = now;
        b.sig = sig;
        const sid = SCORE_ID[n.id] || (n.uses_ai || n.id === "thinktank" ? `ai:${n.name}` : null);
        const score = Array.isArray(sid) ? sid.reduce((a, k) => a + (sc[k] || 0), 0) : sid ? sc[sid] : undefined;
        b.score = score;
        const off = n.status === "off" || n.enabled === false;
        const err = n.status === "error";
        const busy = !off && (n.status === "running" || now - (logged[n.name] || 0) < 20 || now - b.busyAt < 20
          || (n.kind === "source" && n.status === "ok" && now - (n.last_run || 0) < 20));
        b.busy = busy;
        const base = n.kind === "source" ? 0.42 : n.kind === "gate" ? 0.6 : n.kind === "you" ? 0.55 : 0.52;
        const want = base * (1 + (score != null ? 0.75 * Math.tanh(Math.abs(score) / 12) : 0));
        b.r += (want - b.r) * Math.min(1, dt * 3);
        const col = err ? pal.red : off ? pal.dim : pal[n.kind] || pal.agent;
        b.mat.uniforms.uColor.value.lerp(col, Math.min(1, dt * 4));
        const rim = score > 0.005 ? pal.green : score < -0.005 ? pal.red : col;
        b.mat.uniforms.uRim.value.lerp(rim, Math.min(1, dt * 4));
        b.mat.uniforms.uTime.value = t + b.seed;
        const beat = busy ? 0.5 + 0.5 * Math.sin(t * 5.2 + b.seed) : 0.5 + 0.5 * Math.sin(t * 1.3 + b.seed);
        b.mat.uniforms.uGlow.value = off ? 0.35 : (busy ? 1.25 : 0.85) + beat * (busy ? 0.45 : 0.15) + (err ? 0.4 * Math.sin(t * 14) : 0);
        const isSel = L.selected === n.id;
        const conn = L.selected && (isSel || n.inputs?.includes(L.selected) || byId.get(L.selected)?.inputs?.includes(n.id)
          || (EXTRA_INPUTS[n.id] || []).includes(L.selected) || (EXTRA_INPUTS[L.selected] || []).includes(n.id));
        b.mat.uniforms.uAlpha.value = !L.selected || conn ? (off ? 0.45 : 1) : (off ? 0.3 : 0.62);
        const s = b.r * (1 + (busy ? 0.07 : 0.025) * Math.sin(t * (busy ? 5.2 : 1.3) + b.seed));
        b.shell.scale.setScalar(s);
        b.core.scale.setScalar(s * (0.8 + beat * 0.5));
        b.coreMat.color.copy(col).lerp(pal.hi, 0.55);
        b.coreMat.opacity = off ? 0.2 : (0.45 + beat * 0.45) * (!L.selected || conn ? 1 : 0.65);
        b.haloMat.color.copy(col);
        b.haloMat.opacity = off ? 0.08 : (busy ? 0.55 : 0.28) * (!L.selected || conn ? 1 : 0.7);
        b.halo.scale.setScalar(s * (busy ? 6.5 + beat * 1.5 : 5));
        // score ring: arc = share of the biggest score, green gain / red loss
        const arc = score ? Math.max(0.15, (Math.abs(score) / top) * Math.PI * 2) : 0;
        if (Math.abs(arc - b.arc) > 0.02) {
          b.ring.geometry.dispose();
          b.ring.geometry = new THREE.TorusGeometry(1.32, 0.05, 8, 96, Math.max(0.001, arc));
          b.arc = arc;
        }
        b.ring.visible = arc > 0;
        b.ringMat.color.copy(score >= 0 ? pal.green : pal.red);
        b.ring.scale.setScalar(s);
        b.ring.rotation.z = t * 0.25 + b.seed;
        b.ringMat.opacity = !L.selected || conn ? 0.75 : 0.45;
        b.sel.scale.setScalar(s);
        b.sel.lookAt(camera.position);
        b.selMat.opacity = isSel ? 0.55 + 0.3 * Math.sin(t * 3) : 0;
        // moons for agents that think with Claude
        const wantMoons = n.uses_ai ? 2 : 0;
        while (b.moons.length < wantMoons) {
          const m = new THREE.Mesh(moonGeo, new THREE.MeshBasicMaterial({ color: pal.hi, transparent: true, opacity: 0.9, blending: BLEND }));
          b.g.add(m);
          b.moons.push(m);
        }
        b.moons.forEach((m, i) => {
          const a = t * (busy ? 2.4 : 0.9) + i * Math.PI + b.seed;
          m.position.set(Math.cos(a) * s * 1.7, Math.sin(a * 0.7) * s * 0.5, Math.sin(a) * s * 1.7);
          m.material.opacity = !L.selected || conn ? 0.9 : 0.6;
        });
        // float
        b.g.position.set(b.home.x + Math.sin(t * 0.31 + b.seed) * 0.12, b.home.y + Math.sin(t * 0.47 + b.seed * 2) * 0.18,
          b.home.z + Math.cos(t * 0.27 + b.seed) * 0.12);
        // label
        const d = label(n.id);
        if (d) {
          world.localToWorld(tmp.copy(b.g.position));
          tmp.y -= s * 1.25 + 0.18;
          tmp.project(camera);
          const w = el.clientWidth, h = el.clientHeight;
          const behind = tmp.z > 1;
          d.style.transform = `translate(-50%, 0) translate(${((tmp.x + 1) / 2) * w}px, ${((1 - tmp.y) / 2) * h}px)`;
          d.style.opacity = behind ? 0 : !L.selected || conn ? 1 : 0.7;
          const txt = `${n.name}${score != null && Math.abs(score) >= 0.005 ? `|${fmtScore(score)}` : ""}`;
          if (d.dataset.t !== txt) {
            d.dataset.t = txt;
            const [nm, scs] = txt.split("|");
            d.innerHTML = "";
            const a = document.createElement("span");
            a.textContent = nm;
            d.appendChild(a);
            if (scs) {
              const v = document.createElement("b");
              v.textContent = scs;
              v.className = score >= 0 ? "up" : "down";
              d.appendChild(v);
            }
          }
          d.classList.toggle("src", n.kind === "source");
          d.classList.toggle("sel", isSel);
        }
      }
      for (const [id, b] of bodies) {
        if (byId.has(id)) continue;
        world.remove(b.g);
        bodies.delete(id);
        labelEls.get(id)?.remove();
        labelEls.delete(id);
      }

      // strings: real inputs, bent upwards a little; lights travel while data flows
      const seen = new Set();
      for (const n of all) {
        for (const src of [...(n.inputs || []), ...(EXTRA_INPUTS[n.id] || [])]) {
          const a = bodies.get(src), b = bodies.get(n.id);
          if (!a || !b) continue;
          const s = ensureString(src, n.id);
          seen.add(s.key);
          mid.addVectors(a.g.position, b.g.position).multiplyScalar(0.5);
          mid.y += 0.6 + a.g.position.distanceTo(b.g.position) * 0.08;
          s.curve.v0.copy(a.g.position);
          s.curve.v1.copy(mid);
          s.curve.v2.copy(b.g.position);
          const arr = s.geo.attributes.position.array;
          for (let i = 0; i <= SEG; i++) {
            s.curve.getPoint(i / SEG, tmp);
            arr[i * 3] = tmp.x; arr[i * 3 + 1] = tmp.y; arr[i * 3 + 2] = tmp.z;
          }
          s.geo.attributes.position.needsUpdate = true;
          s.geo.computeBoundingSphere();
          const from = a.n;
          const col = from.status === "error" ? pal.red : from.kind === "source" ? pal.source : from.kind === "gate" ? pal.gate
            : from.kind === "you" ? pal.you : pal.agent;
          s.mat.color.copy(col);
          const conn = L.selected && (src === L.selected || n.id === L.selected);
          const off = from.status === "off" || n.status === "off";
          s.mat.opacity = off ? 0.08 : conn ? 0.95 : (a.busy ? 0.5 : 0.26) * (L.selected ? 0.8 : 1);  // every string stays visible, the chosen sphere's shine
          if (a.busy && !off && now > s.next) {
            spark(s, 0.42 + Math.random() * 0.15, conn ? 0.55 : 0.42, conn || !L.selected ? 1 : 0.75);
            s.next = now + (a.n.kind === "source" ? 2.6 : 1.4) + Math.random();
          }
        }
      }
      for (const [k, s] of strings) {
        if (seen.has(k)) continue;
        world.remove(s.line);
        s.geo.dispose();
        strings.delete(k);
      }
      for (let i = sparks.length - 1; i >= 0; i--) {
        const p = sparks[i];
        p.t += dt * p.speed;
        if (p.t >= 1 || !strings.has(p.s.key)) {
          world.remove(p.sp);
          p.sp.material.dispose();
          sparks.splice(i, 1);
          const b = bodies.get(p.s.to);
          if (b && p.t >= 1) b.haloMat.opacity = Math.min(1, b.haloMat.opacity + 0.25);
          continue;
        }
        const e = p.t < 0.5 ? 2 * p.t * p.t : 1 - (-2 * p.t + 2) ** 2 / 2;
        p.s.curve.getPoint(e, p.sp.position);
        p.sp.material.opacity = (Math.sin(Math.PI * p.t) * 0.85 + 0.15) * p.bright;
      }
      for (let i = waves.length - 1; i >= 0; i--) {
        const wv = waves[i];
        wv.t += dt * 0.8;
        wv.w.scale.setScalar(wv.r * (1 + wv.t * 3.2));
        wv.w.lookAt(camera.position);
        wv.m.opacity = 0.8 * (1 - wv.t);
        if (wv.t >= 1) { world.remove(wv.w); wv.w.geometry.dispose(); wv.m.dispose(); waves.splice(i, 1); }
      }

      // camera: fly to the clicked sphere, double click flies home; slow drift when you leave it alone
      if (focus) {
        const fb = focus !== "home" && bodies.get(focus);
        if (fb) world.localToWorld(target.copy(fb.g.position));
        else target.set(0, 0, 0);
        controls.target.lerp(target, Math.min(1, dt * 2.5));
        if (controls.target.distanceTo(target) < 0.02) focus = null;
      }
      if (!calm && idleAt && nowMs - idleAt > 9000) { controls.autoRotate = true; idleAt = 0; }
      controls.update();
      outer.rotation.y = Math.sin(t * 0.05) * 0.06;
      renderer.render(scene, camera);
    };
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      window.removeEventListener("tb-skin", onPalette);
      controls.dispose();
      renderer.dispose();
      scene.traverse((o) => { o.geometry?.dispose?.(); o.material?.dispose?.(); });
      glow.dispose();
      labelEls.forEach((d) => d.remove());
      el.removeChild(renderer.domElement);
    };
  }, [ink]);

  const all = [...(nodes || []), ...VIRTUAL];
  const h = hover && all.find((n) => n.id === hover);
  const hs = h && (() => {
    const sid = SCORE_ID[h.id] || (h.uses_ai || h.id === "thinktank" ? `ai:${h.name}` : null);
    return Array.isArray(sid) ? sid.reduce((a, k) => a + ((scores || {})[k] || 0), 0) : sid ? (scores || {})[sid] : undefined;
  })();
  return (
    <div className="universe" ref={wrap}>
      <div className="uv-canvas" ref={mount} />
      <div className="uv-labels" ref={labels} aria-hidden="true" />
      <div className="uv-tip" ref={tip} style={{ opacity: h ? 1 : 0 }}>
        {h && <>
          <b>{h.name}</b> <span className={`dot st-${h.status}`} /> <span className="dim small">{h.status}</span>
          {hs != null && <div className={hs >= 0 ? "up" : "down"}>{fmtScore(hs)} {currency} <span className="dim small">Punktestand</span></div>}
          <div className="dim small">{h.summary}</div>
          {!h.virtual && <div className="small">Klick: Details · Doppelklick: zurück</div>}
        </>}
      </div>
    </div>
  );
}
