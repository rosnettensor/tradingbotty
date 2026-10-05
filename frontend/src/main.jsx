import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";
import { applySkin, currentSkin } from "./skins.js";

applySkin(currentSkin());

createRoot(document.getElementById("root")).render(<App />);
