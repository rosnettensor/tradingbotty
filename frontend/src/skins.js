// The dashboard has one design, Aurora, in several palettes. The choice is remembered in this browser only.
// [key, name, three swatch colors, what it is]
export const PALETTES = [
  ["aurora", "Aurora", ["#5ee7ff", "#b49bff", "#ff7ad9"], "Nordlicht, färbt sich grün oder rot mit dem Konto"],
  ["mono", "Schwarz-Weiss", ["#ffffff", "#55555e", "#0d0d10"], "nur Grautöne, Gewinne grün, Verluste rot"],
  ["borealis", "Borealis", ["#6dffc8", "#63d6ff", "#c6ff6b"], "grün und türkis"],
  ["ocean", "Ozean", ["#5cb9ff", "#6bffe6", "#25399e"], "tiefblau"],
  ["nebula", "Nebula", ["#c79eff", "#ff6bd6", "#3529a5"], "violett und pink"],
  ["ember", "Glut", ["#ffb36b", "#ff6b9c", "#c7811b"], "orange und rot"],
];

const BAR = { aurora: "#0b1020", mono: "#08080a", borealis: "#05130f", ocean: "#040b19", nebula: "#0c0717", ember: "#13090b" };

export function currentPalette() {
  try {
    const p = localStorage.getItem("tb-palette");
    return PALETTES.some(([k]) => k === p) ? p : "aurora";
  } catch { return "aurora"; }
}

export function applyPalette(p) {
  const root = document.documentElement;
  root.dataset.skin = "aurora";
  root.dataset.palette = p;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", BAR[p] || BAR.aurora);
  try { localStorage.setItem("tb-palette", p); } catch { /* private mode */ }
  window.dispatchEvent(new CustomEvent("tb-skin", { detail: p }));  // the 3D parts re-read the colors
}
