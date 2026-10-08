import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import "./hud.css";
import "./academy.css";
import "./command.css";
import { applyTheme, readTheme } from "./theme";

// Before the first paint: a saved light/dark choice, else the device's.
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
