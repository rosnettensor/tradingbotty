import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";
import { applyPalette, currentPalette } from "./skins.js";

applyPalette(currentPalette());

createRoot(document.getElementById("root")).render(<App />);
