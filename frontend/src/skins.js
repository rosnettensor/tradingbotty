// Dashboard looks. The choice is remembered in this browser only.
export const SKINS = [
  ["neon", "Neon"],
  ["minimal", "Minimal"],
  ["terminal", "Terminal"],
  ["bridge", "Bridge"],
  ["aurora", "Aurora"],
];

export function currentSkin() {
  try { const s = localStorage.getItem("tb-skin"); return SKINS.some(([k]) => k === s) ? s : "neon"; } catch { return "neon"; }
}

const BAR = { neon: "#05060a", minimal: "#ffffff", terminal: "#000000", bridge: "#000000", aurora: "#0b1020" };

export function applySkin(skin) {
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", BAR[skin] || BAR.neon);
  if (skin === "neon") delete document.documentElement.dataset.skin;
  else document.documentElement.dataset.skin = skin;
  try { localStorage.setItem("tb-skin", skin); } catch { /* private mode */ }
  window.dispatchEvent(new CustomEvent("tb-skin", { detail: skin }));
}

/** The current skin, updated when you switch it (for parts that can't follow CSS alone). */
export function useSkin(useState, useEffect) {
  const [skin, setSkin] = useState(currentSkin);
  useEffect(() => {
    const on = (e) => setSkin(e.detail);
    window.addEventListener("tb-skin", on);
    return () => window.removeEventListener("tb-skin", on);
  }, []);
  return skin;
}
