// Dashboard looks. The choice is remembered in this browser only.
export const SKINS = [
  ["neon", "Neon"],
  ["minimal", "Minimal"],
  ["terminal", "Terminal"],
  ["bridge", "Bridge"],
];

export function currentSkin() {
  try { const s = localStorage.getItem("tb-skin"); return SKINS.some(([k]) => k === s) ? s : "neon"; } catch { return "neon"; }
}

export function applySkin(skin) {
  if (skin === "neon") delete document.documentElement.dataset.skin;
  else document.documentElement.dataset.skin = skin;
  try { localStorage.setItem("tb-skin", skin); } catch { /* private mode */ }
  window.dispatchEvent(new CustomEvent("tb-skin", { detail: skin }));
}
