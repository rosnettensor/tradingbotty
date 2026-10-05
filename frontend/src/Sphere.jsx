import { useEffect, useRef, useState } from "react";
import * as THREE from "three";

// The live core: every layer is real data.
// - surface color: the bot's own gain (green) or loss (red); wobble: how much the market moves today
// - long spikes: the bot's coins, length = gain or loss on that coin, green up / red down
// - short spikes: the watchlist, length = how close a coin is to its buy line (amber near, green = buy tonight)
// - inner cage: Bitcoin filter (green = buys allowed, red = holding cash)
// - halo: share of coins up in the last 24h (green) vs down (red)
// - outer ring: cash (cyan) vs coins (magenta) in your account
// - red sparks: Guardian blocks; shockwave: every real trade (green buy, red sell)
// colors come from the dashboard skin (CSS tokens) and change when you switch it
const GREEN = new THREE.Color(0x39ff88), RED = new THREE.Color(0xff3b5c), CYAN = new THREE.Color(0x00f0ff);
const AMBER = new THREE.Color(0xffb020), MAGENTA = new THREE.Color(0xff2bd6), DIM = new THREE.Color(0x3a5a7a);
const INK = new THREE.Color(0xffffff);

function readSkin() {
  const css = getComputedStyle(document.documentElement);
  const set = (c, name) => { const v = css.getPropertyValue(name).trim(); if (v) { try { c.setStyle(v); } catch { /* keep */ } } };
  set(GREEN, "--green"); set(RED, "--red"); set(CYAN, "--cyan"); set(AMBER, "--amber"); set(MAGENTA, "--magenta"); set(DIM, "--dim");
  set(INK, "--hi");
  return css.getPropertyValue("--sphere-blend").trim() === "normal" ? THREE.NormalBlending : THREE.AdditiveBlending;
}

export const LAYERS = [
  ["var(--green)", "Surface color", "the bot's own gain (green) or loss (red)"],
  ["var(--text)", "Long spikes", "the bot's coins: length = gain or loss on each"],
  ["var(--amber)", "Short spikes", "watchlist: length = closeness to the buy line"],
  ["var(--green)", "Inner cage", "Bitcoin filter: green = buys allowed, red = cash"],
  ["var(--cyan)", "Halo", "share of coins up (green) vs down (red) in 24h"],
  ["var(--magenta)", "Outer ring", "your account: cash (cyan) vs coins (magenta)"],
  ["var(--red)", "Red sparks", "Guardian blocks after hack or delisting news"],
  ["var(--text)", "Shockwave", "a real trade: green buy, red sell"],
];

function fib(n) {
  const out = [], g = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / (n - 1)) * 2, r = Math.sqrt(1 - y * y), th = g * i;
    out.push(new THREE.Vector3(Math.cos(th) * r, y, Math.sin(th) * r));
  }
  return out;
}

function glowTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grd.addColorStop(0, "rgba(255,255,255,1)");
  grd.addColorStop(0.25, "rgba(255,255,255,0.6)");
  grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd;
  g.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
}

export default function Sphere({ data = {}, pulse = 0, label }) {
  const wrap = useRef(null);
  const mount = useRef(null);
  const tags = useRef(null);
  const live = useRef({ data, kick: 0 });
  const [legend, setLegend] = useState(false);

  useEffect(() => { live.current.data = data; }, [data]);
  useEffect(() => { live.current.kick = 1; }, [pulse]);

  useEffect(() => {
    const el = mount.current;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
    camera.position.set(0, 0.45, 5);
    const world = new THREE.Group();
    scene.add(world);
    const glow = glowTexture();
    let blend = readSkin();
    const glowing = [];  // materials that glow on dark skins and turn to ink on paper
    const mark = (m) => { m.blending = blend; glowing.push(m); return m; };

    // surface
    const geo = new THREE.IcosahedronGeometry(1, 24);
    const base = geo.attributes.position.array.slice();
    const pointsMat = new THREE.PointsMaterial({ size: 0.02, color: CYAN, transparent: true, opacity: 0.85, depthWrite: false });
    world.add(new THREE.Points(geo, pointsMat));

    // inner cage: Bitcoin filter
    const cageMat = new THREE.MeshBasicMaterial({ color: GREEN, wireframe: true, transparent: true, opacity: 0.4 });
    const onSkin = () => {
      blend = readSkin();
      for (const m of glowing) { m.blending = blend; m.needsUpdate = true; }
      haloUp.material.color.copy(GREEN); haloDown.material.color.copy(RED);
      cashArc.material.color.copy(CYAN); coinArc.material.color.copy(MAGENTA);
      sparkMat.color.copy(RED);
    };
    window.addEventListener("tb-skin", onSkin);
    const cage = new THREE.Mesh(new THREE.IcosahedronGeometry(0.62, 2), cageMat);
    world.add(cage);

    // halo (breadth) and outer ring (cash vs coins): arcs rebuilt when the share changes
    const arc = (r1, r2, color, opacity) => {
      const m = new THREE.Mesh(new THREE.RingGeometry(r1, r2, 128, 1, 0, Math.PI * 2),
        mark(new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, transparent: true, opacity, depthWrite: false })));
      return m;
    };
    const halo = new THREE.Group(), outer = new THREE.Group();
    const haloUp = arc(1.42, 1.46, GREEN, 0.55), haloDown = arc(1.42, 1.46, RED, 0.55);
    const cashArc = arc(1.62, 1.635, CYAN, 0.5), coinArc = arc(1.62, 1.635, MAGENTA, 0.5);
    halo.add(haloUp, haloDown);
    outer.add(cashArc, coinArc);
    halo.rotation.x = Math.PI / 2.3;
    outer.rotation.x = Math.PI / 2.6;
    outer.rotation.y = 0.25;
    scene.add(halo, outer);
    let lastBreadth = -1, lastCash = -1;
    const setArc = (a, b, share) => {
      const s = Math.max(0.001, Math.min(0.999, share)) * Math.PI * 2;
      a.geometry.dispose(); b.geometry.dispose();
      const p = a.geometry.parameters ?? {};
      a.geometry = new THREE.RingGeometry(p.innerRadius, p.outerRadius, 128, 1, 0, s);
      b.geometry = new THREE.RingGeometry(p.innerRadius, p.outerRadius, 128, 1, s, Math.PI * 2 - s);
    };

    // spikes, one per coin, at a fixed spot on the sphere
    const spots = fib(48);
    const used = new Map();
    const spikes = new Map();
    const spotFor = (sym) => {
      if (used.has(sym)) return used.get(sym);
      let h = 0;
      for (const ch of sym) h = (h * 31 + ch.charCodeAt(0)) % spots.length;
      const taken = new Set(used.values());
      while (taken.has(h)) h = (h + 7) % spots.length;
      used.set(sym, h);
      return h;
    };
    const makeSpike = (sym) => {
      const dir = spots[spotFor(sym)].clone();
      const mat = mark(new THREE.MeshBasicMaterial({ color: CYAN, transparent: true, opacity: 0.85, depthWrite: false }));
      const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.006, 0.018, 1, 6, 1, true), mat);
      shaft.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
      const tip = new THREE.Sprite(mark(new THREE.SpriteMaterial({ map: glow, color: CYAN, transparent: true, depthWrite: false })));
      world.add(shaft, tip);
      const tag = document.createElement("div");
      tag.className = "core-tag";
      tags.current?.appendChild(tag);
      const s = { dir, shaft, tip, mat, tag, len: 0, target: 0, color: CYAN.clone(), seen: true };
      spikes.set(sym, s);
      return s;
    };

    // Guardian sparks
    const sparkGeo = new THREE.BufferGeometry();
    const sparkPos = new Float32Array(60 * 3);
    sparkGeo.setAttribute("position", new THREE.BufferAttribute(sparkPos, 3));
    const sparkMat = mark(new THREE.PointsMaterial({ size: 0.05, color: RED, map: glow, transparent: true, opacity: 0, depthWrite: false }));
    world.add(new THREE.Points(sparkGeo, sparkMat));

    // shockwave
    const shockMat = new THREE.MeshBasicMaterial({ color: INK, side: THREE.DoubleSide, transparent: true, opacity: 0 });
    const shock = new THREE.Mesh(new THREE.RingGeometry(0.98, 1.0, 128), shockMat);
    scene.add(shock);

    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.position.z = w / h < 1 ? 6.2 : 5;
      camera.updateProjectionMatrix();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(el);

    // drag to turn it
    let drag = null, spinX = 0, spinY = 0;
    const down = (e) => { drag = [e.clientX, e.clientY]; el.setPointerCapture?.(e.pointerId); };
    const move = (e) => {
      if (!drag) return;
      spinY += (e.clientX - drag[0]) * 0.006;
      spinX += (e.clientY - drag[1]) * 0.006;
      drag = [e.clientX, e.clientY];
    };
    const up = () => { drag = null; };
    el.addEventListener("pointerdown", down);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);

    let raf, t = 0, shockT = 1;
    const pos = geo.attributes.position;
    const v = new THREE.Vector3();
    const loop = () => {
      t += 0.016;
      const d = live.current.data || {};
      const mood = Math.max(-1, Math.min(1, (d.mood ?? 0) / 4));
      const energy = Math.min(1, Math.abs(d.market ?? 0) / 5 + (d.energy ?? 0.2));
      if (live.current.kick > 0) {
        shockT = 0;
        live.current.kick = 0;
        shockMat.color.copy(d.lastSide === "SELL" ? RED : d.lastSide === "BUY" ? GREEN : CYAN);
      }
      shockT = Math.min(1, shockT + 0.015);

      // surface: wobble = market movement, beat faster when things happen
      const amp = 0.03 + energy * 0.1 + (1 - shockT) * 0.18;
      const beat = 1 + Math.sin(t * (1.6 + energy * 2)) * 0.02 * (1 + energy * 2);
      for (let i = 0; i < pos.count; i++) {
        const x = base[i * 3], y = base[i * 3 + 1], z = base[i * 3 + 2];
        const n = Math.sin(x * 4 + t * 1.7) * Math.cos(y * 3.3 - t * 1.3) * Math.sin(z * 5 + t);
        const r = beat * (1 + n * amp);
        pos.setXYZ(i, x * r, y * r, z * r);
      }
      pos.needsUpdate = true;
      const target = mood >= 0 ? CYAN.clone().lerp(GREEN, Math.min(1, mood * 1.5)) : CYAN.clone().lerp(RED, Math.min(1, -mood * 1.5));
      pointsMat.color.lerp(target, 0.04);
      pointsMat.size = 0.016 + Math.abs(mood) * 0.008;

      // Bitcoin filter cage
      cageMat.color.lerp(d.btcOk == null ? DIM : d.btcOk ? GREEN : RED, 0.05);
      cage.rotation.y -= 0.003 + Math.min(0.02, Math.abs(d.btcGap ?? 0) / 600);
      cage.rotation.x += 0.0015;
      cageMat.opacity = 0.25 + 0.15 * Math.sin(t * 2);

      // arcs
      if (d.breadth != null && Math.abs(d.breadth - lastBreadth) > 0.01) { setArc(haloUp, haloDown, d.breadth); lastBreadth = d.breadth; }
      if (d.cashShare != null && Math.abs(d.cashShare - lastCash) > 0.01) { setArc(cashArc, coinArc, d.cashShare); lastCash = d.cashShare; }
      halo.rotation.z += 0.002 + energy * 0.004;
      outer.rotation.z -= 0.0012;

      // spikes
      for (const s of spikes.values()) s.seen = false;
      for (const c of d.spikes || []) {
        const s = spikes.get(c.symbol) || makeSpike(c.symbol);
        s.seen = true;
        s.target = c.len;
        s.color.copy(c.kind === "held" ? (c.pct >= 0 ? GREEN : RED) : c.state === "would buy" ? GREEN : c.state === "would sell" ? RED
          : c.state === "near breakout" || c.state === "breakout, no slot" ? AMBER : DIM);
        s.tag.textContent = c.text;
        s.tag.dataset.kind = c.kind;
        s.flash = c.kind !== "held" && c.state === "would buy";
      }
      for (const [sym, s] of spikes) {
        if (!s.seen) {
          s.target = 0;
          if (s.len < 0.01) {
            world.remove(s.shaft, s.tip);
            s.shaft.geometry.dispose(); s.mat.dispose(); s.tip.material.dispose();
            s.tag.remove();
            spikes.delete(sym);
            used.delete(sym);
            continue;
          }
        }
        s.len += (s.target - s.len) * 0.05;
        const wob = 1 + Math.sin(t * 3 + s.dir.x * 9) * 0.04 * (1 + energy);
        const L = Math.max(0.001, s.len * wob);
        s.shaft.scale.set(1, L, 1);
        s.shaft.position.copy(s.dir).multiplyScalar(1 + L / 2);
        s.tip.position.copy(s.dir).multiplyScalar(1 + L);
        const pulseOn = s.flash ? 0.6 + 0.4 * Math.sin(t * 6) : 1;
        s.tip.scale.setScalar((0.12 + L * 0.12) * pulseOn);
        s.mat.color.lerp(s.color, 0.08);
        s.tip.material.color.copy(s.mat.color);
      }

      // Guardian sparks
      const g = d.guard || 0;
      sparkMat.opacity = g ? 0.5 + 0.5 * Math.random() : Math.max(0, sparkMat.opacity - 0.02);
      if (g) {
        for (let i = 0; i < 60; i++) {
          if (Math.random() < 0.15) {
            v.set(Math.random() - 0.5, Math.random() - 0.5, Math.random() - 0.5).normalize().multiplyScalar(1.05 + Math.random() * 0.25);
            sparkPos[i * 3] = v.x; sparkPos[i * 3 + 1] = v.y; sparkPos[i * 3 + 2] = v.z;
          }
        }
        sparkGeo.attributes.position.needsUpdate = true;
      }

      // turn: slow auto spin plus your drag
      world.rotation.y += 0.0025 + energy * 0.003 + spinY;
      world.rotation.x = Math.max(-1.2, Math.min(1.2, world.rotation.x + spinX));
      spinX *= 0.9; spinY *= 0.9;

      shock.scale.setScalar(1 + shockT * 1.8);
      shockMat.opacity = (1 - shockT) * 0.85;
      renderer.render(scene, camera);

      // labels follow the spike tips; hidden behind the sphere
      const w = el.clientWidth, h = el.clientHeight;
      world.updateMatrixWorld();
      for (const s of spikes.values()) {
        v.copy(s.tip.position).applyMatrix4(world.matrixWorld);
        const behind = v.z < -0.2;
        v.project(camera);
        s.tag.style.transform = `translate(${((v.x + 1) / 2) * w}px, ${((1 - v.y) / 2) * h}px) translate(-50%, -130%)`;
        s.tag.style.opacity = behind || s.len < 0.05 ? 0 : 1;
        s.tag.style.color = `#${s.mat.color.getHexString()}`;
      }
      raf = requestAnimationFrame(loop);
    };
    loop();
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      el.removeEventListener("pointerdown", down);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("tb-skin", onSkin);
      for (const s of spikes.values()) s.tag.remove();
      renderer.dispose();
      el.removeChild(renderer.domElement);
    };
  }, []);

  const full = () => {
    const box = wrap.current?.closest(".core") || wrap.current;
    if (document.fullscreenElement) document.exitFullscreen?.();
    else box?.requestFullscreen?.().catch(() => {});
  };

  return (
    <div className="sphere" ref={wrap}>
      <div className="sphere-gl" ref={mount} />
      <div className="core-tags" ref={tags} />
      <div className="core-vignette" />
      {label}
      {data.hud && ["left", "right"].map((side) => (
        <div key={side} className={`core-hud ${side}`}>
          {data.hud[side].map(([k, v, tone]) => <div key={k}><span>{k}</span><b className={tone}>{v}</b></div>)}
        </div>
      ))}
      <div className="core-tools">
        <button className="mini" onClick={() => setLegend((x) => !x)}>{legend ? "hide layers" : "what am I seeing?"}</button>
        <button className="mini" onClick={full} title="fullscreen">⛶</button>
      </div>
      {legend && (
        <div className="core-legend">
          {LAYERS.map(([c, name, what]) => <div key={name}><i style={{ background: c }} /><b>{name}</b> {what}</div>)}
          {data.facts && <div className="dim small" style={{ marginTop: 6 }}>{data.facts}</div>}
        </div>
      )}
    </div>
  );
}
