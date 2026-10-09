// The dashboard has one design, Aurora, in several palettes. The choice is remembered in this browser only.
// [key, name, three swatch colors, what it is]
export const PALETTES = [
  ["aurora", "Aurora", ["#5ee7ff", "#b49bff", "#ff7ad9"], "Nordlicht, färbt sich grün oder rot mit dem Konto"],
  ["mono", "Schwarz-Weiss", ["#ffffff", "#55555e", "#0d0d10"], "nur Grautöne, Gewinne grün, Verluste rot"],
  ["auto", "Auto", ["#0d0d10", "#ffffff", "#0d0d10"], "folgt dem Handy oder Mac: tagsüber Weiss-Schwarz, nachts Schwarz-Weiss"],
  ["paper", "Weiss-Schwarz", ["#0d0d10", "#9a9aa2", "#f4f4f1"], "hell wie Papier, schwarze Tinte"],
  ["borealis", "Borealis", ["#6dffc8", "#63d6ff", "#c6ff6b"], "grün und türkis"],
  ["ocean", "Ozean", ["#5cb9ff", "#6bffe6", "#25399e"], "tiefblau"],
  ["nebula", "Nebula", ["#c79eff", "#ff6bd6", "#3529a5"], "violett und pink"],
  ["ember", "Glut", ["#ffb36b", "#ff6b9c", "#c7811b"], "orange und rot"],
];

const BAR = { aurora: "#0b1020", mono: "#08080a", paper: "#f4f4f1", borealis: "#05130f", ocean: "#040b19", nebula: "#0c0717", ember: "#13090b" };

export function currentPalette() {
  try {
    const p = localStorage.getItem("tb-palette");
    return PALETTES.some(([k]) => k === p) ? p : "mono";
  } catch { return "mono"; }
}

const dark = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
let chosen = null;

export function applyPalette(p) {
  chosen = p;
  const real = p === "auto" ? (dark && !dark.matches ? "paper" : "mono") : p;  // Auto: light or dark like the device
  const root = document.documentElement;
  root.dataset.skin = "aurora";
  root.dataset.palette = real;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", BAR[real] || BAR.aurora);
  try { localStorage.setItem("tb-palette", p); } catch { /* private mode */ }
  window.dispatchEvent(new CustomEvent("tb-skin", { detail: real }));  // the 3D parts re-read the colors
}
dark?.addEventListener?.("change", () => { if (chosen === "auto") applyPalette("auto"); });

// T flips between the two black-and-white looks (and leaves Auto)
export function flipInk() {
  applyPalette(document.documentElement.dataset.palette === "paper" ? "mono" : "paper");
}
