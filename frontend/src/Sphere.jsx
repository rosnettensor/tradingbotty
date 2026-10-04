import { useEffect, useRef } from "react";
import * as THREE from "three";

// The pulsing core. Color follows profit (green/red), wobble follows market energy,
// and every trade sends a shockwave through it.
export default function Sphere({ mood = 0, energy = 0.3, pulse = 0, label }) {
  const mount = useRef(null);
  const live = useRef({ mood, energy, kick: 0 });

  useEffect(() => {
    live.current.mood = mood;
    live.current.energy = energy;
  }, [mood, energy]);

  useEffect(() => {
    live.current.kick = 1;
  }, [pulse]);

  useEffect(() => {
    const el = mount.current;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
    camera.position.z = 4.2;

    const geo = new THREE.IcosahedronGeometry(1, 24);
    const base = geo.attributes.position.array.slice();
    const pointsMat = new THREE.PointsMaterial({ size: 0.018, color: 0x00f0ff, transparent: true, opacity: 0.9 });
    const points = new THREE.Points(geo, pointsMat);
    scene.add(points);

    const wire = new THREE.Mesh(
      new THREE.IcosahedronGeometry(0.72, 2),
      new THREE.MeshBasicMaterial({ color: 0xff2bd6, wireframe: true, transparent: true, opacity: 0.35 }),
    );
    scene.add(wire);

    const ringGeo = new THREE.RingGeometry(1.45, 1.47, 128);
    const ringMat = new THREE.MeshBasicMaterial({ color: 0x00f0ff, side: THREE.DoubleSide, transparent: true, opacity: 0.4 });
    const ring = new THREE.Mesh(ringGeo, ringMat);
    ring.rotation.x = Math.PI / 2.3;
    scene.add(ring);
    const shock = new THREE.Mesh(new THREE.RingGeometry(0.98, 1.0, 128),
      new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide, transparent: true, opacity: 0 }));
    scene.add(shock);

    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(el);

    const green = new THREE.Color(0x39ff88), red = new THREE.Color(0xff3b5c), cyan = new THREE.Color(0x00f0ff);
    let raf, t = 0, shockT = 1;
    const pos = geo.attributes.position;
    const loop = () => {
      t += 0.016;
      const { mood, energy } = live.current;
      if (live.current.kick > 0) {
        shockT = 0;
        live.current.kick = 0;
      }
      shockT = Math.min(1, shockT + 0.02);
      const amp = 0.04 + energy * 0.12 + (1 - shockT) * 0.15;
      const beat = 1 + Math.sin(t * 2.2) * 0.025 * (1 + energy * 2);
      for (let i = 0; i < pos.count; i++) {
        const x = base[i * 3], y = base[i * 3 + 1], z = base[i * 3 + 2];
        const n = Math.sin(x * 4 + t * 1.7) * Math.cos(y * 3.3 - t * 1.3) * Math.sin(z * 5 + t);
        const r = beat * (1 + n * amp);
        pos.setXYZ(i, x * r, y * r, z * r);
      }
      pos.needsUpdate = true;
      const target = mood > 0 ? cyan.clone().lerp(green, Math.min(1, mood)) : cyan.clone().lerp(red, Math.min(1, -mood));
      pointsMat.color.lerp(target, 0.05);
      ringMat.color.copy(pointsMat.color);
      points.rotation.y += 0.002 + energy * 0.004;
      wire.rotation.y -= 0.004;
      wire.rotation.x += 0.002;
      ring.rotation.z += 0.003;
      shock.scale.setScalar(1 + shockT * 1.6);
      shock.material.opacity = (1 - shockT) * 0.8;
      renderer.render(scene, camera);
      raf = requestAnimationFrame(loop);
    };
    loop();
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      renderer.dispose();
      el.removeChild(renderer.domElement);
    };
  }, []);

  return (
    <div className="sphere" ref={mount}>
      {label}
    </div>
  );
}
