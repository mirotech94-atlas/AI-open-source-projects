import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import { ToastProvider } from "./design";
import "./styles/fonts.css";
import "./styles/tokens.css";
import "./styles/glass.css";

// Registered for installability, not for offline caching: the worker is
// network-only and `web/public/sw.js` explains at length why a watchdog must
// not serve a cached "all clear". Failure to register is deliberately silent
// — an unsupported or blocked worker costs the user nothing, and an error
// toast about it on a dashboard someone opened in an emergency would be
// noise at the worst possible moment.
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </StrictMode>,
);
