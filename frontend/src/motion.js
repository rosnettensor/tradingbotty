import { useEffect, useState } from "react";

const KEY = "tb-motion";
const media = () => window.matchMedia("(prefers-reduced-motion: reduce)");
export function motionPreference() {
  let choice = "auto";
  try { choice = localStorage.getItem(KEY) || "auto"; } catch { /* browser-only preference */ }
  return { choice, reduced: choice === "calm" || (choice !== "full" && media().matches) };
}
export function useMotion() {
  const [value, setValue] = useState(motionPreference);
  useEffect(() => {
    const sync = () => { const next = motionPreference(); document.documentElement.dataset.motion = next.reduced ? "reduced" : "full"; setValue(next); };
    const query = media();
    sync(); query.addEventListener("change", sync);
    window.addEventListener("tb-motion", sync); window.addEventListener("storage", sync);
    return () => { query.removeEventListener("change", sync); window.removeEventListener("tb-motion", sync); window.removeEventListener("storage", sync); };
  }, []);
  return value;
}
export function setMotion(choice) {
  try { localStorage.setItem(KEY, choice); } catch { /* private mode */ }
  window.dispatchEvent(new Event("tb-motion"));
}

/** Low-frequency clock for truthful ages/countdowns, including after a hidden tab resumes. */
export function useNow() {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const tick = () => { if (!document.hidden) setNow(Date.now() / 1000); };
    const timer = setInterval(tick, 1000);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, []);
  return now;
}
