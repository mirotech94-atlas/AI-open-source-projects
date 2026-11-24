import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import "./hud.css";
import "./academy.css";
import "./command.css";
import "./desk.css";
import "./motion.css";
import { applyTheme, readTheme } from "./theme";

// Before the first paint. Dark only (brand: black + royal blue).
applyTheme(readTheme());

const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
